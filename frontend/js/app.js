const App = (() => {
    let activeTab = 'positions';
    const validTabs = ['positions', 'trades', 'fills', 'balances', 'chart'];

    function init() {
        // Tab navigation — links allow middle-click / long-press "Open in new tab"
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                switchTab(btn.dataset.tab);
            });
        });

        // Modal close on overlay click
        document.getElementById('modal-overlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) Positions.hideModal();
        });

        // Clock
        updateClock();
        setInterval(updateClock, 1000);

        // Read ?tab= from URL
        const params = new URLSearchParams(window.location.search);
        const urlTab = params.get('tab');
        if (urlTab && validTabs.includes(urlTab)) {
            activeTab = urlTab;
        }

        // Apply initial tab
        switchTab(activeTab);

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

        // Update URL without reload
        const url = new URL(window.location);
        url.searchParams.set('tab', tab);
        history.replaceState(null, '', url);

        // Load data on tab switch
        if (tab === 'trades') Cycles.load();
        if (tab === 'fills') Trades.load();
        if (tab === 'balances') Balances.load();
        if (tab === 'chart') { KlineChart.init(); KlineChart.loadChart(); }
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

    // Notifications from WS
    WS.on('notification', (data) => toast(data.level, data.message));

    document.addEventListener('DOMContentLoaded', init);

    return { toast, switchTab };
})();
