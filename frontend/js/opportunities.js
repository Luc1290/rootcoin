const Opportunities = (() => {
    let _opportunities = [];
    let _dismissedIds = _loadDismissed();
    let _chartIds = {};

    function update(list) {
        if (!Array.isArray(list)) return;
        _opportunities = list;
    }

    function render(container) {
        if (!container) return;
        const visible = _opportunities.filter(o => !_dismissedIds.has(o.id));

        if (!visible.length) {
            _destroyCharts();
            container.innerHTML = '';
            return;
        }

        const keepIds = new Set();
        const cards = visible.slice(0, 3).map(o => {
            const sym = o.symbol.replace('USDC', '');
            const dirClass = o.direction === 'LONG' ? 'long' : 'short';
            const chartContainerId = `opp-chart-${o.id}`;
            keepIds.add(o.id);

            const lvl = o.levels || {};
            const levelsHtml = lvl.entry ? `<div class="opp-levels">
                <span class="opp-lvl"><span style="color:#3b82f6">Entry</span> <span style="color:#3b82f6">${Utils.fmtPriceCompact(lvl.entry)}</span></span>
                <span class="opp-lvl"><span style="color:#ef4444">SL</span> <span style="color:#ef4444">${Utils.fmtPriceCompact(lvl.sl)}</span></span>
                <span class="opp-lvl"><span style="color:#22c55e">TP</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp1)}</span></span>
                ${lvl.tp2 ? `<span class="opp-lvl"><span style="color:#22c55e">TP2</span> <span style="color:#22c55e">${Utils.fmtPriceCompact(lvl.tp2)}</span></span>` : ''}
                <span class="opp-rr">R:R ${lvl.rr}</span>
            </div>` : '';

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

        // Destroy all existing charts before rewriting DOM
        _destroyCharts();

        container.innerHTML = `<div class="space-y-2">${cards}</div>`;

        // Create charts for visible opportunities
        for (const o of visible.slice(0, 3)) {
            const chartContainerId = `opp-chart-${o.id}`;
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
                _chartIds[o.id] = chartId;
                MiniTradeChart.addLabel(chartId, o.direction, strength);

                const timing = o.timing;
                if (timing) MiniTradeChart.addTiming(chartId, timing);

                MiniTradeChart.fetchAndRender(chartId, o.symbol, '5m', 24);
            }
        }
    }

    function dismiss(id) {
        _dismissedIds.add(id);
        _saveDismissed();
        if (_chartIds[id]) {
            MiniTradeChart.destroy(_chartIds[id]);
            delete _chartIds[id];
        }
        const el = document.getElementById('cockpit-opportunities');
        if (el) render(el);
    }

    function _destroyCharts() {
        for (const [oppId, chartId] of Object.entries(_chartIds)) {
            MiniTradeChart.destroy(chartId);
        }
        _chartIds = {};
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
