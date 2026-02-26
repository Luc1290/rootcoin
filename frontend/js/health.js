const Health = (() => {
    let _data = null;
    let _logEntries = [];
    let _eventEntries = [];
    let _logPaused = false;
    let _logFilter = '';
    let _initialized = false;
    let _pollInterval = null;
    const POLL_DELAY = 10_000;
    const MAX_LOG_DISPLAY = 200;

    function init() {
        if (_initialized) return;
        _initialized = true;

        document.getElementById('log-level-filter').onchange = (e) => {
            _logFilter = e.target.value;
            _renderLogs();
        };
        document.getElementById('log-clear-btn').onclick = () => {
            _logEntries = [];
            _renderLogs();
        };
        document.getElementById('log-pause-btn').onclick = (e) => {
            _logPaused = !_logPaused;
            e.target.textContent = _logPaused ? 'Resume' : 'Pause';
        };
    }

    async function load() {
        try {
            const [healthResp, logsResp, eventsResp] = await Promise.all([
                fetch('/api/health'),
                fetch('/api/health/logs?limit=200'),
                fetch('/api/health/events?limit=50'),
            ]);
            if (healthResp.ok) _data = await healthResp.json();
            if (logsResp.ok) {
                const logData = await logsResp.json();
                _logEntries = logData.logs || [];
            }
            if (eventsResp.ok) {
                const evData = await eventsResp.json();
                _eventEntries = evData.events || [];
            }
            render();
        } catch (e) {
            console.error('Health load failed', e);
            document.getElementById('health-empty').classList.remove('hidden');
        }
    }

    function render() {
        if (!_data) {
            document.getElementById('health-empty').classList.remove('hidden');
            return;
        }
        document.getElementById('health-empty').classList.add('hidden');
        _renderFreshness();
        _renderWS();
        _renderModules();
        _renderDB();
        _renderSystem();
        _renderEvents();
        _renderLogs();
    }

    function _renderFreshness() {
        const el = document.getElementById('health-freshness');
        if (_data.collected_at) {
            el.textContent = 'Mis a jour ' + Utils.timeAgo(_data.collected_at);
        }
    }

    function _renderWS() {
        const el = document.getElementById('health-ws');
        const ws = _data.websockets;
        if (!ws) { el.innerHTML = ''; return; }

        const streams = [
            { key: 'user_stream', label: 'User Data Stream' },
            { key: 'price_stream', label: 'Price Stream' },
        ];

        el.innerHTML = streams.map(s => {
            const info = ws[s.key];
            if (!info) return '';
            const dotClass = _statusDotClass(info.status);
            const ageStr = info.last_msg_age_s !== null
                ? Math.round(info.last_msg_age_s) + 's ago'
                : 'No messages yet';
            return `
            <div class="card">
                <div class="flex items-center gap-2 mb-1">
                    <span class="health-dot ${dotClass}"></span>
                    <span class="text-sm font-semibold">${s.label}</span>
                </div>
                <div class="text-xs text-gray-500">Last message: ${ageStr}</div>
                <div class="text-xs text-gray-500 mt-1">Status: <span class="health-status-${info.status}">${info.status}</span></div>
            </div>`;
        }).join('');
    }

    function _renderModules() {
        const el = document.getElementById('health-modules');
        const modules = _data.modules;
        if (!modules || !modules.length) { el.innerHTML = ''; return; }

        const grid = modules.map(m => {
            const dotClass = _statusDotClass(m.status);
            return `
            <div class="health-module-card">
                <span class="health-dot ${dotClass}"></span>
                <span class="text-xs font-medium">${m.name.replace(/_/g, ' ')}</span>
            </div>`;
        }).join('');

        const healthy = modules.filter(m => m.status === 'healthy').length;
        const degraded = modules.filter(m => m.status === 'degraded').length;
        const unhealthy = modules.filter(m => m.status === 'unhealthy').length;

        el.innerHTML = `
        <div class="card">
            <div class="flex items-center justify-between mb-2">
                <div class="metric-label">Modules (${modules.length})</div>
                <div class="flex gap-3 text-xs">
                    <span class="pnl-positive">${healthy} healthy</span>
                    ${degraded ? `<span class="text-yellow-400">${degraded} degraded</span>` : ''}
                    ${unhealthy ? `<span class="pnl-negative">${unhealthy} unhealthy</span>` : ''}
                </div>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">${grid}</div>
        </div>`;
    }

    function _renderDB() {
        const el = document.getElementById('health-db');
        const db = _data.database;
        if (!db) { el.innerHTML = ''; return; }

        const rows = Object.entries(db.table_counts || {}).map(([table, count]) =>
            `<div class="flex justify-between text-xs py-1 border-b border-gray-800/30">
                <span class="text-gray-400">${table}</span>
                <span class="tabular-nums">${count.toLocaleString()}</span>
            </div>`
        ).join('');

        el.innerHTML = `
        <div class="card">
            <div class="metric-label mb-2">Database</div>
            <div class="grid grid-cols-2 gap-4 mb-3">
                <div>
                    <div class="text-xs text-gray-500">File size</div>
                    <div class="text-sm font-bold">${db.file_size_mb} MB</div>
                </div>
                <div>
                    <div class="text-xs text-gray-500">Query latency</div>
                    <div class="text-sm font-bold">${db.query_latency_ms} ms</div>
                </div>
            </div>
            <div>${rows}</div>
        </div>`;
    }

    function _renderSystem() {
        const el = document.getElementById('health-system');
        const mem = _data.memory;
        const proc = _data.process;
        if (!mem && !proc) { el.innerHTML = ''; return; }

        let html = '<div class="card"><div class="metric-label mb-2">System</div><div class="grid grid-cols-2 sm:grid-cols-4 gap-3">';

        if (_data.uptime_s) {
            html += `<div><div class="text-xs text-gray-500">Uptime</div><div class="text-sm font-bold">${_fmtUptime(_data.uptime_s)}</div></div>`;
        }
        if (mem && mem.rss_mb) {
            html += `<div><div class="text-xs text-gray-500">RSS Memory</div><div class="text-sm font-bold">${mem.rss_mb} MB</div></div>`;
        }
        if (proc && proc.python_version) {
            html += `<div><div class="text-xs text-gray-500">Python</div><div class="text-sm font-bold">${proc.python_version}</div></div>`;
        }
        if (proc && proc.pid) {
            html += `<div><div class="text-xs text-gray-500">PID</div><div class="text-sm font-bold">${proc.pid}</div></div>`;
        }

        html += '</div>';

        if (mem && mem.caches) {
            const cacheRows = Object.entries(mem.caches).map(([k, v]) =>
                `<span class="text-xs text-gray-500">${k.replace(/_/g, ' ')}: <span class="text-gray-300">${v}</span></span>`
            ).join(' &middot; ');
            html += `<div class="mt-3 text-xs">${cacheRows}</div>`;
        }

        html += '</div>';
        el.innerHTML = html;
    }

    function _renderEvents() {
        const el = document.getElementById('health-events');
        if (!_eventEntries.length) {
            el.innerHTML = '';
            return;
        }

        const rows = _eventEntries.slice().reverse().slice(0, 30).map((ev, i) => {
            const ts = ev.ts ? ev.ts.substring(11, 19) : '';
            const typeClass = 'event-type-' + (ev.type || '');
            const rawId = 'event-raw-' + i;
            const symbol = ev.raw && ev.raw.s ? ev.raw.s : '';
            const side = ev.raw && ev.raw.S ? ev.raw.S : '';
            const status = ev.raw && ev.raw.X ? ev.raw.X : '';
            const summary = [symbol, side, status].filter(Boolean).join(' ');
            return `<div class="event-row" onclick="document.getElementById('${rawId}').classList.toggle('hidden')">
                <div class="flex items-center gap-2">
                    <span class="text-xs text-gray-600">${ts}</span>
                    <span class="event-type ${typeClass}">${Utils.escHtml(ev.type || '')}</span>
                    <span class="text-xs text-gray-400">${Utils.escHtml(summary)}</span>
                </div>
                <div id="${rawId}" class="event-raw hidden">${Utils.escHtml(JSON.stringify(ev.raw, null, 2))}</div>
            </div>`;
        }).join('');

        const evRecorder = _data && _data.memory && _data.memory.event_recorder;
        const fileInfo = evRecorder ? ` &middot; ${evRecorder.today_file_kb} KB today` : '';

        el.innerHTML = `
        <div class="card">
            <div class="flex items-center justify-between mb-2">
                <div class="metric-label">Recent WS Events (${_eventEntries.length})</div>
                <span class="text-xs text-gray-600">${fileInfo}</span>
            </div>
            <div style="max-height:300px;overflow-y:auto">${rows}</div>
        </div>`;
    }

    function _renderLogs() {
        const el = document.getElementById('log-terminal');
        let entries = _logEntries;
        if (_logFilter) {
            entries = entries.filter(e => e.level === _logFilter);
        }
        entries = entries.slice(-MAX_LOG_DISPLAY);

        el.innerHTML = entries.map(e => {
            const lvlClass = 'log-level-' + (e.level || 'info');
            const ctx = e.context && Object.keys(e.context).length
                ? ' ' + Object.entries(e.context).map(([k, v]) => k + '=' + v).join(' ')
                : '';
            const ts = e.timestamp ? e.timestamp.substring(11, 19) : '';
            return `<div class="log-line"><span class="log-ts">${ts}</span> <span class="${lvlClass}">${(e.level || '').toUpperCase().padEnd(5)}</span> <span class="log-event">${Utils.escHtml(e.event || '')}</span><span class="log-ctx">${Utils.escHtml(ctx)}</span></div>`;
        }).join('');

        if (!_logPaused) {
            el.scrollTop = el.scrollHeight;
        }
    }

    function _statusDotClass(status) {
        if (status === 'healthy') return 'health-dot-green';
        if (status === 'degraded') return 'health-dot-yellow';
        return 'health-dot-red';
    }

    function _fmtUptime(seconds) {
        const d = Math.floor(seconds / 86400);
        const h = Math.floor((seconds % 86400) / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        if (d > 0) return d + 'd ' + h + 'h';
        if (h > 0) return h + 'h ' + m + 'm';
        return m + 'm';
    }

    // WS real-time updates
    WS.on('health_update', (data) => {
        _data = data;
        if (document.getElementById('view-health').classList.contains('hidden')) return;
        _renderFreshness();
        _renderWS();
        _renderModules();
        _renderDB();
        _renderSystem();
    });

    const _throttledRenderLogs = Utils.throttle(() => _renderLogs(), 200);

    WS.on('log_entry', (entry) => {
        _logEntries.push(entry);
        if (_logEntries.length > 1000) {
            _logEntries = _logEntries.slice(-500);
        }
        if (!_logPaused && !document.getElementById('view-health').classList.contains('hidden')) {
            _throttledRenderLogs();
        }
    });

    function startPolling() {
        stopPolling();
        _pollInterval = setInterval(load, POLL_DELAY);
    }

    function stopPolling() {
        if (_pollInterval) {
            clearInterval(_pollInterval);
            _pollInterval = null;
        }
    }

    return { init, load, startPolling, stopPolling };
})();
