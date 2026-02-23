const App = (() => {
    let activeTab = 'cockpit';
    const validTabs = ['cockpit', 'positions', 'trades', 'fills', 'balances', 'chart', 'analysis', 'heatmap', 'health'];

    function init() {
        // Tab navigation — links allow middle-click / long-press "Open in new tab"
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                switchTab(btn.dataset.tab);
                _closeDrawer();
            });
        });

        // Burger menu
        const burgerBtn = document.getElementById('burger-btn');
        const drawer = document.getElementById('nav-drawer');
        const overlay = document.getElementById('nav-drawer-overlay');
        if (burgerBtn && drawer && overlay) {
            burgerBtn.addEventListener('click', () => {
                drawer.classList.toggle('open');
                overlay.classList.toggle('hidden');
            });
            overlay.addEventListener('click', _closeDrawer);
        }

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
        if (tab === 'cockpit') Cockpit.load();
        if (tab === 'trades') { Cycles.load(); Cycles.startPolling(); } else { Cycles.stopPolling(); }
        if (tab === 'fills') Trades.load();
        if (tab === 'balances') Balances.load();
        if (tab === 'chart') { KlineChart.init(); KlineChart.loadChart(); }
        if (tab === 'analysis') Analysis.load();
        if (tab === 'heatmap') { Heatmap.init(); Heatmap.load(); Heatmap.startPolling(); } else { Heatmap.stopPolling(); }
        if (tab === 'health') { Health.init(); Health.load(); Health.startPolling(); } else { Health.stopPolling(); }
    }

    function _closeDrawer() {
        const drawer = document.getElementById('nav-drawer');
        const overlay = document.getElementById('nav-drawer-overlay');
        if (drawer) drawer.classList.remove('open');
        if (overlay) overlay.classList.add('hidden');
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
