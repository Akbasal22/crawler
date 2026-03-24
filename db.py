"""
SQLite access for the local web crawler.
Uses WAL mode so reads (search) can run concurrently with writes (indexer).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database file next to this package (portable for local runs)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "crawler.db"

# Storage directory for word index files (like p.data, a.data, etc.)
STORAGE_DIR = BASE_DIR / "data" / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    url TEXT PRIMARY KEY,
    origin_url TEXT NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'visited', 'failed'))
);

CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    origin_url TEXT NOT NULL,
    depth INTEGER NOT NULL,
    title TEXT,
    html_content TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_origin_status ON queue(origin_url, status);
CREATE INDEX IF NOT EXISTS idx_pages_origin ON pages(origin_url);
CREATE INDEX IF NOT EXISTS idx_pages_title ON pages(title);

-- Single-row settings for resumability (depth limit + last job origin)
CREATE TABLE IF NOT EXISTS crawl_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- FTS5 virtual table for fast full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    url UNINDEXED,
    title,
    content,
    content=pages,
    content_rowid=rowid
);

-- Triggers to keep FTS index in sync
CREATE TRIGGER IF NOT EXISTS pages_fts_insert AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, url, title, content)
    VALUES (new.rowid, new.url, new.title, new.html_content);
END;

CREATE TRIGGER IF NOT EXISTS pages_fts_delete AFTER DELETE ON pages BEGIN
    DELETE FROM pages_fts WHERE rowid = old.rowid;
END;

CREATE TRIGGER IF NOT EXISTS pages_fts_update AFTER UPDATE ON pages BEGIN
    DELETE FROM pages_fts WHERE rowid = old.rowid;
    INSERT INTO pages_fts(rowid, url, title, content)
    VALUES (new.rowid, new.url, new.title, new.html_content);
END;
"""


