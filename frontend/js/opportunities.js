const Opportunities = (() => {
    let _opportunities = [];
    let _knownIds = new Set();
    let _dismissedIds = new Set();

    function update(list) {
        if (!Array.isArray(list)) return;
        for (const o of list) {
            if (!_knownIds.has(o.id) && !_dismissedIds.has(o.id)) {
                const sym = o.symbol.replace('USDC', '');
                App.toast('warning', `Opportunit\u00e9 ${sym} ${o.direction} (${o.confidence}%)`);
            }
        }
        _opportunities = list;
        _knownIds = new Set(list.map(o => o.id));
    }

    function render(container) {
        if (!container) return;
        const visible = _opportunities.filter(o => !_dismissedIds.has(o.id));

        if (!visible.length) {
            container.innerHTML = '';
            return;
        }

        const cards = visible.slice(0, 3).map(o => {
            const sym = o.symbol.replace('USDC', '');
            const dirClass = o.direction === 'LONG' ? 'opp-long' : 'opp-short';
            const dirIcon = o.direction === 'LONG' ? '&#x2191;' : '&#x2193;';
            const signals = (o.key_signals || []).map(s => {
                const cls = s.type === 'bullish' ? 'opp-signal-bull'
                    : s.type === 'bearish' ? 'opp-signal-bear'
                    : 'opp-signal-level';
                return `<span class="opp-signal ${cls}">${s.label}</span>`;
            }).join('');
            const ago = _timeAgo(o.detected_at);

            return `<div class="opp-card ${dirClass}">
                <div class="flex items-center justify-between mb-1.5">
                    <div class="flex items-center gap-2">
                        <span class="text-sm font-bold">${sym}</span>
                        <span class="opp-direction ${dirClass}">${dirIcon} ${o.direction}</span>
                        <span class="opp-score">${o.score}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <span class="text-xs text-gray-500">${ago}</span>
                        <button class="opp-dismiss" onclick="Opportunities.dismiss('${o.id}')" title="Masquer">&#x2715;</button>
                    </div>
                </div>
                <p class="text-xs text-gray-300 leading-relaxed mb-1.5">${o.message}</p>
                <div class="flex flex-wrap gap-1">${signals}</div>
            </div>`;
        }).join('');

        container.innerHTML = `<div class="space-y-2">${cards}</div>`;
    }

    function dismiss(id) {
        _dismissedIds.add(id);
        const el = document.getElementById('cockpit-opportunities');
        if (el) render(el);
    }

    function _timeAgo(isoStr) {
        if (!isoStr) return '';
        const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
        if (diff < 60) return diff + 's';
        if (diff < 3600) return Math.floor(diff / 60) + 'min';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h';
        return Math.floor(diff / 86400) + 'd';
    }

    return { update, render, dismiss };
})();
