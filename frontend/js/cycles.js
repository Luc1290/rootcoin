const Cycles = (() => {
    let currentOffset = 0;
    const PAGE_SIZE = 50;
    let _pollInterval = null;
    const POLL_DELAY = 30_000;

    function formatPrice(p) {
        if (p >= 1000) return p.toFixed(2);
        if (p >= 1) return p.toFixed(4);
        return p.toFixed(6);
    }

    function _capStr(pnl) {
        const total = BalanceStore.getTotal();
        if (!total) return '';
        const pct = pnl / total * 100;
        const sign = pct >= 0 ? '+' : '';
        return ` | ${sign}${pct.toFixed(2)}% solde`;
    }

    function formatDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })
            + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    }

    function buildCard(c) {
        const isOpen = c.is_active;
        const sideClass = c.side === 'LONG' ? 'side-long' : 'side-short';
        const sideBg = c.side === 'LONG' ? 'bg-emerald-900/30 text-emerald-400' : 'bg-red-900/30 text-red-400';
        const entry = parseFloat(c.entry_price) || 0;
        const qty = parseFloat(c.quantity) || 0;
        const fees = parseFloat(c.total_fees_usd) || 0;

        let pnlValue, pnlPct, priceLabel, priceValue, statusBadge, cardBorder, grossPnl = null;

        if (isOpen) {
            const current = parseFloat(c.current_price || 0) || 0;
            const unrealized = parseFloat(c.pnl_usd) || 0;
            const unrealizedPct = parseFloat(c.pnl_pct) || 0;
            pnlValue = unrealized;
            pnlPct = unrealizedPct;
            priceLabel = 'Current';
            priceValue = current > 0 ? formatPrice(current) : '--';
            statusBadge = '<span class="badge bg-blue-900/40 text-blue-400">Ouvert</span>';
            cardBorder = 'cycle-open';
        } else {
            const exit = parseFloat(c.exit_price) || 0;
            grossPnl = parseFloat(c.realized_pnl) || 0;
            const realPct = parseFloat(c.realized_pnl_pct) || 0;
            const netPnl = grossPnl - fees;
            pnlValue = netPnl;
            pnlPct = realPct;
            priceLabel = 'Exit';
            priceValue = exit > 0 ? formatPrice(exit) : '--';
            statusBadge = pnlValue >= 0
                ? '<span class="badge bg-emerald-900/40 text-emerald-400">Win</span>'
                : '<span class="badge bg-red-900/40 text-red-400">Loss</span>';
            cardBorder = pnlValue >= 0 ? 'cycle-win' : 'cycle-loss';
        }

        const pnlClass = pnlValue >= 0 ? 'pnl-positive' : 'pnl-negative';
        const pnlSign = pnlValue >= 0 ? '+' : '';
        const pctSign = pnlPct >= 0 ? '+' : '';
        const notional = (entry * qty).toFixed(2);
        const marketLabel = c.market_type.replace('_MARGIN', '').replace('CROSS', 'Cross').replace('ISOLATED', 'Isolated');

        return `
        <div class="cycle-card ${cardBorder}">
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                    <span class="font-bold">${c.symbol}</span>
                    <span class="badge ${sideBg}">${c.side}</span>
                    <span class="text-xs text-gray-500 font-medium">${marketLabel}</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-xs text-gray-500 tabular-nums">${c.duration || ''}</span>
                    ${statusBadge}
                </div>
            </div>
            <div class="grid grid-cols-4 gap-2 text-sm">
                <div>
                    <div class="metric-label mb-0.5">Entry</div>
                    <div class="font-medium tabular-nums">${formatPrice(entry)}</div>
                </div>
                <div>
                    <div class="metric-label mb-0.5">${priceLabel}</div>
                    <div class="font-medium tabular-nums">${priceValue}</div>
                </div>
                <div>
                    <div class="metric-label mb-0.5">Brut</div>
                    <div class="${grossPnl !== null ? (grossPnl >= 0 ? 'pnl-positive' : 'pnl-negative') : pnlClass} font-medium tabular-nums">
                        ${grossPnl !== null ? `${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)}` : '--'}
                    </div>
                </div>
                <div>
                    <div class="metric-label mb-0.5">Net</div>
                    <div class="${pnlClass} font-bold tabular-nums">
                        ${pnlSign}$${pnlValue.toFixed(2)}
                    </div>
                    <div class="text-gray-600 text-xs tabular-nums">${pctSign}${pnlPct.toFixed(2)}% position${_capStr(pnlValue)}</div>
                </div>
            </div>
            <div class="flex items-center justify-between mt-2 text-xs text-gray-500">
                <span class="tabular-nums">$${notional} | fees $${fees.toFixed(2)}</span>
                <div class="flex items-center gap-2">
                    <span class="tabular-nums">${formatDate(c.opened_at)}${c.closed_at ? ' → ' + formatDate(c.closed_at) : ''}</span>
                    ${!isOpen ? `<button onclick="event.stopPropagation();Cycles.deleteCycle(${c.id})" class="text-gray-600 hover:text-red-400 transition-colors" title="Supprimer ce cycle">&times;</button>` : ''}
                </div>
            </div>
        </div>`;
    }

    function render(cycles) {
        const list = document.getElementById('cycles-list');
        const empty = document.getElementById('cycles-empty');
        if (!cycles.length) {
            list.innerHTML = '';
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');
        list.innerHTML = cycles.map(buildCard).join('');
    }

    async function loadStats(symbol) {
        try {
            const url = symbol ? `/api/cycles/stats?symbol=${symbol}` : '/api/cycles/stats';
            const resp = await fetch(url);
            const s = await resp.json();
            const wr = document.getElementById('stat-winrate');
            const tp = document.getElementById('stat-total-pnl');
            const tc = document.getElementById('stat-total-cycles');
            if (wr) wr.textContent = s.total_cycles > 0 ? `${s.win_rate}%` : '--';
            if (tc) tc.textContent = s.total_cycles > 0 ? `${s.total_cycles} (${s.wins}W/${s.losses}L)` : '--';
            if (tp) {
                const pnl = parseFloat(s.total_pnl) || 0;
                tp.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
                tp.className = `font-bold text-base ${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
            }
        } catch (e) {
            console.error('Failed to load cycle stats', e);
        }
    }

    async function load(symbol, status) {
        currentOffset = 0;
        const f = document.getElementById('cycles-filter');
        const s = document.getElementById('cycles-status');
        symbol = symbol ?? (f ? f.value : '');
        status = status ?? (s ? s.value : '');
        try {
            let url = `/api/cycles?limit=${PAGE_SIZE}&offset=0`;
            if (symbol) url += `&symbol=${symbol}`;
            if (status) url += `&status=${status}`;
            const resp = await fetch(url);
            const data = await resp.json();
            render(data);
            currentOffset = data.length;
        } catch (e) {
            console.error('Failed to load cycles', e);
        }
        loadStats(symbol);
    }

    document.addEventListener('DOMContentLoaded', () => {
        const f = document.getElementById('cycles-filter');
        const s = document.getElementById('cycles-status');
        if (f) f.addEventListener('change', () => load());
        if (s) s.addEventListener('change', () => load());
    });

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

    async function deleteCycle(id) {
        if (!confirm('Supprimer ce cycle ?')) return;
        try {
            const resp = await fetch(`/api/cycles/${id}`, { method: 'DELETE' });
            if (resp.ok) {
                App.toast('Cycle supprimé', 'success');
                load();
            } else {
                const err = await resp.json();
                App.toast(err.detail || 'Erreur', 'error');
            }
        } catch (e) {
            App.toast('Erreur réseau', 'error');
        }
    }

    return { load, startPolling, stopPolling, deleteCycle };
})();
