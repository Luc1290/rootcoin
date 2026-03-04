const Opportunities = (() => {
    let _opportunities = [];
    let _dismissedIds = _loadDismissed();
    // Per-container chart tracking: { containerId: { oppId: chartId } }
    let _containerCharts = {};
    // Per-container previous keys for change detection
    let _prevKeys = {};

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

        // Change detection: skip full rebuild if same opportunities
        const newKey = items.map(o => o.id).join(',');
        if (_prevKeys[cid] === newKey && _containerCharts[cid] && Object.keys(_containerCharts[cid]).length === items.length) {
            return;
        }
        _prevKeys[cid] = newKey;

        const cards = items.map(o => {
            const sym = o.symbol.replace('USDC', '');
            const dirClass = o.direction === 'LONG' ? 'long' : 'short';
            // Use container-specific chart ID to avoid collisions between pages
            const chartContainerId = `${cid}-chart-${o.id}`;

            const lvl = o.levels || {};
            let levelsHtml = '';
            if (lvl.entry) {
                const entry = parseFloat(lvl.entry);
                const sl = parseFloat(lvl.sl);
                const tp1 = parseFloat(lvl.tp1);
                const tp2 = lvl.tp2 ? parseFloat(lvl.tp2) : 0;
                const isLong = o.direction === 'LONG';
                const slPct = entry ? ((isLong ? sl - entry : entry - sl) / entry * 100) : 0;
                const tpPct = entry ? ((isLong ? tp1 - entry : entry - tp1) / entry * 100) : 0;
                const tp2Pct = (entry && tp2) ? ((isLong ? tp2 - entry : entry - tp2) / entry * 100) : 0;
                const REF = 40000; // reference position $40k
                const fmtPnl = pct => {
                    const usd = pct / 100 * REF;
                    return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}% <b>${usd >= 0 ? '+' : '-'}$${Math.abs(usd).toFixed(0)}</b>`;
                };

                levelsHtml = `<div class="opp-levels">
                <span class="opp-lvl"><span style="color:#3b82f6">Entry</span> <span style="color:#3b82f6">${Utils.fmtPriceCompact(lvl.entry)}</span></span>
                <span class="opp-lvl"><span style="color:#ef4444">SL</span> <span style="color:#ef4444">${Utils.fmtPriceCompact(lvl.sl)}</span> <span style="color:#ef4444;opacity:0.8;font-size:10px">${fmtPnl(slPct)}</span></span>
                <span class="opp-lvl"><span style="color:#22c55e">TP</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp1)}</span> <span style="color:#22c55e;opacity:0.8;font-size:10px">${fmtPnl(tpPct)}</span></span>
                ${tp2 ? `<span class="opp-lvl"><span style="color:#22c55e">TP2</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp2)}</span> <span style="color:#22c55e;opacity:0.8;font-size:10px">${fmtPnl(tp2Pct)}</span></span>` : ''}
                <span class="opp-rr">R:R ${lvl.rr}</span>
                <span style="color:#6b7280;font-size:9px">/ $40k</span>
            </div>`;
            }

            const ago = Utils.timeAgoShort(o.detected_at);

            return `<div class="mini-chart-card ${dirClass}">
                <div class="flex items-center justify-between mb-1">
                    <div class="flex items-center gap-2">
                        <span class="text-sm font-bold">${sym}</span>
                        <span class="text-xs text-gray-500">${ago}</span>
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
            const score = o.score || 0;
            const strength = score >= 60 ? 'strong' : null;

            const chartId = MiniTradeChart.create(chartContainerId, {
                symbol: o.symbol,
                height: 140,
                entryPrice: lvl.entry ? parseFloat(lvl.entry) : 0,
                slPrice: lvl.sl ? parseFloat(lvl.sl) : 0,
                tpPrice: lvl.tp1 ? parseFloat(lvl.tp1) : 0,
            });

            if (chartId) {
                newCharts[o.id] = chartId;
                MiniTradeChart.addLabel(chartId, o.direction, strength);

                const timing = o.timing;
                if (timing) MiniTradeChart.addTiming(chartId, timing);

                MiniTradeChart.fetchAndRender(chartId, o.symbol, '5m', 288);
            }
        }
        _containerCharts[cid] = newCharts;
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

    return { update, render, dismiss };
})();
