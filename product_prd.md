# Product Requirements Document: Wikipedia Web Crawler

## 1. Executive Summary

### 1.1 Project Overview
Build a multi-threaded web crawler application specifically designed to index English Wikipedia articles. The system must provide real-time crawling with concurrent search capabilities, a web-based dashboard for monitoring, and handle back-pressure to prevent memory exhaustion.

### 1.2 Core Objectives
- Index Wikipedia pages starting from a user-specified origin URL
- Support concurrent crawling with depth-based traversal
- Enable real-time search on indexed content while crawling is active
- Provide a responsive web dashboard for control and monitoring
- Implement back-pressure mechanisms to prevent resource exhaustion
- Support resumable crawls after application restart

### 1.3 Success Criteria
- Successfully crawl and index 1000+ Wikipedia pages per crawl session
- Achieve ~2 pages/second throughput with configurable rate limiting
- Support concurrent reads (search) and writes (indexing) without blocking
- Memory usage stays bounded regardless of queue size
- Zero data loss on graceful application shutdown
- Dashboard updates reflect real-time crawler status within 2 seconds

## 2. Technology Stack

### 2.1 Backend
- **Language**: Python 3.7+
- **Web Framework**: Flask (lightweight, WSGI-compliant)
- **Database**: SQLite 3 with WAL (Write-Ahead Logging) mode
- **HTTP Client**: urllib (Python standard library)
- **HTML Parser**: html.parser (Python standard library)
- **Threading**: threading module (Python standard library)
- **Concurrency**: queue.Queue for thread-safe operations

### 2.2 Frontend
- **HTML5**: Semantic markup with accessibility features
- **CSS3**: Responsive design with flexbox/grid layouts
- **JavaScript**: Vanilla ES6+ (no frameworks)
- **AJAX**: Fetch API for asynchronous requests

### 2.3 Data Storage
- **Primary Database**: SQLite (file-based: `crawler.db`)
- **Journal Mode**: WAL (Write-Ahead Logging)
- **Concurrency**: Multiple read connections, single write serialization
- **Persistence**: File-based storage for portability

### 2.4 No External Dependencies
**Critical Requirement**: Beyond Flask, the crawler MUST use only Python standard library modules. This means:
- No BeautifulSoup (use html.parser)
- No Scrapy (custom implementation)
- No requests library (use urllib)
- No Redis/RabbitMQ (use in-memory queue.Queue)

## 3. Functional Requirements

### 3.1 Crawling Function

#### 3.1.1 URL Queue Management
**Input**: Origin URL (string), Max Depth (integer)

**Processing**:
1. Validate origin URL is from `en.wikipedia.org`
2. Clear any pending URLs from previous crawls
3. Insert origin URL into database `queue` table with depth=0, status='pending'
4. Load pending URLs from database into in-memory queue
5. Start worker threads if not already running

**Output**: Confirmation that crawl has started

**Database Operations**:
```sql
-- Clear previous pending URLs
DELETE FROM queue WHERE status = 'pending';

-- Insert origin URL
INSERT OR IGNORE INTO queue (url, origin_url, depth, status)
VALUES (?, ?, 0, 'pending');

-- Fetch pending URLs for in-memory queue
SELECT url, origin_url, depth FROM queue
WHERE status = 'pending'
ORDER BY rowid DESC
LIMIT 50;
```

#### 3.1.2 Multi-threaded Worker Architecture
**Number of Workers**: 5 concurrent threads

**Worker Loop**:
```python
while self._running:
    # Get URL from bounded queue (blocking with timeout)
    url, job_origin, depth = self._work_queue.get(timeout=0.5)
    
    # Skip if already indexed
    if page_exists(url):
        update_queue_status(url, 'visited')
        continue
    
    # Rate limiting (global throttle)
    self._throttle()  # Ensure MIN_FETCH_INTERVAL_SEC has passed
    
    # Fetch HTML
    html = fetch_html(url)  # Uses urllib with timeout
    
    # Parse HTML
    parser = PageContextParser(url)
    parser.feed(html)
    title = parser.parsed_title()
    links = parser.links
    
    # Save to database
    insert_page(url, job_origin, depth, title, html)
    update_queue_status(url, 'visited')
    
    # Enqueue discovered links if depth allows
    if depth < max_depth:
        for link in links:
            if _is_fetchable_http_url(link):
                try_enqueue_pending(link, job_origin, depth + 1)
```

