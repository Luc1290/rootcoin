const Notifications = (() => {
    let _entries = [];
    let _offset = 0;
    const PAGE_SIZE = 50;

    async function load() {
        _offset = 0;
        _entries = [];
        await _fetch();
        render();
        _bindControls();
    }

    async function _fetch() {
        const filter = document.getElementById('notif-type-filter')?.value || '';
        const url = '/api/notifications?limit=' + PAGE_SIZE + '&offset=' + _offset +
            (filter ? '&type=' + filter : '');
        try {
            const resp = await fetch(url);
            if (!resp.ok) return;
            const data = await resp.json();
            const items = data.notifications || [];
            _entries = _entries.concat(items);
            const btn = document.getElementById('notif-load-more');
            if (btn) btn.classList.toggle('hidden', items.length < PAGE_SIZE);
        } catch (e) { /* ignore */ }
    }

    function render() {
        const el = document.getElementById('notif-timeline');
        const empty = document.getElementById('notif-empty');
        if (!el) return;
        if (!_entries.length) {
            el.innerHTML = '';
            if (empty) empty.classList.remove('hidden');
            return;
        }
        if (empty) empty.classList.add('hidden');

        // Group by date
        const groups = {};
        for (const n of _entries) {
            const d = n.created_at ? n.created_at.slice(0, 10) : 'unknown';
            (groups[d] = groups[d] || []).push(n);
        }

        let html = '';
        for (const [date, items] of Object.entries(groups)) {
            const label = _formatDate(date);
            html += '<div class="mb-1">';
            html += '<div class="text-[9px] text-gray-500 font-medium mb-0.5 uppercase">' + label + '</div>';
            html += items.map(_cardHtml).join('');
            html += '</div>';
        }
        el.innerHTML = html;

        // Stats
        const statsEl = document.getElementById('notif-stats');
        if (statsEl) {
            const today = new Date().toISOString().slice(0, 10);
            const todayCount = (groups[today] || []).length;
            statsEl.textContent = todayCount ? todayCount + "j" : '';
        }
        _bindControls();
    }

    function _cardHtml(n) {
        const time = _formatTime(n.created_at);
        const changePct = parseFloat(n.change_pct);
        const sign = changePct > 0 ? '+' : '';
        const color = changePct > 0 ? 'text-emerald-400' : 'text-red-400';
        const base = n.symbol.replace('USDC', '').replace('USDT', '');

        const typeBadge = n.type === 'momentum'
            ? '<span class="px-0.5 text-[8px] font-bold text-blue-400">M</span>'
            : '<span class="px-0.5 text-[8px] font-bold text-purple-400">E</span>';

        return '<div class="flex items-center gap-1 px-1.5 py-1 bg-stone-800/30 rounded text-[10px]">' +
            typeBadge +
            ' <span class="font-bold text-gray-200">' + base + '</span>' +
            ' <span class="' + color + ' font-medium tabular-nums">' + sign + changePct.toFixed(1) + '%</span>' +
            ' <span class="text-gray-600 text-[9px] ml-auto">' + time + '</span>' +
            '</div>';
    }

    function _formatDate(dateStr) {
        const today = new Date().toISOString().slice(0, 10);
        if (dateStr === today) return "Aujourd'hui";
        const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
        if (dateStr === yesterday) return 'Hier';
        const d = new Date(dateStr + 'T00:00:00Z');
        return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
    }

    function _formatTime(isoStr) {
        if (!isoStr) return '';
        const d = new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z');
        return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    }

    async function clearAll() {
        try {
            const resp = await fetch('/api/notifications', { method: 'DELETE' });
            if (!resp.ok) return;
            _entries = [];
            render();
        } catch (e) { /* ignore */ }
    }

    function _bindControls() {
        const sel = document.getElementById('notif-type-filter');
        if (sel) sel.onchange = () => load();
        const btn = document.getElementById('notif-load-more');
        if (btn) btn.onclick = async () => {
            _offset += PAGE_SIZE;
            await _fetch();
            render();
        };
        const clr = document.getElementById('notif-clear-all');
        if (clr) clr.onclick = () => clearAll();
    }

    // Live WS updates
    WS.on('notification_log', (data) => {
        _entries.unshift(data);
        if (!document.getElementById('view-heatmap')?.classList.contains('hidden')) {
            render();
        }
    });

    return { load, render, clearAll };
})();
