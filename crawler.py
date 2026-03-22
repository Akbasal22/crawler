"""
Crawler: Multi-crawler architecture with one manager per origin.
Uses urllib + html.parser only (no BeautifulSoup / Scrapy).
Restricted to English Wikipedia (en.wikipedia.org) only.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

from db import (
    clear_all_pending,
    count_pending,
    get_meta,
    insert_page,
    mark_queue_failed,
    page_exists,
    pending_urls,
    set_meta,
    try_enqueue_pending,
    update_queue_status,
)

# Set up logging
logger = logging.getLogger(__name__)

NUM_WORKERS = 3  # Per crawler instance
# Overall cap: ~2 pages/sec per crawler instance
MIN_FETCH_INTERVAL_SEC = 0.5
# In-memory work queue bound — blocking put = back pressure for producers
MAX_IN_MEMORY_QUEUE = 300  # Per crawler instance
# Short timeout to prevent workers from hanging on slow sites
DEFAULT_REQUEST_TIMEOUT_SEC = 5


def _is_fetchable_http_url(candidate: str) -> bool:
    """Only enqueue absolute http(s) URLs that belong to English Wikipedia articles."""
    try:
        c = urlparse(candidate)
        if c.scheme not in ("http", "https") or not c.netloc:
            return False
        
        # Only allow en.wikipedia.org domain (strict check)
        netloc_lower = c.netloc.lower()
        if not (netloc_lower == "en.wikipedia.org" or netloc_lower.endswith(".en.wikipedia.org")):
            return False
        
        # Only allow article pages (/wiki/Article_Name)
        # Reject special pages that contain ":" after /wiki/
        # Examples to reject: Wikipedia:, Help:, Category:, Talk:, User:, Template:, Special:, File:
        path = c.path
        if not path.startswith("/wiki/"):
            return False
        
        # Extract the part after /wiki/
        article_part = path[6:]  # Remove "/wiki/" prefix
        
        # Reject if contains ":" (special/meta pages)
        if ":" in article_part:
            return False
        
        # Reject empty article name
        if not article_part:
            return False
        
        return True
    except Exception:
        return False


class PageContextParser(HTMLParser):
    """Extract <title> text and <a href> links (resolved against base URL)."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base = base_url
        self.title_parts: list[str] = []
        self.links: list[str] = []
        self._in_title = False
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = True
            self._title_depth += 1
            return
        if t != "a":
            return
        href: str | None = None
        for name, value in attrs:
            if name.lower() == "href" and value:
                href = value.strip()
                break
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return
        try:
            abs_url = urljoin(self._base, href)
            abs_url, _frag = urldefrag(abs_url)
            if _is_fetchable_http_url(abs_url):
                self.links.append(abs_url)
        except Exception as e:
            logger.debug(f"Failed to parse link {href}: {e}")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title" and self._in_title:
            self._title_depth -= 1
            if self._title_depth <= 0:
                self._in_title = False
                self._title_depth = 0

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)

    def parsed_title(self) -> str | None:
        t = "".join(self.title_parts).strip()
        return t or None


def _decode_body(resp: Any, raw: bytes) -> str:
    charset = "utf-8"
    try:
        ctype = resp.headers.get_content_charset()
        if ctype:
            charset = ctype
    except Exception:
        pass
    return raw.decode(charset, errors="replace")