**Error Handling**:
- HTTPError (404, 500, etc.) → mark URL as 'failed' in queue
- URLError (network issues) → mark as 'failed'
- Timeout → mark as 'failed'
- All errors logged with full traceback

#### 3.1.3 Back-Pressure Mechanism
**In-Memory Queue**: Bounded at 500 URLs maximum

**Behavior**:
- When queue is full: `queue.put()` blocks until space available
- Drainer thread pauses when memory queue is full
- Workers continue processing, gradually freeing space
- This prevents unbounded memory growth

**Implementation**:
```python
self._work_queue = queue.Queue(maxsize=MAX_IN_MEMORY_QUEUE)

# Blocking put (back-pressure)
try:
    self._work_queue.put((url, origin, depth), timeout=1.0)
except queue.Full:
    # Queue is full, back-pressure active
    return False
```

#### 3.1.4 Rate Limiting
**Global Rate Limiter**: Shared across all workers

**Target**: ~2 pages per second (MIN_FETCH_INTERVAL_SEC = 0.5)

**Implementation**:
```python
def _throttle(self):
    with self._rate_lock:  # Thread-safe
        now = time.monotonic()
        wait = MIN_FETCH_INTERVAL_SEC - (now - self._last_fetch_at)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch_at = time.monotonic()
```

**Enforcement**: Every worker calls `_throttle()` before fetching a URL

#### 3.1.5 Database Drainer Thread
**Purpose**: Move URLs from database to in-memory queue

**Behavior**:
```python
while self._running:
    if self._work_queue.full():
        time.sleep(0.2)
        continue
    
    # Calculate how many URLs we can add
    remaining = self._work_queue.maxsize - self._work_queue.qsize()
    batch = pending_urls(min(50, remaining))
    
    if not batch:
        time.sleep(0.5)  # No pending URLs, wait
        continue
    
    for url, origin, depth in batch:
        if url not in self._in_memory_urls:
            self._work_queue.put_nowait((url, origin, depth))
            self._in_memory_urls.add(url)
```

**Key Points**:
- Runs continuously in daemon thread
- Only adds URLs not already in memory (deduplication)
- Respects queue capacity
- Batch fetches for efficiency

#### 3.1.6 HTML Parsing
**Parser Class**: `PageContextParser` (extends `html.parser.HTMLParser`)

**Extraction**:
1. **Title**: Content between `<title>` tags
2. **Links**: All `<a href="...">` attributes

**Link Processing**:
```python
def handle_starttag(self, tag, attrs):
    if tag.lower() == 'a':
        href = get_attr(attrs, 'href')
        if href:
            abs_url = urljoin(self._base, href)  # Resolve relative URLs
            abs_url, _ = urldefrag(abs_url)  # Remove fragments
            if _is_fetchable_http_url(abs_url):
                self.links.append(abs_url)
```

**Title Extraction**:
```python
def handle_data(self, data):
    if self._in_title:
        self.title_parts.append(data)

def parsed_title(self):
    return "".join(self.title_parts).strip() or None
```

#### 3.1.7 URL Filtering
**Function**: `_is_fetchable_http_url(candidate: str) -> bool`

**Criteria**:
1. ✅ Scheme must be `http` or `https`
2. ✅ Domain must be exactly `en.wikipedia.org`
3. ✅ Path must start with `/wiki/`
4. ✅ Article name must not contain `:` (filters special pages)
5. ❌ Reject: `Wikipedia:`, `Talk:`, `User:`, `Template:`, `Category:`, `Help:`, `File:`, `Special:`

