const Opportunities = (() => {
    const POSITION_SIZE = 40000; // $ position size for gain estimation
    let _opportunities = [];
    let _dismissedIds = _loadDismissed();
    // Per-container chart tracking: { containerId: { oppId: chartId } }
    let _containerCharts = {};
    // Per-container previous keys for change detection
    let _prevKeys = {};
    // Timer for updating timeAgo
    let _agoTimer = null;
    // Live prices from WS
    const _livePrices = {};
    let _priceRefreshTimer = null;

    function update(list) {
        if (!Array.isArray(list)) return;
        _opportunities = list;
    }

    function render(container, maxItems) {
        if (!container) return;
        const cid = container.id;
        const visible = _opportunities.filter(o => !_dismissedIds.has(o.id));

        if (!visible.length) {
            _destroyContainerCharts(cid);
            _prevKeys[cid] = '';
            container.innerHTML = '';
            return;
        }

        // Sort by best R:R first
        const sorted = [...visible].sort((a, b) => {
            const rrA = parseFloat((a.levels || {}).rr) || 0;
            const rrB = parseFloat((b.levels || {}).rr) || 0;
            return rrB - rrA;
        });

        const limit = maxItems || sorted.length;
        const items = sorted.slice(0, limit);

        // Check which symbols have active positions
        const activeSymbols = (typeof Positions !== 'undefined' && Positions.getActiveSymbols)
            ? Positions.getActiveSymbols() : {};

        // Change detection: skip full rebuild if same opportunities + same taken state
        const takenKey = Object.keys(activeSymbols).sort().join(',');
        const newKey = items.map(o => o.id).join(',') + '|' + takenKey;
        if (_prevKeys[cid] === newKey && _containerCharts[cid] && Object.keys(_containerCharts[cid]).length === items.length) {
            _refreshAgo(container);
            return;
        }
        _prevKeys[cid] = newKey;

        const cards = items.map(o => {
            const sym = o.symbol.replace('USDC', '');
            const dirClass = o.direction === 'LONG' ? 'long' : 'short';
            const chartContainerId = `${cid}-chart-${o.id}`;

            // Direction badge (same style as position cards)
            const sideClass = o.direction === 'LONG' ? 'side-long' : 'side-short';
            const dirBadge = `<span class="cockpit-side ${sideClass}">${o.direction}</span>`;

            // Taken badge
            const takenSide = activeSymbols[o.symbol];
            let takenBadge = '';
            if (takenSide) {
                const badgeColor = takenSide === 'LONG' ? '#22c55e' : '#ef4444';
                takenBadge = `<span style="background:${badgeColor};color:#fff;font-size:9px;padding:1px 5px;border-radius:3px;font-weight:600">Pris</span>`;
            }

            const lvl = o.levels || {};
            const retestPrice = o.timing && o.timing.retest_price ? o.timing.retest_price : null;
            const retestType = o.timing && o.timing.retest_type ? o.timing.retest_type : null;
            const retestMet = o.timing && o.timing.retest_met;
            const retestLabel = retestType === 'plancher' ? 'Attendre plancher ↓' : retestType === 'plafond' ? 'Attendre plafond ↑' : 'Retest';
            let levelsHtml = '';
            if (lvl.entry) {
                const e = parseFloat(lvl.entry);
                const slPct = e ? ((parseFloat(lvl.sl) - e) / e * 100) : 0;
                const tpPct = e ? ((parseFloat(lvl.tp1) - e) / e * 100) : 0;
                const tp2Pct = lvl.tp2 && e ? ((parseFloat(lvl.tp2) - e) / e * 100) : 0;
                const slGain = Math.round(POSITION_SIZE * slPct / 100);
                const tpGain = Math.round(POSITION_SIZE * tpPct / 100);
                const tp2Gain = Math.round(POSITION_SIZE * tp2Pct / 100);
                const liveP = _livePrices[o.symbol];
                const curPrice = liveP || (o.current_price ? parseFloat(o.current_price) : null);
                const curDist = curPrice && e ? ((curPrice - e) / e * 100) : null;
                levelsHtml = `<div class="opp-levels">
                ${curPrice ? `<span class="opp-lvl" data-opp-price="${o.symbol}"><span style="color:#9ca3af">Prix</span> <span style="color:#d1d5db;font-weight:600" data-opp-price-val>${Utils.fmtPriceCompact(curPrice)}</span> <span data-opp-price-dist style="color:${curDist >= 0 ? '#22c55e' : '#ef4444'};font-size:10px">${curDist !== null ? (curDist >= 0 ? '+' : '') + curDist.toFixed(2) + '%' : ''}</span></span>` : ''}
                <span class="opp-lvl"><span style="color:#c9956b">Entrer &agrave;</span> <span style="color:#c9956b">${Utils.fmtPriceCompact(lvl.entry)}</span></span>
                <span class="opp-lvl"><span style="color:#ef4444">SL</span> <span style="color:#ef4444">${Utils.fmtPriceCompact(lvl.sl)}</span> <span style="color:#ef4444;font-size:10px">${slPct.toFixed(2)}% ${slGain}$</span></span>
                <span class="opp-lvl"><span style="color:#22c55e">TP</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp1)}</span> <span style="color:#22c55e;font-size:10px">+${tpPct.toFixed(2)}% +${tpGain}$</span></span>
                ${lvl.tp2 ? `<span class="opp-lvl"><span style="color:#22c55e">TP2</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp2)}</span> <span style="color:#22c55e;font-size:10px">+${tp2Pct.toFixed(2)}% +${tp2Gain}$</span></span>` : ''}
                ${retestPrice && !retestMet ? `<span class="opp-lvl"><span style="color:#f59e0b">${retestLabel}</span> <span style="color:#f59e0b">${Utils.fmtPriceCompact(retestPrice)}</span></span>` : ''}
                <span class="opp-rr">R:R ${lvl.rr}</span>
            </div>`;
            }

            const ago = Utils.timeAgoShort(o.detected_at);

            return `<div class="mini-chart-card ${dirClass}">
                <div class="flex items-center justify-between mb-1">
                    <div class="flex items-center gap-2">
                        <span class="card-type-tag tag-signal">Signal</span>
                        <span class="text-sm font-bold">${sym}</span>
                        ${dirBadge}
                        ${takenBadge}
                        <span class="text-xs text-gray-500 opp-ago" data-ts="${o.detected_at || ''}">${ago}</span>
                    </div>
                    <button class="opp-dismiss" onclick="Opportunities.dismiss('${o.id}')" title="Masquer">&#x2715;</button>
                </div>
                <div id="${chartContainerId}" style="height:140px;width:100%"></div>
                ${levelsHtml}
            </div>`;
        }).join('');

        // Destroy only this container's charts before rewriting DOM
        _destroyContainerCharts(cid);

        container.innerHTML = `<div class="space-y-2">${cards}</div>`;

        // Create charts for visible opportunities
        const newCharts = {};
        for (const o of items) {
            const chartContainerId = `${cid}-chart-${o.id}`;
            const lvl = o.levels || {};

            const rm = o.timing && o.timing.retest_met;
            const rp = !rm && o.timing && o.timing.retest_price ? parseFloat(o.timing.retest_price) : 0;
            const rt = o.timing && o.timing.retest_type;
            const rl = rt === 'plancher' ? 'Attendre plancher ↓' : rt === 'plafond' ? 'Attendre plafond ↑' : 'Retest';
            const chartId = MiniTradeChart.create(chartContainerId, {
                symbol: o.symbol,
                height: 140,
                entryPrice: lvl.entry ? parseFloat(lvl.entry) : 0,
                entryLabel: 'Entry',
                slPrice: lvl.sl ? parseFloat(lvl.sl) : 0,
                tpPrice: lvl.tp1 ? parseFloat(lvl.tp1) : 0,
                retestPrice: rp,
                retestLabel: rl,
                showLineLabels: false,
            });

            if (chartId) {
                newCharts[o.id] = chartId;

                if (o.detected_at) MiniTradeChart.addMarker(chartId, o.detected_at, o.direction, lvl.entry);

                MiniTradeChart.fetchAndRender(chartId, o.symbol, '5m', _klineLimitForDetection(o.detected_at));
            }
        }
        _containerCharts[cid] = newCharts;

        // Start periodic timeAgo refresh
        _ensureAgoTimer();
    }

    function _refreshAgo(container) {
        if (!container) return;
        container.querySelectorAll('.opp-ago').forEach(el => {
            const ts = el.dataset.ts;
            if (ts) el.textContent = Utils.timeAgoShort(ts);
        });
    }

    function _ensureAgoTimer() {
        if (_agoTimer) return;
        _agoTimer = setInterval(() => {
            document.querySelectorAll('.opp-ago').forEach(el => {
                const ts = el.dataset.ts;
                if (ts) el.textContent = Utils.timeAgoShort(ts);
            });
        }, 30_000);
    }

    function dismiss(id) {
        _dismissedIds.add(id);
        _saveDismissed();
        // Force re-render on all known containers
        _prevKeys = {};
        const cids = Object.keys(_containerCharts);
        for (const cid of cids) {
            const el = document.getElementById(cid);
            if (el) render(el);
        }
    }

    function _destroyContainerCharts(cid) {
        const charts = _containerCharts[cid];
        if (!charts) return;
        for (const chartId of Object.values(charts)) {
            MiniTradeChart.destroy(chartId);
        }
        delete _containerCharts[cid];
    }

    function _loadDismissed() {
        try {
            const raw = sessionStorage.getItem('opp_dismissed');
            return raw ? new Set(JSON.parse(raw)) : new Set();
        } catch { return new Set(); }
    }

    function _saveDismissed() {
        try {
            sessionStorage.setItem('opp_dismissed', JSON.stringify([..._dismissedIds]));
        } catch {}
    }

    function _klineLimitForDetection(detectedAt) {
        if (!detectedAt) return 72;
        const elapsed = Date.now() - new Date(detectedAt).getTime();
        const candleMs = 5 * 60 * 1000;
        const candles = Math.ceil(elapsed / candleMs);
        // 36 candles (3h) padding before, min 72 (6h), max 1000 (~3.5 days at 5m)
        return Math.max(72, Math.min(candles + 36, 1000));
    }

    // ── Compact list mode (analysis page) ──────────────────

    let _expandedId = null;

    function renderCompact(container) {
        if (!container) return;
        const cid = container.id;
        const visible = _opportunities.filter(o => !_dismissedIds.has(o.id));

        if (!visible.length) {
            _destroyContainerCharts(cid);
            _prevKeys[cid] = '';
            container.innerHTML = '';
            return;
        }

        // Sort newest first
        const sorted = [...visible].sort((a, b) => {
            const tA = a.detected_at ? new Date(a.detected_at).getTime() : 0;
            const tB = b.detected_at ? new Date(b.detected_at).getTime() : 0;
            return tB - tA;
        });

        const activeSymbols = (typeof Positions !== 'undefined' && Positions.getActiveSymbols)
            ? Positions.getActiveSymbols() : {};

        const takenKey = Object.keys(activeSymbols).sort().join(',');
        const newKey = 'c|' + sorted.map(o => o.id).join(',') + '|' + takenKey + '|' + (_expandedId || '');
        if (_prevKeys[cid] === newKey) {
            _refreshAgo(container);
            return;
        }
        _prevKeys[cid] = newKey;

        _destroyContainerCharts(cid);

        const rows = sorted.map(o => {
            const sym = o.symbol.replace('USDC', '');
            const sideClass = o.direction === 'LONG' ? 'side-long' : 'side-short';
            const dirBadge = `<span class="cockpit-side ${sideClass}" style="font-size:9px;padding:1px 4px">${o.direction}</span>`;
            const lvl = o.levels || {};
            const rrVal = lvl.rr ? parseFloat(lvl.rr) : null;
            const rrColor = rrVal !== null ? (rrVal >= 2 ? '#c9956b' : rrVal >= 1.5 ? '#a78b6d' : '#6b7280') : '';
            const rrBadge = rrVal !== null ? `<span class="text-xs tabular-nums font-semibold" style="color:${rrColor}">${rrVal.toFixed(1)}R</span>` : '';

            const takenSide = activeSymbols[o.symbol];
            const takenBadge = takenSide
                ? `<span style="background:${takenSide === 'LONG' ? '#22c55e' : '#ef4444'};color:#fff;font-size:8px;padding:1px 4px;border-radius:3px;font-weight:600">Pris</span>`
                : '';

            const ago = Utils.timeAgoShort(o.detected_at);
            const isExpanded = _expandedId === o.id;
            const chartContainerId = `${cid}-chart-${o.id}`;
            const dirClass = o.direction === 'LONG' ? 'long' : 'short';

            const retestPrice = o.timing && o.timing.retest_price ? o.timing.retest_price : null;
            const retestType = o.timing && o.timing.retest_type ? o.timing.retest_type : null;
            const retestMet = o.timing && o.timing.retest_met;
            const retestLabel = retestType === 'plancher' ? 'Attendre plancher ↓' : retestType === 'plafond' ? 'Attendre plafond ↑' : 'Retest';
            const timingStatus = o.timing ? o.timing.status : null;
            const timingTitle = timingStatus === 'ready' ? 'Entrer maintenant'
                : timingStatus === 'wait' ? (o.timing.summary || 'Attendre') : 'Prudence';
            const timingDot = timingStatus === 'ready' ? `<span style="color:#4ade80" title="${timingTitle}">&#9679;</span>`
                : timingStatus === 'wait' ? `<span style="color:#f59e0b" title="${timingTitle}">&#9679;</span>`
                : timingStatus === 'caution' ? `<span style="color:#fb923c" title="${timingTitle}">&#9679;</span>` : '';

            let levelsLine = '';
            if (lvl.entry) {
                levelsLine = `<span style="color:#c9956b">${Utils.fmtPriceCompact(lvl.entry)}</span> <span style="color:#ef4444">${Utils.fmtPriceCompact(lvl.sl)}</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp1)}</span>`;
                if (retestPrice && !retestMet) levelsLine += ` <span style="color:#f59e0b">${retestLabel} ${Utils.fmtPriceCompact(retestPrice)}</span>`;
            }

            return `<div class="opp-compact-item ${dirClass}" data-opp-id="${o.id}">
                <div class="opp-compact-row" onclick="Opportunities.toggle('${o.id}','${cid}')">
                    <div class="flex items-center gap-2" style="min-width:0">
                        <span class="text-xs font-bold">${sym}</span>
                        ${dirBadge}
                        ${rrBadge}
                        ${timingDot}
                        ${takenBadge}
                        <span class="text-xs text-gray-500 tabular-nums truncate" style="font-size:9px">${levelsLine}</span>
                    </div>
                    <div class="flex items-center gap-2 flex-shrink-0">
                        <span class="text-xs text-gray-400 opp-ago" data-ts="${o.detected_at || ''}">${ago}</span>
                        <button class="opp-dismiss" onclick="event.stopPropagation();Opportunities.dismiss('${o.id}')" title="Masquer">&#x2715;</button>
                    </div>
                </div>
                ${isExpanded ? `<div id="${chartContainerId}" style="height:140px;width:100%;margin-top:4px"></div>
                    <div class="opp-levels" style="margin-top:2px">
                        ${lvl.entry ? (() => {
                            const e = parseFloat(lvl.entry);
                            const slP = e ? ((parseFloat(lvl.sl) - e) / e * 100) : 0;
                            const tpP = e ? ((parseFloat(lvl.tp1) - e) / e * 100) : 0;
                            const cp2 = _livePrices[o.symbol] || (o.current_price ? parseFloat(o.current_price) : null);
                            const cpD2 = cp2 && e ? ((cp2 - e) / e * 100) : null;
                            return `${cp2 ? `<span class="opp-lvl" data-opp-price="${o.symbol}"><span style="color:#9ca3af">Prix</span> <span style="color:#d1d5db;font-weight:600" data-opp-price-val>${Utils.fmtPriceCompact(cp2)}</span> <span data-opp-price-dist style="color:${cpD2 >= 0 ? '#22c55e' : '#ef4444'};font-size:10px">${cpD2 !== null ? (cpD2 >= 0 ? '+' : '') + cpD2.toFixed(2) + '%' : ''}</span></span>` : ''}
                            <span class="opp-lvl"><span style="color:#c9956b">Entrer &agrave;</span> ${Utils.fmtPriceCompact(lvl.entry)}</span>
                            <span class="opp-lvl"><span style="color:#ef4444">SL</span> ${Utils.fmtPriceCompact(lvl.sl)} <span style="font-size:10px;color:#ef4444">${slP.toFixed(2)}% ${Math.round(POSITION_SIZE * slP / 100)}$</span></span>
                            <span class="opp-lvl"><span style="color:#22c55e">TP</span> ${Utils.fmtPriceCompact(lvl.tp1)} <span style="font-size:10px;color:#22c55e">+${tpP.toFixed(2)}% +${Math.round(POSITION_SIZE * tpP / 100)}$</span></span>`;
                        })() : ''}
                        ${lvl.tp2 ? (() => {
                            const e = parseFloat(lvl.entry);
                            const tp2P = e ? ((parseFloat(lvl.tp2) - e) / e * 100) : 0;
                            return `<span class="opp-lvl"><span style="color:#22c55e">TP2</span> ${Utils.fmtPriceCompact(lvl.tp2)} <span style="font-size:10px;color:#22c55e">+${tp2P.toFixed(2)}% +${Math.round(POSITION_SIZE * tp2P / 100)}$</span></span>`;
                        })() : ''}
                        ${retestPrice && !retestMet ? `<span class="opp-lvl"><span style="color:#f59e0b">${retestLabel}</span> <span style="color:#f59e0b">${Utils.fmtPriceCompact(retestPrice)}</span></span>` : ''}
                        ${rrVal ? `<span class="opp-rr">R:R ${rrVal.toFixed(1)}</span>` : ''}
                    </div>` : ''}
            </div>`;
        }).join('');

        container.innerHTML = `<div class="card" style="padding:8px">
            <div class="flex items-center justify-between mb-2">
                <span class="metric-label">Signaux actifs</span>
                <span class="text-xs text-gray-500">${sorted.length}</span>
            </div>
            <div class="opp-compact-list">${rows}</div>
        </div>`;

        // Create chart for expanded item
        if (_expandedId) {
            const o = sorted.find(x => x.id === _expandedId);
            if (o) {
                const chartContainerId = `${cid}-chart-${o.id}`;
                const lvl = o.levels || {};
                const newCharts = {};
                const rm2 = o.timing && o.timing.retest_met;
                const rp = !rm2 && o.timing && o.timing.retest_price ? parseFloat(o.timing.retest_price) : 0;
                const rt = o.timing && o.timing.retest_type;
                const rl = rt === 'plancher' ? 'Attendre plancher ↓' : rt === 'plafond' ? 'Attendre plafond ↑' : 'Retest';
                const chartId = MiniTradeChart.create(chartContainerId, {
                    symbol: o.symbol,
                    height: 140,
                    entryPrice: lvl.entry ? parseFloat(lvl.entry) : 0,
                    entryLabel: 'Entry',
                    slPrice: lvl.sl ? parseFloat(lvl.sl) : 0,
                    tpPrice: lvl.tp1 ? parseFloat(lvl.tp1) : 0,
                    retestPrice: rp,
                    retestLabel: rl,
                    showLineLabels: false,
                });
                if (chartId) {
                    newCharts[o.id] = chartId;
                    if (o.detected_at) MiniTradeChart.addMarker(chartId, o.detected_at, o.direction, lvl.entry);
                    MiniTradeChart.fetchAndRender(chartId, o.symbol, '5m', _klineLimitForDetection(o.detected_at));
                }
                _containerCharts[cid] = newCharts;
            }
        }

        _ensureAgoTimer();
    }

    function toggle(id, cid) {
        _expandedId = _expandedId === id ? null : id;
        _prevKeys[cid] = ''; // force rebuild
        const el = document.getElementById(cid);
        if (el) renderCompact(el);
    }

    // Live price updates from WS — cache prices, refresh DOM every 60s
    WS.on('price_update', (data) => {
        if (data.symbol && data.price) _livePrices[data.symbol] = parseFloat(data.price);
        if (!_priceRefreshTimer) {
            _priceRefreshTimer = setInterval(_refreshLivePrices, 60000);
        }
    });

    function _refreshLivePrices() {
        document.querySelectorAll('[data-opp-price]').forEach(el => {
            const sym = el.dataset.oppPrice;
            const price = _livePrices[sym];
            if (!price) return;
            const valEl = el.querySelector('[data-opp-price-val]');
            const distEl = el.querySelector('[data-opp-price-dist]');
            if (valEl) valEl.textContent = Utils.fmtPriceCompact(price);
            // Find entry price from the sibling
            const opp = _opportunities.find(o => o.symbol === sym);
            if (distEl && opp && opp.levels && opp.levels.entry) {
                const e = parseFloat(opp.levels.entry);
                const dist = e ? ((price - e) / e * 100) : 0;
                distEl.textContent = `${dist >= 0 ? '+' : ''}${dist.toFixed(2)}%`;
                distEl.style.color = dist >= 0 ? '#22c55e' : '#ef4444';
            }
        });
    }

    return { update, render, renderCompact, toggle, dismiss };
})();
