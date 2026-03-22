"""
Local Web Crawler — Flask entrypoint.

Run: python app.py
Then open http://127.0.0.1:5000/
"""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, render_template, request

from crawler import get_registry, MAX_IN_MEMORY_QUEUE
from db import db_health, init_db, search_pages

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.get("/")
def index():
    try:
        health = db_health()
        status = get_registry().snapshot_all()
        # For template compatibility, provide a default crawler status
        default_status = {
            "max_depth": 2,
            "job_origin": "",
            "session_pages_fetched": 0,
            "pending_in_db": 0,
            "memory_queue_size": 0,
            "memory_queue_capacity": MAX_IN_MEMORY_QUEUE,
            "back_pressure": "No active crawlers",
            "workers": 0,
        }
        # Use first crawler's status if available
        if status["crawlers"]:
            default_status = status["crawlers"][0]
        return render_template("index.html", health=health, status=default_status)
    except Exception as e:
        logger.error(f"Error rendering index: {e}", exc_info=True)
        return f"Error: {e}", 500


@app.get("/api/health")
def api_health():
    try:
        return jsonify(db_health())
    except Exception as e:
        logger.error(f"Error in /api/health: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.get("/api/status")
def api_status():
    """Merged DB + all crawler runtime stats (for dashboard polling)."""
    try:
        payload = db_health()
        registry_status = get_registry().snapshot_all()
        payload["crawler"] = registry_status
        return jsonify(payload)
    except Exception as e:
        logger.error(f"Error in /api/status: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.post("/api/start")
def api_start():
    """Start (or continue) indexing from an origin URL up to depth k."""
    try:
        data = request.get_json(silent=True) or {}
        origin = (data.get("origin") or request.form.get("origin") or "").strip()
        k_raw = data.get("k", request.form.get("k", 2))
        try:
            k = int(k_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "k must be an integer"}), 400
        
        if not origin:
            return jsonify({"ok": False, "error": "origin URL is required"}), 400
        
        get_registry().start_crawler(origin, k)
        logger.info(f"Started crawl job: origin={origin}, max_depth={k}")
        return jsonify({"ok": True, "origin": origin, "max_depth": k})
    except ValueError as e:
        logger.warning(f"Invalid start request: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error in /api/start: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/crawlers")
def api_crawlers():
    """Get status of all active crawlers."""
    try:
        registry = get_registry()
        crawlers_data = []
        
        for origin, crawler in registry.get_all_crawlers():
            snapshot = crawler.snapshot()
            snapshot["origin"] = origin
            crawlers_data.append(snapshot)
        
        return jsonify({
            "total_crawlers": len(crawlers_data),
            "crawlers": crawlers_data
        })
    except Exception as e:
        logger.error(f"Error in /api/crawlers: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.post("/api/stop")
def api_stop():
    """Stop a specific crawler by origin URL."""
    try:
        data = request.get_json(silent=True) or {}
        origin = (data.get("origin") or request.form.get("origin") or "").strip()
        
        if not origin:
            return jsonify({"ok": False, "error": "origin URL is required"}), 400
        
        registry = get_registry()
        stopped = registry.stop_crawler(origin)
        
        if stopped:
            logger.info(f"Stopped crawler for origin={origin}")
            return jsonify({"ok": True, "origin": origin})
        else:
            return jsonify({"ok": False, "error": "Crawler not found"}), 404
    except Exception as e:
        logger.error(f"Error in /api/stop: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/search")
def api_search_get():
    try:
        q = request.args.get("q", "").strip()
        results = search_pages(q)
        return jsonify({"query": q, "results": results, "count": len(results)})
    except Exception as e:
        logger.error(f"Error in /api/search (GET): {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.post("/api/search")
def api_search_post():
    try:
        data = request.get_json(silent=True) or {}
        q = (data.get("q") or data.get("query") or request.form.get("q") or "").strip()
        results = search_pages(q)
        return jsonify({"query": q, "results": results, "count": len(results)})
    except Exception as e:
        logger.error(f"Error in /api/search (POST): {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def main() -> None:
    port = int(os.environ.get("PORT", "5000"))
    logger.info(f"Starting Flask app on http://127.0.0.1:{port}/")
    app.run(host="127.0.0.1", port=port, debug=True, threaded=True)


with app.app_context():
    try:
        init_db()
        # No need to bootstrap resume - crawlers start on demand
        logger.info("Application initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
