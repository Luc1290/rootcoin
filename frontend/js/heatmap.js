const Heatmap = (() => {
    let _data = null;
    let _currentWindow = '4h';
    let _initialized = false;
    let _pollInterval = null;
    const POLL_DELAY = 60_000;

    function init() {
        if (_initialized) return;
        _initialized = true;
        document.querySelectorAll('#heatmap-windows .chart-interval-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('#heatmap-windows .chart-interval-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                _currentWindow = btn.dataset.window;
                load();
            });
        });
    }

    async function load() {
        try {
            const resp = await fetch(`/api/heatmap?limit=50&window=${_currentWindow}`);
            if (!resp.ok) throw new Error('Failed to load heatmap');
            _data = await resp.json();
            render();
        } catch (e) {
            console.error('Heatmap load failed', e);
            document.getElementById('heatmap-empty').classList.remove('hidden');
        }
    }

    function render() {
        const grid = document.getElementById('heatmap-grid');
        const empty = document.getElementById('heatmap-empty');
        const freshness = document.getElementById('heatmap-freshness');

        if (!_data || !_data.assets || !_data.assets.length) {
            if (empty) empty.classList.remove('hidden');
            grid.innerHTML = '';
            return;
        }
        if (empty) empty.classList.add('hidden');

        // Freshness
        if (freshness) {
            if (_data.is_stale) {
                freshness.innerHTML = '<span class="stale-badge">STALE</span>';
            } else if (_data.updated_at) {
                freshness.textContent = `Mis a jour ${Utils.timeAgo(_data.updated_at)}`;
            }
        }

        // Summary stats
        const changes = _data.assets.map(a => parseFloat(a.change_24h));
        const avgChange = changes.reduce((s, v) => s + v, 0) / changes.length;
        const positive = changes.filter(c => c > 0).length;
        const negative = changes.filter(c => c < 0).length;

        let summaryHtml = `
        <div class="flex gap-4 mb-3 text-sm">
            <span class="text-gray-400">Moyenne: <span class="${avgChange >= 0 ? 'pnl-positive' : 'pnl-negative'} font-bold">${avgChange >= 0 ? '+' : ''}${avgChange.toFixed(2)}%</span></span>
            <span class="pnl-positive">${positive} en hausse</span>
            <span class="pnl-negative">${negative} en baisse</span>
        </div>`;

        // Build tiles
        const tiles = _data.assets.map(a => _tileHtml(a)).join('');
        grid.innerHTML = tiles;

        // Insert summary before grid
        const parent = grid.parentNode;
        let existingSummary = parent.querySelector('.heatmap-summary');
        if (existingSummary) existingSummary.remove();
        const summaryDiv = document.createElement('div');
        summaryDiv.className = 'heatmap-summary';
        summaryDiv.innerHTML = summaryHtml;
        parent.insertBefore(summaryDiv, grid);
    }

    function _tileHtml(asset) {
        const change = parseFloat(asset.change_24h);
        const bgColor = _changeColor(change);
        const textColor = Math.abs(change) > 3 ? '#fff' : (Math.abs(change) > 1 ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.7)');
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        const price = _fmtPrice(asset.price);

        return `
        <div class="heatmap-tile" style="background:${bgColor}" title="${asset.symbol} — ${price}">
            <div class="font-bold text-sm" style="color:${textColor}">${asset.base_asset}</div>
            <div class="text-xs tabular-nums font-semibold" style="color:${textColor}">${changeStr}</div>
            <div class="text-xs tabular-nums opacity-60" style="color:${textColor}">${price}</div>
        </div>`;
    }

    function _changeColor(change) {
        // Clamp to -8% / +8% for intensity
        const ratio = Math.min(Math.abs(change) / 8, 1); // 0 to 1

        if (change >= 0) {
            // Dark green (20,80,40) -> Bright green (34,197,94)
            const r = Math.round(20 + (34 - 20) * ratio);
            const g = Math.round(80 + (197 - 80) * ratio);
            const b = Math.round(40 + (94 - 40) * ratio);
            return `rgb(${r}, ${g}, ${b})`;
        } else {
            // Dark red (80,20,20) -> Bright red (239,68,68)
            const r = Math.round(80 + (239 - 80) * ratio);
            const g = Math.round(20 + (68 - 20) * ratio);
            const b = Math.round(20 + (68 - 20) * ratio);
            return `rgb(${r}, ${g}, ${b})`;
        }
    }

    function _fmtPrice(val) {
        const n = parseFloat(val);
        if (isNaN(n)) return val;
        if (n >= 1000) return n.toLocaleString('fr-FR', { maximumFractionDigits: 0 });
        if (n >= 1) return n.toFixed(2);
        return n.toFixed(4);
    }

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