**Implementation**:
```python
def _is_fetchable_http_url(candidate: str) -> bool:
    parsed = urlparse(candidate)
    
    # Check scheme
    if parsed.scheme not in ("http", "https"):
        return False
    
    # Check domain (exact match)
    if parsed.netloc.lower() != "en.wikipedia.org":
        return False
    
    # Check path
    if not parsed.path.startswith("/wiki/"):
        return False
    
    # Extract article name
    article = parsed.path[6:]  # Remove "/wiki/"
    
    # Reject special pages (contain colon)
    if ":" in article or not article:
        return False
    
    return True
```

#### 3.1.8 HTTP Fetching
**Function**: `fetch_html(url: str) -> str`

**Implementation**:
```python
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
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
```

**Timeout**: 5 seconds (configurable)

**Error Handling**: Raises HTTPError, URLError, or TimeoutError (handled by worker)

#### 3.1.9 Resumability
**On Application Startup**:
```python
def bootstrap_resume_if_needed(self):
    pending_count = count_pending()
    if pending_count <= 0:
        return
    
    # Load saved settings
    self._max_depth = int(get_meta("max_depth", "2"))
    self._job_origin = get_meta("job_origin", "")
    
    # Start workers and drainer
    self._ensure_workers()
    # Continue crawling from pending URLs
```

**Metadata Storage**:
```sql
INSERT INTO crawl_meta (key, value) VALUES ('max_depth', '2');
INSERT INTO crawl_meta (key, value) VALUES ('job_origin', 'https://...');
```

### 3.2 Search Function

#### 3.2.1 Search API
**Endpoint**: `GET /api/search?q=query`

**Parameters**:
- `q` (required): Search query string

**Response**:
```json
{
  "query": "machine learning",
  "results": [
    {
      "relevant_url": "https://en.wikipedia.org/wiki/Machine_learning",
      "origin_url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
      "depth": 1,
      "title": "Machine learning"
    }
  ],
  "count": 1
}
```

#### 3.2.2 Search Implementation
**Query Method**: SQLite LIKE search on title and HTML content

```sql
SELECT url, origin_url, depth, title
FROM pages
WHERE title LIKE ? OR html_content LIKE ?
ORDER BY depth ASC, url ASC
LIMIT ?
```

**Pattern**: `%query%` (case-insensitive substring match)

**Limit**: 200 results maximum (configurable)

**Ordering**: Shallow pages first (depth ASC), then alphabetical

#### 3.2.3 Concurrent Search
**Enabled by**: SQLite WAL mode

**Behavior**:
- Reads can occur while writes are in progress
- No blocking between readers and writers
- Multiple concurrent search requests supported
- Searches see committed data immediately

**WAL Configuration**:
```python
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA synchronous=NORMAL;")
```

#### 3.2.4 Search Performance
**Expected**: <100ms for databases with <10,000 pages

**Optimization**:
- Index on `title` column: `CREATE INDEX idx_pages_title ON pages(title);`
- LIKE optimization: Prefix searches (`query%`) use index
- Full-text search: Consider adding FTS5 virtual table for production

## 4. Database Schema

### 4.1 Table Definitions

#### 4.1.1 queue Table
**Purpose**: Track URLs to be crawled and their status

```sql
CREATE TABLE IF NOT EXISTS queue (
    url TEXT PRIMARY KEY,
    origin_url TEXT NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'visited', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
```

**Columns**:
- `url`: Unique URL identifier (primary key)
- `origin_url`: The origin URL that led to discovering this URL
- `depth`: Distance from origin (0 = origin, 1 = direct link, etc.)
- `status`: Current processing state
  - `'pending'`: Waiting to be crawled
  - `'visited'`: Successfully crawled and indexed
  - `'failed'`: Crawl attempt failed (HTTP error, timeout, etc.)

**Usage**:
- Insert: When new URL discovered
- Update: When URL is fetched (pending → visited/failed)
- Query: Drainer fetches pending URLs

#### 4.1.2 pages Table
**Purpose**: Store indexed page content

```sql
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    origin_url TEXT NOT NULL,
    depth INTEGER NOT NULL,
    title TEXT,
    html_content TEXT
);

CREATE INDEX IF NOT EXISTS idx_pages_title ON pages(title);
```

