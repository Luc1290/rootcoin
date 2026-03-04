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

        // Check which symbols have active positions
        const activeSymbols = (typeof Positions !== 'undefined' && Positions.getActiveSymbols)
            ? Positions.getActiveSymbols() : {};

        // Change detection: skip full rebuild if same opportunities + same taken state
        const takenKey = Object.keys(activeSymbols).sort().join(',');
        const newKey = items.map(o => o.id).join(',') + '|' + takenKey;
        if (_prevKeys[cid] === newKey && _containerCharts[cid] && Object.keys(_containerCharts[cid]).length === items.length) {
            return;
        }
        _prevKeys[cid] = newKey;

        const cards = items.map(o => {
            const sym = o.symbol.replace('USDC', '');
            const dirClass = o.direction === 'LONG' ? 'long' : 'short';
            const chartContainerId = `${cid}-chart-${o.id}`;

            // Taken badge
            const takenSide = activeSymbols[o.symbol];
            let takenBadge = '';
            if (takenSide) {
                const badgeColor = takenSide === 'LONG' ? '#22c55e' : '#ef4444';
                takenBadge = `<span style="background:${badgeColor};color:#fff;font-size:9px;padding:1px 5px;border-radius:3px;font-weight:600">${takenSide}</span>`;
            }

            const lvl = o.levels || {};
            let levelsHtml = '';
            if (lvl.entry) {
                levelsHtml = `<div class="opp-levels">
                <span class="opp-lvl"><span style="color:#3b82f6">Entry</span> <span style="color:#3b82f6">${Utils.fmtPriceCompact(lvl.entry)}</span></span>
                <span class="opp-lvl"><span style="color:#ef4444">SL</span> <span style="color:#ef4444">${Utils.fmtPriceCompact(lvl.sl)}</span></span>
                <span class="opp-lvl"><span style="color:#22c55e">TP</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp1)}</span></span>
                ${lvl.tp2 ? `<span class="opp-lvl"><span style="color:#22c55e">TP2</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp2)}</span></span>` : ''}
                <span class="opp-rr">R:R ${lvl.rr}</span>
            </div>`;
            }

            const ago = Utils.timeAgoShort(o.detected_at);

            return `<div class="mini-chart-card ${dirClass}">
                <div class="flex items-center justify-between mb-1">
                    <div class="flex items-center gap-2">
                        <span class="text-sm font-bold">${sym}</span>
                        ${takenBadge}
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
