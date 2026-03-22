// Dashboard interactivity for the multi-crawler web crawler

(function() {
  'use strict';

  // Form elements
  const startForm = document.getElementById('form-start');
  const searchForm = document.getElementById('form-search');
  const startMsg = document.getElementById('start-msg');
  const searchMeta = document.getElementById('search-meta');
  const searchResults = document.getElementById('search-results');

  // Status elements
  const crawlersContainer = document.getElementById('crawlers-container');
  const valJournal = document.getElementById('val-journal');
  const valPagesDb = document.getElementById('val-pages-db');

  // Handle start crawl form
  if (startForm) {
    startForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const formData = new FormData(startForm);
      const origin = formData.get('origin');
      const k = formData.get('k');

      startMsg.textContent = 'Starting crawl...';
      startMsg.className = 'muted info';

      try {
        const response = await fetch('/api/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ origin, k: parseInt(k) })
        });

        const data = await response.json();

        if (data.ok) {
          startMsg.textContent = `✓ Crawl started from ${data.origin} (max depth: ${data.max_depth})`;
          startMsg.className = 'muted success';
          // Clear form
          startForm.reset();
        } else {
          startMsg.textContent = `✗ Error: ${data.error}`;
          startMsg.className = 'muted error';
        }
      } catch (error) {
        startMsg.textContent = `✗ Network error: ${error.message}`;
        startMsg.className = 'muted error';
      }
    });
  }

  // Handle search form
  if (searchForm) {
    searchForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const formData = new FormData(searchForm);
      const query = formData.get('q');

      if (!query || query.trim() === '') {
        searchMeta.textContent = 'Please enter a search query';
        searchMeta.className = 'muted error';
        searchResults.innerHTML = '';
        return;
      }

      searchMeta.textContent = 'Searching...';
      searchMeta.className = 'muted info';
      searchResults.innerHTML = '';

      try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        if (data.error) {
          searchMeta.textContent = `Error: ${data.error}`;
          searchMeta.className = 'muted error';
          return;
        }

        searchMeta.textContent = `Found ${data.count} result${data.count !== 1 ? 's' : ''} for "${data.query}"`;
        searchMeta.className = 'muted info';

        if (data.results && data.results.length > 0) {
          data.results.forEach(result => {
            const li = document.createElement('li');
            
            // Extract article name from URL if title is "No title"
            let displayTitle = result.title;
            if (!displayTitle || displayTitle === 'No title') {
              try {
                const url = new URL(result.relevant_url);
                const pathParts = url.pathname.split('/');
                const articleName = pathParts[pathParts.length - 1];
                if (articleName) {
                  displayTitle = decodeURIComponent(articleName).replace(/_/g, ' ');
                } else {
                  displayTitle = 'Untitled Article';
                }
              } catch (e) {
                displayTitle = 'Untitled Article';
              }
            }
            
            const title = document.createElement('div');
            title.className = 'result-title';
            title.textContent = displayTitle;
            
            const link = document.createElement('a');
            link.href = result.relevant_url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = result.relevant_url;
            
            const meta = document.createElement('div');
            meta.className = 'result-meta';
            
            // Add frequency badge if available
            const frequencyBadge = result.frequency ? 
              `<span class="frequency-badge">${result.frequency} match${result.frequency !== 1 ? 'es' : ''}</span> · ` : '';
            
            meta.innerHTML = `${frequencyBadge}Depth: ${result.depth} | Origin: ${result.origin_url}`;
            
            li.appendChild(title);
            li.appendChild(link);
            li.appendChild(meta);
            searchResults.appendChild(li);
          });
        } else {
          searchMeta.textContent = `No results found for "${data.query}"`;
        }
      } catch (error) {
        searchMeta.textContent = `Network error: ${error.message}`;
        searchMeta.className = 'muted error';
      }
    });
  }

  // Stop crawler function
  async function stopCrawler(origin) {
    try {
      const response = await fetch('/api/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin })
      });

      const data = await response.json();
      
      if (data.ok) {
        console.log(`Stopped crawler for ${origin}`);
      } else {
        console.error(`Failed to stop crawler: ${data.error}`);
      }
    } catch (error) {
      console.error('Error stopping crawler:', error);
    }
  }

  // Format origin URL for display (shorten if needed)
  function formatOriginUrl(url) {
    try {
      const urlObj = new URL(url);
      const path = urlObj.pathname;
      const parts = path.split('/');
      const article = parts[parts.length - 1];
      return decodeURIComponent(article).replace(/_/g, ' ') || url;
    } catch (e) {
      return url;
    }
  }

  // Render all active crawlers
  function renderCrawlers(crawlers) {
    if (!crawlers || crawlers.length === 0) {
      crawlersContainer.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">😴</div>
          <p>No active crawlers</p>
        </div>
      `;
      return;
    }

    crawlersContainer.innerHTML = '';

    crawlers.forEach(crawler => {
      const card = document.createElement('div');
      card.className = 'crawler-card';

      // Header with origin and stop button
      const header = document.createElement('div');
      header.className = 'crawler-header';

      const titleDiv = document.createElement('div');
      titleDiv.className = 'crawler-title';
      
      const statusDot = document.createElement('span');
      statusDot.className = 'status-dot';
      statusDot.style.background = crawler.running ? 
        (crawler.memory_queue_size > 0 ? '#10b981' : '#f59e0b') : '#6b7280';
      
      const title = document.createElement('span');
      title.textContent = formatOriginUrl(crawler.origin);
      title.title = crawler.origin; // Full URL on hover
      
      titleDiv.appendChild(statusDot);
      titleDiv.appendChild(title);

      const stopBtn = document.createElement('button');
      stopBtn.className = 'btn-stop';
      stopBtn.textContent = 'Stop';
      stopBtn.onclick = () => stopCrawler(crawler.origin);

      header.appendChild(titleDiv);
      header.appendChild(stopBtn);

      // Stats grid
      const stats = document.createElement('dl');
      stats.className = 'crawler-stats';

      const addStat = (label, value) => {
        const dt = document.createElement('dt');
        dt.textContent = label;
        const dd = document.createElement('dd');
        dd.textContent = value;
        stats.appendChild(dt);
        stats.appendChild(dd);
      };

      addStat('Status', crawler.running ? 
        (crawler.memory_queue_size > 0 ? '🟢 Active' : '🟡 Running (idle)') : '⚫ Stopped');
      addStat('Pages Fetched', crawler.session_pages_fetched || 0);
      addStat('Pending URLs', crawler.pending_in_db || 0);
      addStat('Queue', `${crawler.memory_queue_size || 0} / ${crawler.memory_queue_capacity || 0}`);
      addStat('Max Depth', crawler.max_depth);
      addStat('Workers', `${crawler.workers || 0} threads`);

      card.appendChild(header);
      card.appendChild(stats);
      crawlersContainer.appendChild(card);
    });
  }

  // Update all status
  async function updateStatus() {
    try {
      // Fetch crawlers data
      const crawlersResponse = await fetch('/api/crawlers');
      const crawlersData = await crawlersResponse.json();

      if (crawlersData.error) {
        console.error('Crawlers error:', crawlersData.error);
        return;
      }

      // Render active crawlers
      renderCrawlers(crawlersData.crawlers);

      // Fetch database health
      const healthResponse = await fetch('/api/health');
      const healthData = await healthResponse.json();

      if (healthData.error) {
        console.error('Health error:', healthData.error);
        return;
      }

      // Update database stats
      if (valJournal) valJournal.textContent = healthData.journal_mode || 'unknown';
      if (valPagesDb) valPagesDb.textContent = healthData.pages_count || 0;
      
      // Update queue progress bar
      const queueData = healthData.queue_by_status || {};
      const visited = queueData.visited || 0;
      const pending = queueData.pending || 0;
      const failed = queueData.failed || 0;
      const total = visited + pending + failed || 1; // Avoid division by zero
      
      const visitedPercent = (visited / total) * 100;
      const pendingPercent = (pending / total) * 100;
      const failedPercent = (failed / total) * 100;
      
      const progressBar = document.getElementById('queue-progress');
      if (progressBar) {
        const visitedSegment = progressBar.querySelector('.visited');
        const pendingSegment = progressBar.querySelector('.pending');
        const failedSegment = progressBar.querySelector('.failed');
        
        if (visitedSegment) visitedSegment.style.width = `${visitedPercent}%`;
        if (pendingSegment) pendingSegment.style.width = `${pendingPercent}%`;
        if (failedSegment) failedSegment.style.width = `${failedPercent}%`;
      }
      
      const queueVisited = document.getElementById('queue-visited');
      const queuePending = document.getElementById('queue-pending');
      const queueFailed = document.getElementById('queue-failed');
      
      if (queueVisited) queueVisited.textContent = `${visited.toLocaleString()} visited`;
      if (queuePending) queuePending.textContent = `${pending.toLocaleString()} pending`;
      if (queueFailed) queueFailed.textContent = `${failed.toLocaleString()} failed`;

    } catch (error) {
      console.error('Failed to update status:', error);
    }
  }

  // Start polling when page loads
  let pollInterval = null;
  updateStatus(); // Initial update
  pollInterval = setInterval(updateStatus, 2000); // Poll every 2 seconds

  // Stop polling when page is hidden (battery saving)
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    } else {
      if (!pollInterval) {
        updateStatus();
        pollInterval = setInterval(updateStatus, 2000);
      }
    }
  });

  // Cleanup on unload
  window.addEventListener('beforeunload', () => {
    if (pollInterval) {
      clearInterval(pollInterval);
    }
  });

})();
