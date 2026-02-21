const App = (() => {
    let activeTab = 'positions';

    function init() {
        // Tab navigation
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });

        // Modal close on overlay click
        document.getElementById('modal-overlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) Positions.hideModal();
        });

        // Clock
        updateClock();
        setInterval(updateClock, 1000);

        // Initial data load
        Positions.load();
    }

    function switchTab(tab) {
        activeTab = tab;
        document.querySelectorAll('.tab-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === tab);
        });
        document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
        document.getElementById(`view-${tab}`).classList.remove('hidden');

        // Load data on tab switch
        if (tab === 'trades') Cycles.load();
        if (tab === 'fills') Trades.load();
        if (tab === 'balances') Balances.load();
    }

    function updateClock() {
        const el = document.getElementById('clock');
        if (el) {
            const now = new Date();
            el.textContent = now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
    }

    function toast(level, message) {
        const container = document.getElementById('toast-container');
        const el = document.createElement('div');
        el.className = `toast toast-${level}`;
        el.textContent = message;
        container.appendChild(el);
        setTimeout(() => el.remove(), 4000);
    }

    // Header PnL from positions snapshot
    WS.on('positions_snapshot', (positions) => {
        const el = document.getElementById('header-pnl');
        if (!el) return;
        if (!positions.length) {
            el.textContent = '--';
            el.className = 'font-bold tabular-nums text-gray-500';
            return;
        }
        const total = positions.reduce((sum, p) => sum + (parseFloat(p.pnl_usd) || 0), 0);
        const sign = total >= 0 ? '+' : '';
        el.textContent = `${sign}$${total.toFixed(2)}`;
        el.className = `font-bold tabular-nums ${total >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
    });

    // Notifications from WS
    WS.on('notification', (data) => toast(data.level, data.message));

    document.addEventListener('DOMContentLoaded', init);

    return { toast, switchTab };
})();
