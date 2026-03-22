#  Sir Crawls-A-Lot

A high-performance, multi-threaded web crawler built with Python and Flask for indexing English Wikipedia pages. Features concurrent crawling with back-pressure handling, real-time search capabilities, and a modern web dashboard for monitoring and control.

## 🌟 Features

### Core Capabilities

- **🚀 Multi-threaded Crawling**: 5 concurrent worker threads with global rate limiting (~2 pages/sec)
- **⚡ Back Pressure Management**: Bounded in-memory queue (500 URLs max) prevents memory overflow
- **🔄 Concurrent Operations**: SQLite WAL mode enables simultaneous reads/writes without blocking
- **🔍 Real-time Search**: Fast LIKE-based full-text search while crawling is active
- **📊 Live Dashboard**: Real-time status updates with auto-polling every 2 seconds
- **💾 Resumable Crawls**: Automatically resume interrupted crawls on application restart
- **🛡️ Domain Restriction**: Strictly limited to English Wikipedia (en.wikipedia.org)

### Technical Highlights

- **Pure Python Standard Library**: Uses only `urllib` and `html.parser` (no BeautifulSoup/Scrapy)
- **Thread-safe Database**: Each worker gets its own SQLite connection with WAL journaling
- **Intelligent URL Filtering**: Excludes special Wikipedia pages (Talk:, User:, Template:, etc.)
- **Graceful Degradation**: Comprehensive error handling with failed URL tracking
- **Depth-based Crawling**: Configurable crawl depth (k) from origin URL

## 🏗️ Architecture

### System Design

```
┌─────────────────────────────────────────────────────────┐
│                     Flask Web Server                     │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────────┐  │
│  │   Dashboard  │ │  API Routes  │ │  Search Engine  │  │
│  └──────────────┘ └──────────────┘ └─────────────────┘  │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│                    Crawler Manager                       │
│  ┌───────────────────────────────────────────────────┐  │
│  │  Worker Pool (5 threads)                          │  │
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐   │  │
│  │  │ W1   │ │ W2   │ │ W3   │ │ W4   │ │ W5   │   │  │
│  │  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘   │  │
│  │                       ▲                            │  │
│  │                       │ Rate Limiter (0.5s min)   │  │
│  └───────────────────────────────────────────────────┘  │
│                         │                                │
│  ┌──────────────────────▼─────────────────────────┐    │
│  │  In-Memory Queue (Bounded: 500 URLs max)       │    │
│  │  ┌────────────────────────────────────────┐    │    │
│  │  │  Back Pressure: Blocks when full       │    │    │
│  │  └────────────────────────────────────────┘    │    │
│  └─────────────────┬──────────────────────────────┘    │
│                    │                                     │
│  ┌─────────────────▼─────────────────────────────┐     │
│  │  Drainer Thread (DB → RAM)                    │     │
│  │  Moves pending URLs from DB to memory queue   │     │
│  └────────────────────────────────────────────────┘     │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│             SQLite Database (WAL Mode)                   │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────┐  │
│  │    queue     │ │    pages     │ │  crawl_meta    │  │
│  │  (pending)   │ │  (indexed)   │ │  (settings)    │  │
│  └──────────────┘ └──────────────┘ └────────────────┘  │
│                                                          │
│  WAL: Concurrent reads/writes without blocking          │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

1. **URL Discovery**: HTML parser extracts links from fetched pages
2. **Queue Insertion**: New URLs added to SQLite `queue` table (status: pending)
3. **Drainer**: Background thread moves pending URLs to in-memory queue
4. **Worker Pool**: 5 threads fetch, parse, and index pages concurrently
5. **Back Pressure**: Memory queue blocks when full, pausing URL insertion
6. **Rate Limiting**: Global throttle ensures ~2 pages/sec across all workers
7. **Search**: Real-time LIKE queries on indexed content (concurrent with crawling)

## 🚀 Quick Start

### Prerequisites

- Python 3.7 or higher
- Flask library

### Installation

```bash
# Clone the repository
git clone https://github.com/Akbasal22/crawler.git
cd wikipedia-crawler