def fetch_html(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "LocalWebCrawler/1.0 (+edu homework; respectful crawl)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    with urlopen(req, timeout=DEFAULT_REQUEST_TIMEOUT_SEC) as resp:
        raw = resp.read()
    return _decode_body(resp, raw)


WorkItem = tuple[str, str, int]  # url, job_origin, depth


class CrawlManager:
    """
    Coordinates worker threads for a single origin URL.
    
    Each CrawlManager instance manages its own:
    - Worker thread pool
    - Rate limiter
    - In-memory queue
    - Session statistics
    
    This allows multiple crawlers to run concurrently, one per origin.
    """

    def __init__(self, origin: str, max_depth: int) -> None:
        self.origin = origin
        self._work_queue: queue.Queue[WorkItem] = queue.Queue(maxsize=MAX_IN_MEMORY_QUEUE)
        self._rate_lock = threading.Lock()
        self._last_fetch_at = 0.0
        self._state_lock = threading.Lock()
        # Track which URLs are currently in RAM (or being processed).
        self._in_memory_urls: set[str] = set()
        self._in_memory_lock = threading.Lock()
        self._max_depth = max_depth
        self._pages_fetched = 0
        self._running = False
        self._workers: list[threading.Thread] = []
        self._drainer_thread: threading.Thread | None = None

    def _throttle(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            wait = MIN_FETCH_INTERVAL_SEC - (now - self._last_fetch_at)
            if wait > 0:
                time.sleep(wait)
            self._last_fetch_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            max_d = self._max_depth
            job_o = self.origin
            fetched = self._pages_fetched
            running = self._running
        qsize = self._work_queue.qsize()
        cap = MAX_IN_MEMORY_QUEUE
        full = self._work_queue.full()
        if full:
            bp = "Paused: in-memory queue full (back pressure)"
        else:
            bp = f"Crawling up to {1 / MIN_FETCH_INTERVAL_SEC:.0f} pages/sec"

        return {
            "running": running,
            "max_depth": max_d,
            "job_origin": job_o,
            "pending_in_db": count_pending(job_o),
            "memory_queue_size": qsize,
            "memory_queue_capacity": cap,
            "memory_queue_full": full,
            "back_pressure": bp,
            "session_pages_fetched": fetched,
            "workers": len(self._workers),
        }

    def bump_session_pages(self) -> None:
        with self._state_lock:
            self._pages_fetched += 1

    def enqueue_url(self, url: str, job_origin: str, depth: int) -> bool:
        """
        Enqueue a URL for crawling.
        Returns True if successfully enqueued, False otherwise.
        """
        # Check if already in memory
        with self._in_memory_lock:
            if url in self._in_memory_urls:
                return False
        
        # Try to add to database queue
        if not try_enqueue_pending(url, job_origin, depth):
            return False
        
        # Try to add to in-memory queue (non-blocking)
        try:
            self._work_queue.put_nowait((url, job_origin, depth))
            with self._in_memory_lock:
                self._in_memory_urls.add(url)
            return True
        except queue.Full:
            # Queue is full - URL is in DB queue but not in memory yet
            # The drainer will pick it up later
            logger.debug(f"Work queue full, {url} added to DB queue only")
            return True

    def _worker_loop(self) -> None:
        logger.info(f"Worker {threading.current_thread().name} started for origin {self.origin}")
        while self._running:
            item = None
            try:
                url, job_origin, depth = self._work_queue.get(timeout=0.5)
                item = (url, job_origin, depth)
            except queue.Empty:
                continue
            
            # Process the URL with comprehensive error handling
            try:
                self._process_url(url, job_origin, depth)
            except HTTPError as e:
                logger.error(f"HTTP error on {url}: {e.code} {e.reason}")
                try:
                    mark_queue_failed(url)
                except Exception as db_err:
                    logger.error(f"Failed to mark {url} as failed in DB: {db_err}")
            except URLError as e:
                logger.error(f"URL error on {url}: {e.reason}")
                try:
                    mark_queue_failed(url)
                except Exception as db_err:
                    logger.error(f"Failed to mark {url} as failed in DB: {db_err}")
            except TimeoutError as e:
                logger.error(f"Timeout on {url}: {e}")
                try:
                    mark_queue_failed(url)
                except Exception as db_err:
                    logger.error(f"Failed to mark {url} as failed in DB: {db_err}")
            except Exception as e:
                logger.error(f"Unexpected error processing {url}: {e}", exc_info=True)
                try:
                    mark_queue_failed(url)
                except Exception as db_err:
                    logger.error(f"Failed to mark {url} as failed in DB: {db_err}")
            finally:
                # CRITICAL: Always mark task as done
                if item is not None:
                    with self._in_memory_lock:
                        self._in_memory_urls.discard(item[0])
                    try:
                        self._work_queue.task_done()
                    except Exception as e:
                        logger.error(f"Error calling task_done(): {e}")
        
        logger.info(f"Worker {threading.current_thread().name} stopped")

    def _process_url(self, url: str, job_origin: str, depth: int) -> None:
        """Process a single URL. Raises exceptions on failure."""
        # Skip if already indexed
        if page_exists(url):
            update_queue_status(url, "visited")
            return

        # Rate limiting
        self._throttle()
        
        # Fetch HTML
        logger.debug(f"Fetching {url}")
        html = fetch_html(url)

        # Parse HTML
        parser = PageContextParser(url)
        try:
            parser.feed(html)
            parser.close()
        except Exception as e:
            logger.warning(f"HTML parsing error for {url}: {e}")

        # Save to database
        title = parser.parsed_title()
        insert_page(url, job_origin, depth, title, html)
        
        self.bump_session_pages()

        # Check depth limit
        with self._state_lock:
            max_d = self._max_depth
        if depth >= max_d:
            return

        # Enqueue discovered links
        link_count = 0
        for link in parser.links:
            if not self._running:
                break
            if self.enqueue_url(link, job_origin, depth + 1):
                link_count += 1
        
        if link_count > 0:
            logger.debug(f"Discovered {link_count} new links from {url}")

    def _drain_pending_to_memory_loop(self) -> None:
        """Continuously move DB pending rows (for this origin) into the bounded RAM queue."""
        batch_size = 50
        logger.info(f"Starting drainer for origin {self.origin}")
        while self._running:
            if self._work_queue.full():
                time.sleep(0.2)
                continue

            remaining = max(1, self._work_queue.maxsize - self._work_queue.qsize())
            # Only get pending URLs for this origin
            batch = pending_urls(min(batch_size, remaining), origin_url=self.origin)
            if not batch:
                time.sleep(0.5)
                continue

            for item in batch:
                if not self._running:
                    return
                if self._work_queue.full():
                    break

                url = item[0]
                with self._in_memory_lock:
                    if url in self._in_memory_urls:
                        continue

                try:
                    self._work_queue.put_nowait(item)
                    with self._in_memory_lock:
                        self._in_memory_urls.add(url)
                except queue.Full:
                    break
        
        logger.info(f"Drainer for origin {self.origin} stopped")

    def start(self) -> None:
        """Start the crawler workers and drainer."""
        if self._running:
            logger.warning(f"Crawler for {self.origin} already running")
            return
        
        # Clear any pending URLs from this origin (fresh start)
        pending_count = clear_all_pending(self.origin)
        if pending_count > 0:
            logger.info(f"Cleared {pending_count} pending URLs for {self.origin}")
        
        # Clear the in-memory queue
        while not self._work_queue.empty():
            try:
                self._work_queue.get_nowait()
            except queue.Empty:
                break
        
        with self._in_memory_lock:
            self._in_memory_urls.clear()
        
        with self._state_lock:
            self._pages_fetched = 0
            self._running = True
        
        # Save settings to DB
        set_meta(f"max_depth:{self.origin}", str(self._max_depth))
        set_meta(f"job_origin:{self.origin}", self.origin)
        
        # Start workers
        self._workers = []
        logger.info(f"Starting {NUM_WORKERS} worker threads for {self.origin}")
        for i in range(NUM_WORKERS):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"crawler-{self.origin[:30]}-worker-{i}",
                daemon=True
            )
            t.start()
            self._workers.append(t)
        
        # Start drainer
        self._drainer_thread = threading.Thread(
            target=self._drain_pending_to_memory_loop,
            name=f"crawler-{self.origin[:30]}-drainer",
            daemon=True
        )
        self._drainer_thread.start()
        
        # Enqueue the origin URL
        logger.info(f"Starting crawl: origin={self.origin}, max_depth={self._max_depth}")
        self.enqueue_url(self.origin, self.origin, 0)

    def stop(self) -> None:
        """Stop the crawler gracefully."""
        logger.info(f"Stopping crawler for {self.origin}")
        with self._state_lock:
            self._running = False
        
        # Wait for workers to finish (with timeout)
        for worker in self._workers:
            worker.join(timeout=2.0)
        
        if self._drainer_thread:
            self._drainer_thread.join(timeout=2.0)
        
        logger.info(f"Crawler for {self.origin} stopped")

    def is_running(self) -> bool:
        with self._state_lock:
            return self._running


class CrawlerRegistry:
    """
    Registry for managing multiple concurrent crawlers.
    Each origin URL gets its own CrawlManager instance.
    """
    
    def __init__(self) -> None:
        self._crawlers: dict[str, CrawlManager] = {}
        self._lock = threading.Lock()
    
    def start_crawler(self, origin: str, max_depth: int) -> CrawlManager:
        """
        Start a new crawler for the given origin.
        If a crawler already exists for this origin, stop it and start a new one.
        """
        origin = origin.strip()
        if not origin:
            raise ValueError("origin URL is required")

        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("origin must be an absolute http(s) URL")
        
        # Validate that origin is from English Wikipedia
        netloc_lower = parsed.netloc.lower()
        if not (netloc_lower == "en.wikipedia.org" or netloc_lower.endswith(".en.wikipedia.org")):
            raise ValueError("origin must be from en.wikipedia.org (English Wikipedia only)")

        max_depth = max(0, int(max_depth))
        
        with self._lock:
            # Stop existing crawler if any
            if origin in self._crawlers:
                old_crawler = self._crawlers[origin]
                logger.info(f"Stopping existing crawler for {origin}")
                old_crawler.stop()
                del self._crawlers[origin]
            
            # Create and start new crawler
            crawler = CrawlManager(origin, max_depth)
            self._crawlers[origin] = crawler
        
        # Start outside the lock to avoid blocking
        crawler.start()
        return crawler
    
    def get_crawler(self, origin: str) -> CrawlManager | None:
        """Get the crawler for a specific origin, or None if not found."""
        with self._lock:
            return self._crawlers.get(origin)
    
    def get_all_crawlers(self) -> list[tuple[str, CrawlManager]]:
        """Get all active crawlers."""
        with self._lock:
            return list(self._crawlers.items())
    
    def stop_crawler(self, origin: str) -> bool:
        """Stop a specific crawler. Returns True if stopped, False if not found."""
        with self._lock:
            crawler = self._crawlers.get(origin)
            if crawler:
                del self._crawlers[origin]
        
        if crawler:
            crawler.stop()
            return True
        return False
    
    def stop_all(self) -> None:
        """Stop all crawlers."""
        with self._lock:
            crawlers = list(self._crawlers.values())
            self._crawlers.clear()
        
        for crawler in crawlers:
            crawler.stop()
    
    def snapshot_all(self) -> dict[str, Any]:
        """Get snapshot of all crawlers."""
        with self._lock:
            crawler_list = [(origin, crawler) for origin, crawler in self._crawlers.items()]
        
        from db import count_pages
        
        snapshots = []
        for origin, crawler in crawler_list:
            snapshot = crawler.snapshot()
            snapshot["origin"] = origin
            snapshots.append(snapshot)
        
        return {
            "total_crawlers": len(snapshots),
            "total_pages_indexed": count_pages(),
            "crawlers": snapshots,
        }


# Global registry
_registry = CrawlerRegistry()


def get_registry() -> CrawlerRegistry:
    """Get the global crawler registry."""
    return _registry


def get_manager() -> CrawlerRegistry:
    """
    Legacy compatibility function.
    Returns the registry, which now manages multiple crawlers.
    """
    return _registry
