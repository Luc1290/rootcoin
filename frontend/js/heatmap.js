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
        const gainerAssets = _data.assets.filter(a => a.top_gainer || parseFloat(a.change_24h_pct || 0) >= 4)
            .sort((a, b) => parseFloat(b.change_window || 0) - parseFloat(a.change_window || 0));
        const moverAssets = _data.assets.filter(a => a.top_mover);
        const earlyAssets = _data.assets.filter(a => a.early_mover);

        // Summary
        const changes = volumeAssets.map(a => parseFloat(a.change_window));
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

        // Specials (bottom bar — 4 columns: specials + notif journal)
        if (specialsEl) {
            specialsEl.classList.remove('hidden');
            specialsEl.innerHTML =
                _specialCol('\u{1F680} D\u00e9marrages', earlyAssets, 'early') +
                _specialCol('\u{1F525} Gainers 24h', gainerAssets, 'gainer') +
                _specialCol('\u26A1 Movers 12h', moverAssets, 'mover') +
                _notifColHtml();
            // Re-render notif content + rebind controls after DOM rebuild
            if (typeof Notifications !== 'undefined') {
                Notifications.render();
            }
        }
    }

    function _tileHtml(asset) {
        const change = parseFloat(asset.change_window);
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
            <div class="text-xs tabular-nums" style="color:rgba(255,255,255,0.85)">${price}</div>
        </a>`;
    }

    function _notifColHtml() {
        return `<div class="heatmap-notif-col">
            <div class="notif-col-header">
                <div class="heatmap-specials-title" style="margin-bottom:0">\u{1F514} Alertes</div>
                <div class="flex items-center gap-1">
                    <select id="notif-type-filter" class="px-1 py-0 text-[9px] bg-stone-800 border border-stone-700 rounded text-gray-400" style="height:18px">
                        <option value="">Toutes</option>
                        <option value="momentum">Mom</option>
                        <option value="early_mover">Early</option>
                    </select>
                    <span id="notif-stats" class="text-[9px] text-gray-500"></span>
                </div>
            </div>
            <div class="notif-col-scroll">
                <div id="notif-timeline" class="space-y-0.5"></div>
                <div id="notif-empty" class="text-center text-[10px] text-gray-600 py-2 hidden">Aucune alerte</div>
            </div>
            <button id="notif-load-more" class="text-[9px] text-blue-400 hover:text-blue-300 mt-1 hidden">+ voir plus</button>
        </div>`;
    }

    function _specialCol(title, assets, mode) {
        const tiles = assets.length
            ? assets.slice(0, 6).map(a => _specialTileHtml(a, mode)).join('')
            : '<div class="text-xs text-gray-600" style="grid-column:1/-1;padding:4px 0">Aucun</div>';
        return `<div>
            <div class="heatmap-specials-title">${title}</div>
            <div class="heatmap-specials-grid">${tiles}</div>
        </div>`;
    }

    function _specialTileHtml(asset, mode) {
        const isEarly = mode === 'early';
        const isGainer = mode === 'gainer';
        const change = parseFloat(isEarly ? (asset.change_5m || 0) : isGainer ? (asset.change_24h_pct || 0) : asset.change_window);
        const bgColor = _changeColor(isEarly ? change * 3 : isGainer ? change / 2 : change);
        const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`;
        const base = asset.base_asset;
        const price = Utils.fmtPrice(asset.price);
        const vol5m = isEarly && asset.vol_5m ? _fmtVol(asset.vol_5m) : '';
        const tradeUrl = `https://www.binance.com/en/trade/${base}_USDC?_from=markets&type=cross`;
        return `<a href="${tradeUrl}" target="_blank" rel="noopener" class="heatmap-special-tile" style="background:${bgColor};text-decoration:none" title="${asset.symbol} — ${price}${vol5m ? ' — vol 5m: ' + vol5m : ''}">
            <div class="font-bold" style="color:#fff;font-size:11px">${base}</div>
            <div class="tabular-nums font-semibold" style="color:#fff;font-size:10px">${changeStr}${vol5m ? ' <span style="opacity:0.7;font-weight:400">' + vol5m + '</span>' : ''}</div>
            <div class="tabular-nums" style="color:rgba(255,255,255,0.85);font-size:9px">${price}</div>
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
            <div class="tabular-nums" style="color:rgba(255,255,255,0.85);font-size:9px">${valStr}</div>
        </div>`;
    }

    function _fmtVol(vol) {
        const v = parseFloat(vol);
        if (isNaN(v)) return '';
        if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
        if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
        return `$${v.toFixed(0)}`;
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