# Install dependencies
pip install flask

# Run the application
python app.py
```

The server will start on `http://127.0.0.1:5000/`

### First Crawl

1. Open your browser to `http://127.0.0.1:5000/`
2. Enter an origin URL (e.g., `https://en.wikipedia.org/wiki/Python_(programming_language)`)
3. Set max depth (e.g., `2` to crawl origin + links + their links)
4. Click "Start Crawl"
5. Watch the live dashboard update in real-time

## 📖 Using the Dashboard

### Control Panel

#### Start Indexing

- **Origin URL**: Wikipedia article to start crawling from
  - Must be from `en.wikipedia.org`
  - Only article pages allowed (no `Special:`, `Talk:`, `User:`, etc.)
- **Max Depth (k)**: How deep to crawl
  - `0` = only the origin page
  - `1` = origin + all linked pages
  - `2` = origin + links + their links
  - Higher values exponentially increase coverage

#### Search Index

- **Query Field**: Enter search terms
- **Results**: Displays matching pages with:
  - Page title
  - URL
  - Crawl depth
  - Origin URL

### Live Status Dashboard

Real-time metrics updated every 2 seconds:

- **Journal Mode**: Should show `wal` (Write-Ahead Logging)
- **Pages Indexed (DB)**: Total pages stored in database
- **Pages Fetched (Session)**: Pages crawled since app started
- **Pending in DB**: URLs waiting to be crawled
- **In-Memory Queue**: URLs loaded into RAM (max 500)
- **Back Pressure**: Indicates if queue is full
- **Workers**: Number of active crawler threads
- **Queue Breakdown**: JSON showing pending/visited/failed counts

### API Endpoints

#### Start a Crawl

```bash
curl -X POST http://127.0.0.1:5000/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "origin": "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "k": 2
  }'
```

Response:
```json
{
  "ok": true,
  "origin": "https://en.wikipedia.org/wiki/Python_(programming_language)",
  "max_depth": 2
}
```

#### Check Status

```bash
curl http://127.0.0.1:5000/api/status
```

Response:
```json
{
  "db_path": "/path/to/crawler.db",
  "journal_mode": "wal",
  "queue_by_status": {
    "pending": 150,
    "visited": 25,
    "failed": 2
  },
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

#### Search Pages

```bash
curl "http://127.0.0.1:5000/api/search?q=machine+learning"
```

Response:
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

#### Database Health

```bash
curl http://127.0.0.1:5000/api/health
```

## 🗄️ Database Schema

### Tables

#### `queue`
Tracks URLs to crawl and their status.

```sql
CREATE TABLE queue (
    url TEXT PRIMARY KEY,
    origin_url TEXT NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'visited', 'failed'))
);

CREATE INDEX idx_queue_status ON queue(status);
```

#### `pages`
Stores indexed page content.

```sql
CREATE TABLE pages (
    url TEXT PRIMARY KEY,
    origin_url TEXT NOT NULL,
    depth INTEGER NOT NULL,
    title TEXT,
    html_content TEXT
);

CREATE INDEX idx_pages_title ON pages(title);
```

#### `crawl_meta`
Stores crawler configuration for resumability.

```sql
CREATE TABLE crawl_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Keys:
- `max_depth`: Maximum crawl depth
- `job_origin`: Origin URL of current/last crawl

### SQLite Configuration

The database uses Write-Ahead Logging (WAL) mode for concurrent operations:

```python
PRAGMA journal_mode=WAL;      # Enable WAL mode
PRAGMA synchronous=NORMAL;    # Balance safety/performance
PRAGMA foreign_keys=ON;       # Enforce constraints
PRAGMA busy_timeout=5000;     # Wait 5s on locks
```

**Benefits of WAL Mode**:
- Readers don't block writers
- Writers don't block readers
- Better concurrency for multi-threaded crawling
- Crash recovery without corruption

## ⚙️ Configuration

### Crawler Settings

Located in `crawler.py`:

```python
NUM_WORKERS = 5                    # Concurrent worker threads
MIN_FETCH_INTERVAL_SEC = 0.5       # ~2 pages/sec (1/0.5)
MAX_IN_MEMORY_QUEUE = 500          # Back pressure threshold
DEFAULT_REQUEST_TIMEOUT_SEC = 5    # HTTP request timeout
```

