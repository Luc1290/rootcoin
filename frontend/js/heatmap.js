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
        const summaryEl = document.getElementById('heatmap-summary');
        const macroEl = document.getElementById('heatmap-macro');
        const macroGrid = document.getElementById('heatmap-macro-grid');
        const specialsEl = document.getElementById('heatmap-specials');

        if (!_data || !_data.assets || !_data.assets.length) {
            if (empty) empty.classList.remove('hidden');
            grid.innerHTML = '';
            return;
        }
        if (empty) empty.classList.add('hidden');

        if (freshness) {
            if (_data.is_stale) {
                freshness.innerHTML = '<span class="stale-badge">STALE</span>';
            } else if (_data.updated_at) {
                freshness.textContent = `Mis a jour ${Utils.timeAgo(_data.updated_at)}`;
            }
        }

        const volumeAssets = _data.assets.filter(a => !a.top_gainer && !a.top_mover && !a.early_mover);
        const gainerAssets = _data.assets.filter(a => a.top_gainer);
        const moverAssets = _data.assets.filter(a => a.top_mover);
        const earlyAssets = _data.assets.filter(a => a.early_mover);

        // Summary
        const changes = volumeAssets.map(a => parseFloat(a.change_24h));
        const avgChange = changes.length ? changes.reduce((s, v) => s + v, 0) / changes.length : 0;
        const positive = changes.filter(c => c > 0).length;
        const negative = changes.filter(c => c < 0).length;

        if (summaryEl) {
            const gainerInfo = gainerAssets.length
                ? `<span class="text-yellow-400">\u{1F525} ${gainerAssets.length} pump${gainerAssets.length > 1 ? 's' : ''} 24h</span>` : '';
            const moverInfo = moverAssets.length
                ? `<span class="text-purple-400">\u26A1 ${moverAssets.length} volatile${moverAssets.length > 1 ? 's' : ''}</span>` : '';
            summaryEl.innerHTML = `
            <div class="flex flex-wrap gap-4 text-xs">
                <span class="text-gray-400">Moyenne: <span class="${avgChange >= 0 ? 'pnl-positive' : 'pnl-negative'} font-bold">${avgChange >= 0 ? '+' : ''}${avgChange.toFixed(2)}%</span></span>
                <span class="pnl-positive">${positive} en hausse</span>
                <span class="pnl-negative">${negative} en baisse</span>
                ${gainerInfo}
                ${moverInfo}
            </div>`;
        }

        // Main crypto grid
        grid.innerHTML = volumeAssets.map(a => _tileHtml(a)).join('');

        // Macro grid
        const macroTiles = _data.macro || [];
        if (macroEl && macroGrid) {
            if (macroTiles.length) {
                macroEl.classList.remove('hidden');
                macroGrid.innerHTML = macroTiles.map(m => _macroTileHtml(m)).join('');
            } else {
                macroEl.classList.add('hidden');
            }
        }

        // Specials (bottom bar)
        if (specialsEl) {
            const hasSpecials = earlyAssets.length || gainerAssets.length || moverAssets.length;
            if (hasSpecials) {
                specialsEl.classList.remove('hidden');
                specialsEl.innerHTML =
                    _specialCol('\u{1F680} D\u00e9marrages', 'text-cyan-400', earlyAssets, true) +
                    _specialCol('\u{1F525} Gainers 24h', 'text-yellow-400', gainerAssets, false) +
                    _specialCol('\u26A1 Movers 12h', 'text-purple-400', moverAssets, false);
            } else {
                specialsEl.classList.add('hidden');
            }
        }
    }

    function _tileHtml(asset) {
        const change = parseFloat(asset.change_24h);
        const bgColor = _changeColor(change);
        const textColor = Math.abs(change) > 3 ? '#fff' : (Math.abs(change) > 1 ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.7)');
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}% ${_currentWindow}`;
        const price = Utils.fmtPrice(asset.price);
        const base = asset.base_asset;
        const tradeUrl = `https://www.binance.com/en/trade/${base}_USDC?_from=markets&type=cross`;
        return `
        <a href="${tradeUrl}" target="_blank" rel="noopener" class="heatmap-tile" style="background:${bgColor};text-decoration:none" title="${asset.symbol} — ${price}">
            <div class="font-bold text-sm" style="color:${textColor}">${base}</div>
            <div class="text-xs tabular-nums font-semibold" style="color:${textColor}">${changeStr}</div>
            <div class="text-xs tabular-nums opacity-60" style="color:${textColor}">${price}</div>
        </a>`;
    }

    function _specialCol(title, colorClass, assets, isEarly) {
        if (!assets.length) return '<div></div>';
        const tiles = assets.slice(0, 6).map(a => _smallTileHtml(a, isEarly)).join('');
        return `<div>
            <div class="heatmap-specials-title ${colorClass}">${title}</div>
            <div class="heatmap-specials-wrap">${tiles}</div>
        </div>`;
    }

    function _smallTileHtml(asset, isEarly) {
        const change = parseFloat(isEarly ? (asset.change_5m || 0) : asset.change_24h);
        const bgColor = _changeColor(isEarly ? change * 3 : change);
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`;
        const base = asset.base_asset;
        const price = Utils.fmtPrice(asset.price);
        const tradeUrl = `https://www.binance.com/en/trade/${base}_USDC?_from=markets&type=cross`;
        return `<a href="${tradeUrl}" target="_blank" rel="noopener" class="heatmap-special-tile" style="background:${bgColor};text-decoration:none" title="${asset.symbol} — ${price}">
            <div class="font-bold" style="color:#fff;font-size:10px">${base}</div>
            <div class="tabular-nums" style="color:rgba(255,255,255,0.8);font-size:9px">${changeStr}</div>
        </a>`;
    }

    function _macroTileHtml(m) {
        const change = parseFloat(m.change_pct);
        const colorChange = m.inverted ? -change : change;
        const bgColor = _changeColor(colorChange * 8);
        const textColor = 'rgba(255,255,255,0.9)';
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        const val = parseFloat(m.value);
        const valStr = val >= 1000 ? val.toLocaleString('en', {maximumFractionDigits: 0})
            : val >= 10 ? val.toFixed(2) : val.toFixed(3);
        const ci = m.crypto_impact;
        const ciColor = ci === 'up' ? '#22c55e' : ci === 'down' ? '#ef4444' : '#9ca3af';
        const ciIcon = ci === 'up' ? '&#x25B2;' : ci === 'down' ? '&#x25BC;' : '&#x2022;';
        return `
        <div class="heatmap-tile" style="background:${bgColor}" title="${m.label}: ${valStr} (${changeStr})">
            <div class="flex items-center justify-between" style="line-height:1.2">
                <span class="font-bold" style="color:${textColor};font-size:10px">${m.label}</span>
                <span style="color:${ciColor};font-size:8px" title="Impact crypto">${ciIcon}</span>
            </div>
            <div class="tabular-nums font-semibold" style="color:${textColor};font-size:10px">${changeStr}</div>
            <div class="tabular-nums opacity-60" style="color:${textColor};font-size:9px">${valStr}</div>
        </div>`;
    }

    function _changeColor(change) {
        const ratio = Math.min(Math.abs(change) / 8, 1);
        if (change >= 0) {
            const r = Math.round(20 + (34 - 20) * ratio);
            const g = Math.round(80 + (197 - 80) * ratio);
            const b = Math.round(40 + (94 - 40) * ratio);
            return `rgb(${r}, ${g}, ${b})`;
        } else {
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