**Columns**:
- `url`: Unique URL identifier (primary key)
- `origin_url`: The origin URL from crawl session
- `depth`: Distance from origin
- `title`: Extracted from `<title>` tag (nullable)
- `html_content`: Full HTML response body (for search)

**Usage**:
- Insert: When page successfully crawled
- Query: Search operations
- Check: Avoid re-crawling indexed pages

#### 4.1.3 crawl_meta Table
**Purpose**: Store crawler configuration for resumability

```sql
CREATE TABLE IF NOT EXISTS crawl_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

**Columns**:
- `key`: Configuration key (e.g., 'max_depth', 'job_origin')
- `value`: Configuration value (stored as text)

**Usage**:
- `max_depth`: Maximum crawl depth for current/last job
- `job_origin`: Origin URL of current/last job

**Operations**:
```sql
-- Set metadata
INSERT INTO crawl_meta (key, value) VALUES (?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value;

-- Get metadata
SELECT value FROM crawl_meta WHERE key = ?;
```

### 4.2 WAL Mode Requirement

#### 4.2.1 Enabling WAL
**Requirement**: Database MUST use Write-Ahead Logging mode

**Activation**:
```python
conn = sqlite3.connect("crawler.db")
conn.execute("PRAGMA journal_mode=WAL;")
result = conn.execute("PRAGMA journal_mode;").fetchone()
assert result[0] == 'wal', "WAL mode not enabled!"
```

**Persistence**: Once set, WAL mode persists for the database file

#### 4.2.2 WAL Benefits
1. **Concurrent Reads/Writes**: Readers don't block writers, writers don't block readers
2. **Better Performance**: Fewer fsync calls, sequential writes
3. **Crash Recovery**: Atomic commits with automatic recovery
4. **No Corruption**: Even with multiple threads accessing database

#### 4.2.3 WAL Configuration
```python
PRAGMA journal_mode=WAL;        # Enable WAL
PRAGMA synchronous=NORMAL;      # Balance safety/speed
PRAGMA foreign_keys=ON;         # Enforce constraints
PRAGMA busy_timeout=5000;       # Wait 5s if locked
```

#### 4.2.4 Thread Safety
**Connection Strategy**: Each thread gets its own connection

```python
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn
```

**Important**: 
- `check_same_thread=False`: Allow connection sharing (safe with WAL)
- Each worker opens/closes connections per operation
- No connection pooling (SQLite limitation)

### 4.3 Database Operations

#### 4.3.1 Insert Page
```python
def insert_page(url, origin_url, depth, title, html_content):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO pages (url, origin_url, depth, title, html_content) VALUES (?, ?, ?, ?, ?)",
            (url, origin_url, depth, title, html_content)
        )
        conn.execute(
            "UPDATE queue SET status = 'visited' WHERE url = ?",
            (url,)
        )
        conn.commit()
    finally:
        conn.close()
```

**Atomicity**: Both insert and update in single transaction

#### 4.3.2 Enqueue URL
```python
def try_enqueue_pending(url, origin_url, depth):
    if page_exists(url):
        return False  # Already indexed
    
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO queue (url, origin_url, depth, status) VALUES (?, ?, ?, 'pending')",
            (url, origin_url, depth)
        )
        conn.commit()
        return cursor.rowcount > 0  # True if inserted
    finally:
        conn.close()
```

**OR IGNORE**: Silently skip if URL already in queue

#### 4.3.3 Fetch Pending URLs
```python
def pending_urls(limit):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT url, origin_url, depth FROM queue WHERE status = 'pending' ORDER BY rowid DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [(r["url"], r["origin_url"], r["depth"]) for r in rows]
    finally:
        conn.close()
```

**ORDER BY rowid DESC**: Newest URLs first (LIFO-ish behavior)

## 5. User Interface Requirements

### 5.1 Dashboard Layout

#### 5.1.1 Page Structure
```html
<header>
  <h1>🕷️ Wikipedia Crawler</h1>
  <p>Index English Wikipedia pages...</p>