### Modifying Settings

To change crawler behavior:

1. **Increase Speed**: Reduce `MIN_FETCH_INTERVAL_SEC` (be respectful!)
2. **More Workers**: Increase `NUM_WORKERS` (diminishing returns past 10)
3. **Memory Usage**: Adjust `MAX_IN_MEMORY_QUEUE`
4. **Timeout**: Change `DEFAULT_REQUEST_TIMEOUT_SEC` for slow sites

### Flask Settings

```python
# In app.py
port = int(os.environ.get("PORT", "5000"))
app.run(host="127.0.0.1", port=port, debug=True, threaded=True)
```

Override port:
```bash
PORT=8080 python app.py
```

## 🔧 How It Works

### Crawling Process

1. **Initialization**
   - Flask app starts
   - Database initialized with WAL mode
   - Bootstrap checks for pending URLs (resumable crawls)

2. **Starting a Crawl**
   - User submits origin URL + max depth
   - Crawler clears previous pending queue
   - Seeds queue with origin URL at depth 0
   - Starts 3 worker threads (if not already running)
   - Starts drainer thread (DB → RAM)

3. **Worker Loop**
   - Get URL from in-memory queue (blocking, 0.5s timeout)
   - Check if already indexed → skip
   - Rate limit: wait if fetched too recently
   - Fetch HTML with urllib
   - Parse with custom HTML parser
   - Extract title and links
   - Save to database (page + mark visited)
   - If depth < max_depth: enqueue discovered links

4. **Back Pressure**
   - In-memory queue limited to 300 URLs
   - When full: `put()` blocks until space available
   - Drainer waits when queue full
   - Workers naturally slow down

5. **Drainer Thread**
   - Runs continuously in background
   - Fetches pending URLs from DB (batch of 50)
   - Adds to in-memory queue (non-blocking)
   - Sleeps if queue full or no pending URLs

### Search Mechanism

- **Index on Creation**: Pages indexed immediately when crawled
- **Query Method**: SQLite LIKE search on title and HTML content
- **Concurrency**: WAL mode allows searches during active crawling
- **Ordering**: Results sorted by depth (shallow first), then URL

### Resumability

On app startup:
1. Check for pending URLs in database
2. If found: load saved `max_depth` and `job_origin` from `crawl_meta`
3. Resume workers and drainer threads
4. Continue crawling from where it left off

## 🛡️ URL Filtering

### Allowed URLs

✅ English Wikipedia article pages:
- `https://en.wikipedia.org/wiki/Article_Name`
- Domain must be exactly `en.wikipedia.org`
- Path must start with `/wiki/`

### Rejected URLs

❌ The crawler filters out:
- Non-Wikipedia domains
- Non-English Wikipedia (e.g., `es.wikipedia.org`)
- Special pages: `Wikipedia:`, `Talk:`, `User:`, `Template:`, `Category:`, `Help:`, `File:`, `Special:`
- Fragment identifiers (`#section`)
- Non-HTTP schemes (`mailto:`, `javascript:`, `tel:`)
- Relative URLs (converted to absolute, then validated)

### Example Filtering

```python
# Accepted
"https://en.wikipedia.org/wiki/Python_(programming_language)"
"https://en.wikipedia.org/wiki/Machine_learning"

# Rejected
"https://en.wikipedia.org/wiki/Wikipedia:About"      # Special page
"https://en.wikipedia.org/wiki/Talk:Python"          # Talk page
"https://es.wikipedia.org/wiki/Python"               # Spanish Wikipedia
"https://example.com/page"                           # Different domain
```

## 📁 Project Structure

```
wikipedia-crawler/
├── app.py                  # Flask application entrypoint
├── crawler.py              # Multi-threaded crawler logic
├── db.py                   # SQLite database layer
├── templates/
│   └── index.html          # Dashboard HTML template
├── static/
│   ├── style.css           # Dashboard styling
│   └── dashboard.js        # Frontend interactivity
├── crawler.db              # SQLite database (created on first run)
├── crawler.db-shm          # WAL shared memory
├── crawler.db-wal          # WAL log file
└── README.md               # This file
```