def get_connection() -> sqlite3.Connection:
    """
    Open a new connection with WAL and row factory set.
    
    Thread-safe: Each call creates a new connection, so each worker thread
    gets its own connection. WAL mode allows concurrent reads/writes.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        # Allow some concurrency for writes
        conn.execute("PRAGMA busy_timeout=5000;")  # Wait up to 5 seconds if database is locked
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise


def init_db() -> None:
    """Create tables and indexes if they do not exist."""
    try:
        conn = get_connection()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
            logger.info("Database initialized successfully")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def get_journal_mode() -> str:
    """Return current journal mode (should be 'wal' after first connection)."""
    try:
        conn = get_connection()
        try:
            row = conn.execute("PRAGMA journal_mode;").fetchone()
            return str(row[0]) if row else "unknown"
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to get journal mode: {e}")
        return "error"


def count_queue_by_status() -> dict[str, int]:
    """Count rows in queue grouped by status."""
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM queue GROUP BY status"
            ).fetchall()
            return {str(r["status"]): int(r["n"]) for r in rows}
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to count queue by status: {e}")
        return {}


def count_pages() -> int:
    try:
        conn = get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()
            return int(row["n"]) if row else 0
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to count pages: {e}")
        return 0


def db_health() -> dict[str, Any]:
    """Snapshot for dashboard / debugging."""
    return {
        "db_path": str(DB_PATH),
        "journal_mode": get_journal_mode(),
        "queue_by_status": count_queue_by_status(),
        "pages_count": count_pages(),
    }


def page_exists(url: str) -> bool:
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT 1 AS x FROM pages WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to check if page exists for {url}: {e}")
        return False


def try_enqueue_pending(url: str, origin_url: str, depth: int) -> bool:
    """
    Insert a new pending row if this URL is not already indexed and not already
    in the queue. Returns True if a new queue row was created.
    """
    if page_exists(url):
        return False
    try:
        conn = get_connection()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO queue (url, origin_url, depth, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (url, origin_url, depth),
            )
            conn.commit()
            inserted = cur.rowcount > 0
            if inserted:
                logger.debug(f"Enqueued: {url} (depth {depth})")
            return inserted
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to enqueue {url}: {e}")
        return False


def update_queue_status(url: str, status: str) -> None:
    if status not in ("pending", "visited", "failed"):
        raise ValueError("invalid queue status")
    try:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE queue SET status = ? WHERE url = ?",
                (status, url),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to update queue status for {url}: {e}")


def insert_page(
    url: str,
    origin_url: str,
    depth: int,
    title: str | None,
    html_content: str,
) -> None:
    try:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO pages (url, origin_url, depth, title, html_content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (url, origin_url, depth, title, html_content),
            )
            conn.execute(
                "UPDATE queue SET status = 'visited' WHERE url = ?",
                (url,),
            )
            conn.commit()
            logger.info(f"Indexed: {url} (depth {depth}) - {title or 'No title'}")
        finally:
            conn.close()
        
        # Create word index in storage files
        index_page_words(url, origin_url, depth, title, html_content)
        
    except Exception as e:
        logger.error(f"Failed to insert page {url}: {e}")
        raise


def pending_urls(limit: int, origin_url: str | None = None) -> list[tuple[str, str, int]]:
    """
    Return up to `limit` pending rows (url, origin_url, depth).
    If origin_url is provided, only return URLs from that origin.
    
    Uses rowid-based ordering to ensure fair distribution across crawlers:
    newer URLs get priority, preventing one crawler from monopolizing the queue.
    """
    try:
        conn = get_connection()
        try:
            if origin_url:
                # Filter by specific origin - CRITICAL: uses index for fast filtering
                # This prevents crawler A from being starved by crawler B's massive queue
                rows = conn.execute(
                    """
                    SELECT url, origin_url, depth FROM queue
                    WHERE status = 'pending' AND origin_url = ?
                    ORDER BY rowid ASC
                    LIMIT ?
                    """,
                    (origin_url, limit),
                ).fetchall()
            else:
                # Legacy behavior: get all pending URLs
                rows = conn.execute(
                    """
                    SELECT url, origin_url, depth FROM queue
                    WHERE status = 'pending'
                    ORDER BY rowid ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [(str(r["url"]), str(r["origin_url"]), int(r["depth"])) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to get pending URLs: {e}")
        return []


def count_pending(origin_url: str | None = None) -> int:
    """
    Count pending URLs. If origin_url is provided, only count for that origin.
    """
    try:
        conn = get_connection()
        try:
            if origin_url:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM queue WHERE status = 'pending' AND origin_url = ?",
                    (origin_url,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM queue WHERE status = 'pending'"
                ).fetchone()
            return int(row["n"]) if row else 0
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to count pending URLs: {e}")
        return 0


def search_pages(query: str, limit: int = 200) -> list[dict[str, Any]]:
    """
    Full-text search using word index files with relevance scoring.
    
    Returns results sorted by relevance score (highest first).
    
    Relevance score formula:
    score = (frequency × 10) + 1000 (if exact match) - (depth × 5)
    
    Falls back to FTS5 if word index files don't exist.
    """
    if not query:
        return []
    
    query = query.strip().lower()
    
    # Try to search using word index files first
    try:
        results = _search_from_word_index(query, limit)
        if results:
            return results
    except Exception as e:
        logger.debug(f"Word index search failed, falling back to FTS5: {e}")
    
    # Fallback to FTS5 search with frequency counting
    try:
        conn = get_connection()
        try:
            # Try FTS5 first (much faster for full-text search)
            try:
                # FTS5 syntax: use MATCH for full-text search
                # BM25 rank gives us relevance score (negative = better match)
                # We also count actual occurrences for frequency display
                rows = conn.execute(
                    """
                    SELECT 
                        p.url, 
                        p.origin_url, 
                        p.depth, 
                        p.title,
                        p.html_content,
                        bm25(pages_fts) as relevance_score
                    FROM pages_fts fts
                    JOIN pages p ON p.rowid = fts.rowid
                    WHERE pages_fts MATCH ?
                    ORDER BY relevance_score ASC
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
                
                # Now count actual frequency of the search term in each result
                results = []
                search_lower = query.lower()
                
                for r in rows:
                    # Count occurrences in title and content
                    title = str(r["title"]) if r["title"] else ""
                    content = str(r["html_content"]) if r["html_content"] else ""
                    
                    # Convert to lowercase for case-insensitive counting
                    title_lower = title.lower()
                    content_lower = content.lower()
                    
                    # Count occurrences
                    title_count = title_lower.count(search_lower)
                    content_count = content_lower.count(search_lower)
                    total_frequency = title_count + content_count
                    
                    # Calculate relevance score using the formula
                    # score = (frequency × 10) + 1000 (exact match bonus) - (depth × 5)
                    depth = int(r["depth"])
                    relevance_score = (total_frequency * 10) + 1000 - (depth * 5)
                    
                    results.append({
                        "relevant_url": str(r["url"]),
                        "origin_url": str(r["origin_url"]),
                        "depth": depth,
                        "title": title if title else "No title",
                        "frequency": total_frequency,
                        "relevance_score": relevance_score,
                    })
                
                # Sort by relevance score (highest first)
                results.sort(key=lambda x: -x["relevance_score"])
                
                logger.info(f"FTS5 search for '{query}' returned {len(results)} results")
                return results
                
            except Exception as fts_error:
                # FTS5 not available or query syntax error - fall back to simple search
                logger.debug(f"FTS5 search failed ({fts_error}), falling back to LIKE on title only")
                pattern = f"%{query}%"
                rows = conn.execute(
                    """
                    SELECT url, origin_url, depth, title, html_content
                    FROM pages
                    WHERE title LIKE ? OR html_content LIKE ?
                    ORDER BY depth ASC, url ASC
                    LIMIT ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
                
                # Count frequency in fallback mode too
                results = []
                search_lower = query.lower()
                
                for r in rows:
                    title = str(r["title"]) if r["title"] else ""
                    content = str(r["html_content"]) if r["html_content"] else ""
                    
                    title_lower = title.lower()
                    content_lower = content.lower()
                    
                    title_count = title_lower.count(search_lower)
                    content_count = content_lower.count(search_lower)
                    total_frequency = title_count + content_count
                    
                    depth = int(r["depth"])
                    relevance_score = (total_frequency * 10) + 1000 - (depth * 5)
                    
                    results.append({
                        "relevant_url": str(r["url"]),
                        "origin_url": str(r["origin_url"]),
                        "depth": depth,
                        "title": title if title else "No title",
                        "frequency": total_frequency,
                        "relevance_score": relevance_score,
                    })
                
                # Sort by relevance score
                results.sort(key=lambda x: -x["relevance_score"])
                
                logger.info(f"Fallback search for '{query}' returned {len(results)} results")
                return results
                
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to search pages: {e}")
        return []


def _search_from_word_index(query: str, limit: int) -> list[dict[str, Any]]:
    """
    Search using word index files (data/storage/*.data).
    Format per line: word url origin_url depth frequency
    """
    query = query.lower().strip()
    first_letter = query[0] if query else ''
    storage_file = STORAGE_DIR / f"{first_letter}.data"
    
    if not storage_file.exists():
        return []
    
    # Read matching entries from storage file
    matches: list[dict[str, Any]] = []
    
    with storage_file.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            parts = line.split(' ', 4)
            if len(parts) != 5:
                continue
            
            word, url, origin_url, depth_str, freq_str = parts
            
            # Check if word matches query
            if word != query:
                continue
            
            try:
                depth = int(depth_str)
                frequency = int(freq_str)
                
                # Calculate relevance score
                # score = (frequency × 10) + 1000 (exact match bonus) - (depth × 5)
                relevance_score = (frequency * 10) + 1000 - (depth * 5)
                
                # Get title from database
                title = _get_title_from_db(url)
                
                matches.append({
                    "relevant_url": url,
                    "origin_url": origin_url,
                    "depth": depth,
                    "title": title or "No title",
                    "frequency": frequency,
                    "relevance_score": relevance_score,
                })
            except ValueError:
                continue
    
    # Sort by relevance score (highest first)
    matches.sort(key=lambda x: -x["relevance_score"])
    
    # Limit results
    results = matches[:limit]
    
    logger.info(f"Word index search for '{query}' returned {len(results)} results")
    return results


def clear_word_index_storage() -> None:
    """
    Clear all word index storage files (data/storage/*.data).
    Call this when starting a fresh crawl.
    """
    try:
        if STORAGE_DIR.exists():
            for file in STORAGE_DIR.glob("*.data"):
                file.unlink()
            logger.info("Cleared word index storage files")
    except Exception as e:
        logger.error(f"Failed to clear word index storage: {e}")


def _get_title_from_db(url: str) -> str | None:
    """Get page title from database."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT title FROM pages WHERE url = ? LIMIT 1",
                (url,)
            ).fetchone()
            return str(row["title"]) if row and row["title"] else None
        finally:
            conn.close()
    except Exception:
        return None


def mark_queue_failed(url: str) -> None:
    update_queue_status(url, "failed")


def set_meta(key: str, value: str) -> None:
    try:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO crawl_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to set meta {key}: {e}")


def get_meta(key: str, default: str = "") -> str:
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM crawl_meta WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else default
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to get meta {key}: {e}")
        return default


def clear_all_pending(origin_url: str | None = None) -> int:
    """
    Clear all pending URLs from the queue table.
    If origin_url is provided, only clear pending for that origin.
    Returns the number of pending URLs that were cleared.
    """
    try:
        conn = get_connection()
        try:
            if origin_url:
                # Count pending before deletion
                count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM queue WHERE status = 'pending' AND origin_url = ?",
                    (origin_url,)
                ).fetchone()
                count = int(count_row["n"]) if count_row else 0
                
                # Delete pending URLs for this origin
                conn.execute(
                    "DELETE FROM queue WHERE status = 'pending' AND origin_url = ?",
                    (origin_url,)
                )
                conn.commit()
            else:
                # Count pending before deletion
                count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM queue WHERE status = 'pending'"
                ).fetchone()
                count = int(count_row["n"]) if count_row else 0
                
                # Delete all pending URLs
                conn.execute("DELETE FROM queue WHERE status = 'pending'")
                conn.commit()
            
            if count > 0:
                logger.info(f"Cleared {count} pending URLs from queue")
            
            return count
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to clear pending URLs: {e}")
        return 0


def _extract_words(text: str) -> list[str]:
    """
    Extract words from text for indexing.
    Converts to lowercase, removes special characters, keeps only alphanumeric.
    """
    import re
    # Remove HTML tags first
    text = re.sub(r'<[^>]+>', ' ', text)
    # Convert to lowercase and extract words (alphanumeric only, min 3 chars)
    words = re.findall(r'\b[a-z]{3,}\b', text.lower())
    return words


def _write_word_to_storage(word: str, url: str, origin_url: str, depth: int, frequency: int) -> None:
    """
    Write word index entry to storage file.
    Format: word url origin_url depth frequency
    Files are organized by first letter (e.g., p.data, a.data)
    """
    if not word or len(word) < 3:
        return
    
    first_letter = word[0].lower()
    storage_file = STORAGE_DIR / f"{first_letter}.data"
    
    try:
        # Append to file (create if doesn't exist)
        with storage_file.open('a', encoding='utf-8') as f:
            f.write(f"{word} {url} {origin_url} {depth} {frequency}\n")
    except Exception as e:
        logger.error(f"Failed to write word index for '{word}' to {storage_file}: {e}")


def index_page_words(url: str, origin_url: str, depth: int, title: str | None, html_content: str) -> None:
    """
    Extract words from page content and write to storage files.
    Creates word frequency index for relevance scoring.
    """
    # Combine title and content for word extraction
    full_text = ""
    if title:
        full_text += title + " "
    if html_content:
        full_text += html_content
    
    # Extract words
    words = _extract_words(full_text)
    
    # Count word frequencies
    word_freq: dict[str, int] = {}
    for word in words:
        word_freq[word] = word_freq.get(word, 0) + 1
    
    # Write to storage files
    for word, frequency in word_freq.items():
        _write_word_to_storage(word, url, origin_url, depth, frequency)
    
    logger.debug(f"Indexed {len(word_freq)} unique words from {url}")


def clear_all_pending(origin_url: str | None = None) -> int:
    """
    Clear all pending URLs from the queue table.
    If origin_url is provided, only clear URLs from that origin.
    Returns the number of pending URLs that were cleared.
    """
    try:
        conn = get_connection()
        try:
            if origin_url:
                # Count pending before deletion
                count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM queue WHERE status = 'pending' AND origin_url = ?",
                    (origin_url,)
                ).fetchone()
                count = int(count_row["n"]) if count_row else 0
                
                # Delete pending URLs for this origin
                conn.execute(
                    "DELETE FROM queue WHERE status = 'pending' AND origin_url = ?",
                    (origin_url,)
                )
            else:
                # Count pending before deletion
                count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM queue WHERE status = 'pending'"
                ).fetchone()
                count = int(count_row["n"]) if count_row else 0
                
                # Delete all pending URLs
                conn.execute("DELETE FROM queue WHERE status = 'pending'")
            
            conn.commit()
            
            if count > 0:
                logger.info(f"Cleared {count} pending URLs{f' for origin {origin_url}' if origin_url else ''}")
            
            return count
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to clear pending URLs: {e}")
        return 0