</header>

<main>
  <section class="card">
    <h2>Start Indexing</h2>
    <form id="form-start">
      <input name="origin" type="url" required>
      <input name="k" type="number" min="0" required>
      <button type="submit">Start Crawl</button>
    </form>
  </section>

  <section class="card">
    <h2>Search Index</h2>
    <form id="form-search">
      <input name="q" type="search">
      <button type="submit">Search</button>
    </form>
    <ul id="search-results"></ul>
  </section>

  <section class="card">
    <h2>Live Status</h2>
    <dl class="grid">
      <dt>Journal Mode</dt>
      <dd id="val-journal">wal</dd>
      <!-- More stats... -->
    </dl>
  </section>
</main>
```

#### 5.1.2 Start Indexing Panel
**Fields**:
- **Origin URL** (text input, required)
  - Type: `url`
  - Placeholder: `https://en.wikipedia.org/wiki/Python_(programming_language)`
  - Validation: Must be en.wikipedia.org
- **Max Depth** (number input, required)
  - Type: `number`
  - Min: 0
  - Default: 2
  - Label: "Max Depth (k)"

**Submit Action**:
```javascript
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const origin = form.elements['origin'].value;
  const k = parseInt(form.elements['k'].value);
  
  const response = await fetch('/api/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({origin, k})
  });
  
  const data = await response.json();
  if (data.ok) {
    showMessage('Crawl started!', 'success');
  } else {
    showMessage(data.error, 'error');
  }
});
```

#### 5.1.3 Search Panel
**Fields**:
- **Query** (search input)
  - Type: `search`
  - Placeholder: "substring match in title or HTML"
  - Autocomplete: off

**Submit Action**:
```javascript
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = form.elements['q'].value.trim();
  
  const response = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
  const data = await response.json();
  
  displayResults(data.results);
});
```

**Results Display**:
```javascript
function displayResults(results) {
  const ul = document.getElementById('search-results');
  ul.innerHTML = '';
  
  results.forEach(result => {
    const li = document.createElement('li');
    li.innerHTML = `
      <div class="result-title">${result.title}</div>
      <a href="${result.relevant_url}" target="_blank">${result.relevant_url}</a>
      <div class="result-meta">Depth: ${result.depth} | Origin: ${result.origin_url}</div>
    `;
    ul.appendChild(li);
  });
}
```

#### 5.1.4 Live Status Panel
**Metrics to Display**:
1. **Journal Mode**: WAL status (should be 'wal')
2. **Pages Indexed (DB)**: Total pages in database
3. **Pages Fetched (Session)**: Pages crawled since app started
4. **Pending in DB**: URLs waiting in database queue
5. **In-Memory Queue**: Current/max in-memory queue size
6. **Back Pressure**: Status message (active/inactive)
7. **Workers**: Number of active threads
8. **Queue Breakdown**: JSON with pending/visited/failed counts

**Auto-Refresh**:
```javascript
async function updateStatus() {
  const response = await fetch('/api/status');
  const data = await response.json();
  
  document.getElementById('val-journal').textContent = data.journal_mode;
  document.getElementById('val-pages-db').textContent = data.pages_count;
  document.getElementById('val-pages-session').textContent = data.crawler.session_pages_fetched;
  // ... update other fields
}

// Poll every 2 seconds
setInterval(updateStatus, 2000);
```

### 5.2 Styling Requirements

#### 5.2.1 Responsive Design
**Breakpoints**:
- Mobile: < 768px (single column)
- Tablet: 768px - 1024px (single column)
- Desktop: > 1024px (multi-column possible)

**Layout**:
```css
.card {
  background: #ffffff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 1rem;
}

.grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.5rem 1rem;
}
```

#### 5.2.2 Accessibility
- **ARIA Labels**: Use `aria-live="polite"` for status messages
- **Form Labels**: All inputs must have associated labels
- **Keyboard Navigation**: Tab order follows logical flow
- **Color Contrast**: WCAG AA minimum (4.5:1 for text)