## 🚨 Troubleshooting

### Common Issues

#### Database Locked Errors

**Symptom**: `database is locked` errors in logs

**Solution**:
- WAL mode should prevent this
- Check `PRAGMA journal_mode;` returns `wal`
- Ensure `PRAGMA busy_timeout` is set (5000ms)
- If persists: reduce `NUM_WORKERS`

#### Workers Not Starting

**Symptom**: Pending URLs increase but no pages indexed

**Solution**:
- Check logs for worker thread startup messages
- Verify workers aren't blocking on full queue
- Restart application: `python app.py`

#### Memory Usage High

**Symptom**: Application using excessive RAM

**Solution**:
- Reduce `MAX_IN_MEMORY_QUEUE` (default 500)
- Lower `NUM_WORKERS` to reduce concurrent fetches
- Clear database: delete `crawler.db` and restart

#### Search Not Working

**Symptom**: No results for known indexed pages

**Solution**:
- Check `pages_count` in dashboard > 0
- Verify query matches page title or content
- Try simpler, shorter search terms
- Check database: `sqlite3 crawler.db "SELECT COUNT(*) FROM pages;"`

#### Rate Limiting Too Slow

**Symptom**: Crawling feels sluggish

**Solution**:
- Reduce `MIN_FETCH_INTERVAL_SEC` in `crawler.py`
- **Warning**: Be respectful to Wikipedia servers!
- Consider increasing workers instead

## 🎯 Performance Characteristics

### Throughput

- **Base Rate**: ~2 pages/second (global throttle: 0.5s minimum interval)
- **Workers**: 5 concurrent threads
- **Typical**: 120-150 pages/minute under normal conditions
- **Bottlenecks**:
  - Network latency (dominant factor)
  - Rate limiting (intentional)
  - Database writes (minimal with WAL)

### Memory Usage

- **Base**: ~50-100 MB (Python runtime + Flask)
- **Per Page**: ~100-500 KB (HTML content in database)
- **Queue**: ~1-5 MB (500 URLs × ~2-10 KB metadata)
- **Total**: Scales with indexed pages (1000 pages ≈ 200-600 MB)

### Scalability

**Vertical Scaling**:
- More workers → diminishing returns past 10 (network-bound)
- Larger queue → marginal benefit, more memory
- Faster rate → be respectful to servers

**Limitations**:
- SQLite: Good to ~100K pages, then consider PostgreSQL
- Single machine: Can't distribute workers across hosts
- No pagination: Large crawls (>1M pages) need architectural changes

## 🔒 Production Considerations

**This is a development/educational project. For production use, consider:**

1. **Database**: Migrate from SQLite to PostgreSQL for scalability
2. **Message Queue**: Use RabbitMQ/Redis instead of in-memory queue
3. **Robots.txt**: Implement proper robots.txt parsing and respect
4. **Rate Limiting**: Per-domain rate limiting for multi-site crawling
5. **Error Handling**: Retry logic, exponential backoff
6. **Monitoring**: Metrics, logging, alerting (Prometheus/Grafana)
7. **Containerization**: Docker/Kubernetes for deployment
8. **CDN/Caching**: Reduce load on origin servers
9. **Legal**: Terms of service compliance, API usage where available

See `recommendation.md` for detailed production roadmap.

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Add tests if applicable
5. Commit: `git commit -m 'Add amazing feature'`
6. Push: `git push origin feature/amazing-feature`
7. Open a Pull Request

## 📜 License

This project is for educational purposes. Please respect Wikipedia's [Terms of Use](https://foundation.wikimedia.org/wiki/Terms_of_Use) when crawling.

## 🙏 Acknowledgments

- Built with Python's standard library for maximum portability
- SQLite WAL mode enables concurrent operations
- Flask provides a lightweight web framework
- Inspired by the need for understanding web crawler internals

---

**Happy Crawling! 🕷️**

*Remember to crawl responsibly and respect website terms of service.*
