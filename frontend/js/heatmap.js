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
        const volumeAssets = _data.assets.filter(a => !a.top_gainer && !a.top_mover && !a.early_mover);
        const gainerAssets = _data.assets.filter(a => a.top_gainer);
        const moverAssets = _data.assets.filter(a => a.top_mover);
        const earlyAssets = _data.assets.filter(a => a.early_mover);
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

        // Macro heatmap (right column)
        const macroEl = document.getElementById('heatmap-macro');
        const macroTiles = _data.macro || [];
        if (macroEl) {
            if (macroTiles.length) {
                macroEl.classList.remove('hidden');
                macroEl.innerHTML = `
                    <div class="text-xs text-gray-500 font-semibold mb-1.5">Macro</div>
                    <div class="grid grid-cols-2 gap-1">${macroTiles.map(m => _macroTileHtml(m)).join('')}</div>`;
            } else {
                macroEl.classList.add('hidden');
            }
        }

        // Insert summary before the flex container
        const container = grid.parentNode.parentNode;
        let existingSummary = container.querySelector('.heatmap-summary');
        if (existingSummary) existingSummary.remove();
        const summaryDiv = document.createElement('div');
        summaryDiv.className = 'heatmap-summary';
        summaryDiv.innerHTML = summaryHtml;
        container.insertBefore(summaryDiv, grid.parentNode);

        // Special categories — 3-column grid below main heatmap
        let specialGrid = container.querySelector('.heatmap-special-grid');
        if (specialGrid) specialGrid.remove();
        if (earlyAssets.length || gainerAssets.length || moverAssets.length) {
            const earlyCol = earlyAssets.length ? `
                <div>
                    <div class="text-xs text-cyan-400 font-semibold mb-1.5">\u{1F680} D\u00e9marrages (5min)</div>
                    <div class="flex flex-col gap-1.5">${earlyAssets.map(a => _earlyTileHtml(a)).join('')}</div>
                </div>` : '<div></div>';
            const gainerCol = gainerAssets.length ? `
                <div>
                    <div class="text-xs text-yellow-400 font-semibold mb-1.5">\u{1F525} Top Gainers 24h</div>
                    <div class="flex flex-col gap-1.5">${gainerAssets.map(a => _tileHtml(a)).join('')}</div>
                </div>` : '<div></div>';
            const moverCol = moverAssets.length ? `
                <div>
                    <div class="text-xs text-purple-400 font-semibold mb-1.5">\u26A1 Top Movers (12h)</div>
                    <div class="flex flex-col gap-1.5">${moverAssets.map(a => _tileHtml(a)).join('')}</div>
                </div>` : '<div></div>';
            specialGrid = document.createElement('div');
            specialGrid.className = 'heatmap-special-grid grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4';
            specialGrid.innerHTML = earlyCol + gainerCol + moverCol;
            container.appendChild(specialGrid);
        }
    }

    function _tileHtml(asset) {
        const change = parseFloat(asset.change_24h);
        const bgColor = _changeColor(change);
        const textColor = Math.abs(change) > 3 ? '#fff' : (Math.abs(change) > 1 ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.7)');
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}% ${_currentWindow}`;
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

    function _earlyTileHtml(asset) {
        const change5m = parseFloat(asset.change_5m || 0);
        const surge = parseFloat(asset.surge_ratio || 0);
        const bgColor = _changeColor(change5m * 3); // amplify color for 5m moves
        const textColor = '#fff';
        const changeStr = `${change5m >= 0 ? '+' : ''}${change5m.toFixed(2)}% 5m`;
        const price = Utils.fmtPrice(asset.price);
        const base = asset.base_asset;
        const tradeUrl = `https://www.binance.com/en/trade/${base}_USDC?_from=markets&type=cross`;
        return `
        <a href="${tradeUrl}" target="_blank" rel="noopener" class="heatmap-tile heatmap-early-mover" style="background:${bgColor};text-decoration:none;display:block" title="${asset.symbol} — surge x${surge.toFixed(0)}">
            <div class="font-bold text-sm" style="color:${textColor}">\u{1F680} ${base}</div>
            <div class="text-xs tabular-nums font-semibold" style="color:${textColor}">${changeStr}</div>
            <div class="text-xs tabular-nums opacity-60" style="color:${textColor}">${price}</div>
            <div class="text-xs opacity-50" style="color:${textColor}">surge x${surge.toFixed(0)}</div>
        </a>`;
    }

    function _macroTileHtml(m) {
        const change = parseFloat(m.change_pct);
        // For inverted indicators (VIX, DXY), flip color: down = green
        const colorChange = m.inverted ? -change : change;
        const bgColor = _changeColor(colorChange * 8); // amplify: macro moves are smaller
        const textColor = 'rgba(255,255,255,0.9)';
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        const val = parseFloat(m.value);
        const valStr = val >= 1000 ? val.toLocaleString('en', {maximumFractionDigits: 0})
            : val >= 10 ? val.toFixed(2) : val.toFixed(3);
        // Crypto impact indicator
        const ci = m.crypto_impact;
        const ciColor = ci === 'up' ? '#22c55e' : ci === 'down' ? '#ef4444' : '#9ca3af';
        const ciIcon = ci === 'up' ? '&#x25B2;' : ci === 'down' ? '&#x25BC;' : '&#x2022;';
        return `
        <div class="heatmap-tile" style="background:${bgColor};min-width:0;padding:6px 8px" title="${m.label}: ${valStr} (${changeStr})">
            <div class="flex items-center justify-between" style="line-height:1.2">
                <span class="font-bold" style="color:${textColor};font-size:10px">${m.label}</span>
                <span style="color:${ciColor};font-size:8px" title="Impact crypto">${ciIcon}</span>
            </div>
            <div class="tabular-nums font-semibold" style="color:${textColor};font-size:10px">${changeStr}</div>
            <div class="tabular-nums opacity-60" style="color:${textColor};font-size:9px">${valStr}</div>
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