#### 5.2.3 Visual Feedback
- **Success Messages**: Green background, checkmark icon
- **Error Messages**: Red background, X icon
- **Loading States**: Spinner or "..." indicator
- **Disabled States**: Grayed out, not clickable

## 6. API Specification

### 6.1 RESTful Endpoints

#### 6.1.1 GET /
**Purpose**: Render dashboard HTML

**Response**: HTML page with embedded Jinja2 template variables

**Template Variables**:
```python
{
  "health": {
    "db_path": "/path/to/crawler.db",
    "journal_mode": "wal",
    "queue_by_status": {"pending": 150, "visited": 25, "failed": 2},
    "pages_count": 25
  },
  "status": {
    "running": true,
    "max_depth": 2,
    "job_origin": "https://...",
    "session_pages_fetched": 25,
    "pending_in_db": 150,
    "memory_queue_size": 45,
    "memory_queue_capacity": 500,
    "back_pressure": "Crawling up to 2 pages/sec"
  }
}
```

#### 6.1.2 GET /api/health
**Purpose**: Database health check

**Response**:
```json
{
  "db_path": "/path/to/crawler.db",
  "journal_mode": "wal",
  "queue_by_status": {
    "pending": 150,
    "visited": 25,
    "failed": 2
  },
  "pages_count": 25
}
```

#### 6.1.3 GET /api/status
**Purpose**: Combined database + crawler status

**Response**:
```json
{
  "db_path": "/path/to/crawler.db",
  "journal_mode": "wal",
  "queue_by_status": {"pending": 150, "visited": 25, "failed": 2},
  "pages_count": 25,
  "crawler": {
    "workers_started": true,
    "running": true,
    "max_depth": 2,
    "job_origin": "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "pages_indexed_total": 25,
    "pending_in_db": 150,
    "memory_queue_size": 45,
    "memory_queue_capacity": 500,
    "memory_queue_full": false,
    "back_pressure": "Crawling up to 2 pages/sec (global)",
    "session_pages_fetched": 25
  }
}
```

#### 6.1.4 POST /api/start
**Purpose**: Start a new crawl job

**Request**:
```json
{
  "origin": "https://en.wikipedia.org/wiki/Python_(programming_language)",
  "k": 2
}
```

**Response (Success)**:
```json
{
  "ok": true,
  "origin": "https://en.wikipedia.org/wiki/Python_(programming_language)",
  "max_depth": 2
}
```

**Response (Error)**:
```json
{
  "ok": false,
  "error": "origin must be from en.wikipedia.org"
}
```

**Status Codes**:
- 200: Success
- 400: Invalid input (missing origin, invalid k, wrong domain)
- 500: Internal server error

#### 6.1.5 GET /api/search
**Purpose**: Search indexed pages

**Parameters**:
- `q` (query string, required): Search term

**Request Example**:
```
GET /api/search?q=machine+learning
```

**Response**:
```json
{
  "query": "machine learning",
  "results": [
    {
      "relevant_url": "https://en.wikipedia.org/wiki/Machine_learning",
      "origin_url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
      "depth": 1,
      "title": "Machine learning"
    }
  ],
  "count": 1
}
```

**Status Codes**:
- 200: Success (even if 0 results)
- 500: Internal server error

#### 6.1.6 POST /api/search
**Purpose**: Search indexed pages (POST variant)

**Request**:
```json
{
  "q": "machine learning"
}
```

**Response**: Same as GET /api/search

## 7. Non-Functional Requirements

### 7.1 Performance
- **Throughput**: ~120 pages/minute (2 pages/sec)
- **Search Latency**: <100ms for <10K pages
- **Memory Usage**: <500MB for typical crawl (1000 pages)
- **Startup Time**: <2 seconds

### 7.2 Reliability
- **Data Integrity**: No data loss on graceful shutdown
- **Error Recovery**: Failed URLs marked, not retried indefinitely
- **Resumability**: Automatic resume after restart
- **Database Corruption**: WAL mode prevents corruption

