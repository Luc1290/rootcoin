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
            const resp = await fetch(`/api/heatmap?limit=48&window=${_currentWindow}`);
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
        const volumeAssets = _data.assets.filter(a => !a.top_gainer && !a.top_mover);
        const gainerAssets = _data.assets.filter(a => a.top_gainer);
        const moverAssets = _data.assets.filter(a => a.top_mover);
        const changes = volumeAssets.map(a => parseFloat(a.change_24h));
        const avgChange = changes.length ? changes.reduce((s, v) => s + v, 0) / changes.length : 0;
        const positive = changes.filter(c => c > 0).length;
        const negative = changes.filter(c => c < 0).length;
        const gainerInfo = gainerAssets.length
            ? `<span class="text-yellow-400">\u{1F525} ${gainerAssets.length} pump${gainerAssets.length > 1 ? 's' : ''} 24h</span>`
            : '';
        const moverInfo = moverAssets.length
            ? `<span class="text-purple-400">\u26A1 ${moverAssets.length} volatile${moverAssets.length > 1 ? 's' : ''}</span>`
            : '';

        let summaryHtml = `
        <div class="flex flex-wrap gap-4 mb-3 text-sm">
            <span class="text-gray-400">Moyenne: <span class="${avgChange >= 0 ? 'pnl-positive' : 'pnl-negative'} font-bold">${avgChange >= 0 ? '+' : ''}${avgChange.toFixed(2)}%</span></span>
            <span class="pnl-positive">${positive} en hausse</span>
            <span class="pnl-negative">${negative} en baisse</span>
            ${gainerInfo}
            ${moverInfo}
        </div>`;

        // Build tiles (volume assets only in main grid)
        const tiles = volumeAssets.map(a => _tileHtml(a)).join('');
        grid.innerHTML = tiles;

        // Insert summary before grid
        const parent = grid.parentNode;
        let existingSummary = parent.querySelector('.heatmap-summary');
        if (existingSummary) existingSummary.remove();
        const summaryDiv = document.createElement('div');
        summaryDiv.className = 'heatmap-summary';
        summaryDiv.innerHTML = summaryHtml;
        parent.insertBefore(summaryDiv, grid);

        // Top gainers row below grid
        let gainerRow = parent.querySelector('.heatmap-gainers-row');
        if (gainerRow) gainerRow.remove();
        if (gainerAssets.length) {
            gainerRow = document.createElement('div');
            gainerRow.className = 'heatmap-gainers-row';
            gainerRow.innerHTML = `
                <div class="text-xs text-yellow-400 font-semibold mb-1.5 mt-3">\u{1F525} Top Gainers 24h</div>
                <div class="flex gap-1.5 overflow-x-auto pb-1">${gainerAssets.map(a => _tileHtml(a)).join('')}</div>`;
            parent.insertBefore(gainerRow, grid.nextSibling);
        }

        // Top movers row (high amplitude)
        let moverRow = parent.querySelector('.heatmap-movers-row');
        if (moverRow) moverRow.remove();
        if (moverAssets.length) {
            moverRow = document.createElement('div');
            moverRow.className = 'heatmap-movers-row';
            moverRow.innerHTML = `
                <div class="text-xs text-purple-400 font-semibold mb-1.5 mt-3">\u26A1 Top Movers (amplitude 24h)</div>
                <div class="flex gap-1.5 overflow-x-auto pb-1">${moverAssets.map(a => _tileHtml(a)).join('')}</div>`;
            const after = gainerRow ? gainerRow.nextSibling : grid.nextSibling;
            parent.insertBefore(moverRow, after);
        }
    }

    function _tileHtml(asset) {
        const change = parseFloat(asset.change_24h);
        const bgColor = _changeColor(change);
        const textColor = Math.abs(change) > 3 ? '#fff' : (Math.abs(change) > 1 ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.7)');
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        const price = Utils.fmtPrice(asset.price);
        const isGainer = asset.top_gainer;
        const isMover = asset.top_mover;
        const specialClass = isGainer ? ' heatmap-top-gainer' : (isMover ? ' heatmap-top-mover' : '');
        const badge = isGainer ? `<span class="heatmap-gainer-badge">\u{1F525}</span>`
            : (isMover ? `<span class="heatmap-gainer-badge">\u26A1</span>` : '');
        const change24h = isGainer ? parseFloat(asset.change_24h_pct) : null;
        const amplitude = isMover && asset.amplitude ? parseFloat(asset.amplitude) : null;
        let subtitle = '';
        if (isGainer && change24h !== null)
            subtitle = `<div class="text-xs opacity-50" style="color:${textColor}">24h: +${change24h.toFixed(0)}%</div>`;
        else if (isMover && amplitude !== null)
            subtitle = `<div class="text-xs opacity-50" style="color:${textColor}">range: ${amplitude.toFixed(0)}%</div>`;

        const base = asset.base_asset;
        const titleExtra = isGainer ? ' — Top Gainer 24h' : (isMover ? ` — Amplitude ${amplitude?.toFixed(0)}%` : '');
        const tradeUrl = `https://www.binance.com/en/trade/${base}_USDC?_from=markets&type=cross`;
        return `
        <a href="${tradeUrl}" target="_blank" rel="noopener" class="heatmap-tile${specialClass}" style="background:${bgColor};text-decoration:none;display:block" title="${asset.symbol} — ${price}${titleExtra}">
            <div class="font-bold text-sm" style="color:${textColor}">${badge}${base}</div>
            <div class="text-xs tabular-nums font-semibold" style="color:${textColor}">${changeStr}</div>
            <div class="text-xs tabular-nums opacity-60" style="color:${textColor}">${price}</div>
            ${subtitle}
        </a>`;
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
