const Alerts = (() => {
    let _alerts = [];
    let _currentSymbol = 'BTCUSDC';
    let _currentPrice = null;
    let _initialized = false;

    function init() {
        if (_initialized) return;
        _initialized = true;

        const btn = document.getElementById('alert-add-btn');
        if (btn) btn.addEventListener('click', _addAlert);

        const input = document.getElementById('alert-price-input');
        if (input) input.addEventListener('keydown', e => {
            if (e.key === 'Enter') _addAlert();
        });

        const sel = document.getElementById('chart-symbol');
        if (sel) sel.addEventListener('change', () => {
            _currentSymbol = sel.value;
            load();
        });

        WS.on('price_update', msg => {
            if (msg.s === _currentSymbol) _currentPrice = parseFloat(msg.c);
        });
    }

    async function load() {
        try {
            const resp = await fetch(`/api/alerts?symbol=${_currentSymbol}`);
            if (!resp.ok) return;
            _alerts = await resp.json();
            _render();
        } catch { /* ignore */ }
    }

    function setSymbol(sym) {
        _currentSymbol = sym;
        load();
    }

    function getAlerts() {
        return _alerts;
    }

    function _render() {
        const list = document.getElementById('alert-list');
        if (!list) return;
        if (!_alerts.length) {
            list.innerHTML = '<span class="text-gray-600">Aucune alerte</span>';
            _updateChartLines();
            return;
        }
        list.innerHTML = _alerts.map(a => {
            const dir = a.direction === 'above' ? '↑' : '↓';
            const color = a.direction === 'above' ? 'text-green-400' : 'text-red-400';
            const price = Utils.fmtPrice(a.target_price);
            const note = a.note ? ` — ${a.note}` : '';
            return `<div class="flex items-center justify-between py-1 px-2 rounded bg-gray-800/50">
                <span class="${color} font-medium">${dir} ${price}${note}</span>
                <button onclick="Alerts.remove(${a.id})" class="text-gray-500 hover:text-red-400 text-xs px-1">✕</button>
            </div>`;
        }).join('');
        _updateChartLines();
    }

    function _updateChartLines() {
        if (typeof KlineChart !== 'undefined' && KlineChart.renderAlertLines) {
            KlineChart.renderAlertLines();
        }
    }

    async function _addAlert() {
        const input = document.getElementById('alert-price-input');
        const dirSel = document.getElementById('alert-direction');
        if (!input) return;

        const target = parseFloat(input.value);
        if (!target || target <= 0) {
            if (typeof App !== 'undefined') App.toast('Prix invalide', 'error');
            return;
        }

        let direction = dirSel ? dirSel.value : 'auto';
        if (direction === 'auto') {
            direction = _currentPrice && target > _currentPrice ? 'above' : 'below';
        }

        try {
            const resp = await fetch('/api/alerts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: _currentSymbol,
                    target_price: target.toString(),
                    direction,
                }),
            });
            const data = await resp.json();
            if (data.error) {
                if (typeof App !== 'undefined') App.toast(data.error, 'error');
                return;
            }
            input.value = '';
            if (typeof App !== 'undefined') App.toast(`Alerte ${direction === 'above' ? '↑' : '↓'} ${Utils.fmtPrice(target)}`, 'success');
            await load();
        } catch {
            if (typeof App !== 'undefined') App.toast('Erreur creation alerte', 'error');
        }
    }

    async function remove(id) {
        try {
            await fetch(`/api/alerts/${id}`, { method: 'DELETE' });
            await load();
        } catch { /* ignore */ }
    }

    return { init, load, setSymbol, getAlerts, remove };
})();