### 7.3 Scalability Limits
- **Pages**: Works well up to 100K pages
- **Concurrent Users**: Single instance supports ~10 concurrent dashboard users
- **Workers**: Diminishing returns beyond 10 threads
- **Queue Depth**: Limited by database size (SQLite handles millions of rows)

### 7.4 Maintainability
- **Logging**: Comprehensive logs with timestamps and thread names
- **Configuration**: Constants at top of files for easy tuning
- **Documentation**: Docstrings on all public functions
- **Error Messages**: Clear, actionable error descriptions

### 7.5 Security
- **Input Validation**: URL and depth validation before processing
- **Domain Restriction**: Only en.wikipedia.org allowed
- **Timeouts**: HTTP requests timeout after 5 seconds
- **No Injection**: Parameterized SQL queries only

## 8. Implementation Steps

### 8.1 Phase 1: Database Layer (db.py)
1. Define schema with WAL configuration
2. Implement `get_connection()` with WAL enabled
3. Implement `init_db()` to create tables
4. Implement CRUD operations:
   - `try_enqueue_pending()`
   - `pending_urls()`
   - `insert_page()`
   - `update_queue_status()`
   - `page_exists()`
5. Implement metadata functions:
   - `set_meta()`
   - `get_meta()`
6. Implement query functions:
   - `count_pages()`
   - `count_pending()`
   - `count_queue_by_status()`
   - `search_pages()`
7. Implement utility:
   - `clear_all_pending()`
   - `mark_queue_failed()`
   - `db_health()`

### 8.2 Phase 2: Crawler Logic (crawler.py)
1. Implement URL validation:
   - `_is_fetchable_http_url()`
2. Implement HTML parser:
   - `PageContextParser` class
   - Title extraction
   - Link extraction
3. Implement HTTP fetching:
   - `fetch_html()` with timeout
   - Error handling
4. Implement CrawlManager:
   - Initialize bounded queue
   - Implement rate limiter
   - Implement worker loop
   - Implement drainer thread
   - Implement `start_job()`
   - Implement `bootstrap_resume_if_needed()`
5. Implement singleton:
   - `get_manager()` function

### 8.3 Phase 3: Flask Application (app.py)
1. Set up Flask app
2. Configure logging
3. Implement routes:
   - `GET /` (render template)
   - `GET /api/health`
   - `GET /api/status`
   - `POST /api/start`
   - `GET /api/search`
   - `POST /api/search`
4. Implement startup initialization:
   - `init_db()`
   - `bootstrap_resume_if_needed()`
5. Implement `main()` function

### 8.4 Phase 4: Frontend (templates/index.html, static/)
1. Create HTML template:
   - Header
   - Start Indexing form
   - Search form
   - Live Status section
2. Create CSS stylesheet:
   - Responsive layout
   - Card styling
   - Form styling
   - Result styling
3. Create JavaScript:
   - Form submission handlers
   - Search result rendering
   - Status polling (2s interval)
   - Error handling

### 8.5 Phase 5: Testing
1. Test database operations:
   - WAL mode verification
   - Concurrent read/write
   - Resumability
2. Test crawler:
   - URL filtering
   - Rate limiting
   - Back-pressure
   - Error handling
3. Test API:
   - All endpoints
   - Error cases
   - Concurrent requests
4. Test UI:
   - Form validation
   - Search display
   - Status updates

## 9. Configuration Constants

### 9.1 crawler.py
```python
NUM_WORKERS = 5                    # Worker threads
MIN_FETCH_INTERVAL_SEC = 0.5       # Rate limit (2 pages/sec)
MAX_IN_MEMORY_QUEUE = 500          # Back-pressure threshold
DEFAULT_REQUEST_TIMEOUT_SEC = 5    # HTTP timeout
```

### 9.2 db.py
```python
DB_PATH = BASE_DIR / "crawler.db"  # Database file path
```

### 9.3 app.py
```python
port = int(os.environ.get("PORT", "5000"))  # Flask port
app.run(host="127.0.0.1", port=port, debug=True, threaded=True)
```

## 10. Success Metrics

### 10.1 Functional Metrics
- ✅ Successfully crawl 1000+ Wikipedia pages
- ✅ Search returns results in <100ms
- ✅ Dashboard updates within 2 seconds
- ✅ No database corruption after 10+ restarts
- ✅ Memory usage stays below 500MB

### 10.2 Quality Metrics
- ✅ WAL mode enabled and verified
- ✅ All URLs strictly from en.wikipedia.org
- ✅ Zero data loss on shutdown
- ✅ Failed URLs properly marked
- ✅ Logs provide actionable error information

### 10.3 User Experience Metrics
- ✅ Intuitive UI requiring no documentation
- ✅ Real-time feedback on all actions
- ✅ Clear error messages
- ✅ Responsive design works on mobile
- ✅ Accessible to screen readers

## 11. Deliverables

### 11.1 Code Files
1. `app.py` - Flask application (100-150 lines)
2. `crawler.py` - Crawler logic (400-500 lines)
3. `db.py` - Database layer (300-400 lines)
4. `templates/index.html` - Dashboard HTML (80-100 lines)
5. `static/style.css` - Styling (150-200 lines)
6. `static/dashboard.js` - Frontend JS (200-300 lines)

### 11.2 Documentation
1. `README.md` - User guide and setup
2. `recommendation.md` - Production deployment guide
3. `product_prd.md` - This document

### 11.3 Database
1. `crawler.db` - SQLite database file (created on first run)
2. `crawler.db-wal` - WAL log file
3. `crawler.db-shm` - Shared memory file

## 12. Constraints and Assumptions

### 12.1 Constraints
- **No external libraries** beyond Flask
- **SQLite only** (no PostgreSQL/MySQL)
- **Single machine** (no distributed deployment)
- **En.wikipedia.org only** (domain hardcoded)
- **WAL mode required** (non-negotiable)

### 12.2 Assumptions
- **Wikipedia structure stable**: <title> tags and <a> links present
- **Network available**: Internet connectivity assumed
- **Disk space sufficient**: No quota management
- **Python 3.7+**: Modern Python features available
- **Wikipedia allows crawling**: Respectful rate limiting sufficient

### 12.3 Out of Scope
- ❌ Robots.txt parsing
- ❌ Multi-domain crawling
- ❌ Distributed architecture
- ❌ Advanced search (ranking, facets)
- ❌ User authentication
- ❌ Data export/import
- ❌ Scheduled crawls
- ❌ Email notifications

---

## Appendix A: Example Crawl Session

**Input**:
- Origin: `https://en.wikipedia.org/wiki/Python_(programming_language)`
- Max Depth: 2

**Expected Behavior**:
1. Depth 0: Fetch and index Python article
2. Depth 1: Fetch all articles linked from Python page (~200 links)
3. Depth 2: Fetch all articles linked from depth-1 pages (~20,000+ links)
4. Total: ~20,200 pages indexed
5. Time: ~3 hours at 2 pages/sec

**Database State**:
```sql
-- queue table
url                                                    | status   | depth
------------------------------------------------------ | -------- | -----
https://en.wikipedia.org/wiki/Python_(prog...)        | visited  | 0
https://en.wikipedia.org/wiki/Guido_van_Rossum        | visited  | 1
https://en.wikipedia.org/wiki/Computer_programming    | pending  | 2

-- pages table
url                                   | title                      | depth
------------------------------------- | -------------------------- | -----
https://en.wikipedia.org/wiki/Python  | Python (programming lang)  | 0
```

---

## Appendix B: Glossary

- **Back Pressure**: Mechanism to slow producers when consumers can't keep up
- **Bounded Queue**: Queue with maximum size limit
- **Depth**: Number of link hops from origin URL
- **Drainer**: Thread that moves URLs from database to memory
- **Origin URL**: Starting point of a crawl session
- **Pending**: URL queued but not yet crawled
- **Rate Limiting**: Throttling requests to avoid overloading servers
- **Resumability**: Ability to continue crawl after restart
- **WAL**: Write-Ahead Logging, SQLite journaling mode
- **Worker**: Thread that fetches and indexes web pages
