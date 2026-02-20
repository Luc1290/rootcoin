const Positions = (() => {
    const container = () => document.getElementById('positions-list');
    const empty = () => document.getElementById('positions-empty');
    let currentPositions = [];

    function render(positions) {
        currentPositions = positions;
        const list = container();
        const emptyEl = empty();

        if (!positions.length) {
            list.classList.add('hidden');
            emptyEl.classList.remove('hidden');
            return;
        }
        list.classList.remove('hidden');
        emptyEl.classList.add('hidden');

        list.innerHTML = positions.map(p => {
            const pnl = parseFloat(p.pnl_usd) || 0;
            const pnlPct = parseFloat(p.pnl_pct) || 0;
            const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            const sideClass = p.side === 'LONG' ? 'side-long' : 'side-short';
            const entry = parseFloat(p.entry_price) || 0;
            const current = parseFloat(p.current_price) || 0;
            const qty = parseFloat(p.quantity) || 0;
            const value = (current * qty).toFixed(2);

            const hasOrders = p.sl_order_id || p.tp_order_id || p.oco_order_list_id;
            const orderBadges = [];
            if (p.sl_order_id) orderBadges.push('<span class="text-xs bg-red-900/50 text-red-400 px-1.5 py-0.5 rounded">SL</span>');
            if (p.tp_order_id) orderBadges.push('<span class="text-xs bg-green-900/50 text-green-400 px-1.5 py-0.5 rounded">TP</span>');
            if (p.oco_order_list_id) orderBadges.push('<span class="text-xs bg-blue-900/50 text-blue-400 px-1.5 py-0.5 rounded">OCO</span>');

            return `
            <div class="position-card" data-id="${p.id}">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <span class="font-semibold">${p.symbol}</span>
                        <span class="text-xs font-bold ${sideClass}">${p.side}</span>
                        <span class="text-xs text-gray-500">${p.market_type.replace('_', ' ')}</span>
                    </div>
                    <span class="text-xs text-gray-500">${p.duration || ''}</span>
                </div>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm mb-3">
                    <div>
                        <div class="text-gray-500 text-xs">Entry</div>
                        <div>${formatPrice(entry)}</div>
                    </div>
                    <div>
                        <div class="text-gray-500 text-xs">Current</div>
                        <div>${formatPrice(current)}</div>
                    </div>
                    <div>
                        <div class="text-gray-500 text-xs">Value</div>
                        <div>$${value}</div>
                    </div>
                    <div>
                        <div class="text-gray-500 text-xs">PnL</div>
                        <div class="${pnlClass} font-semibold">
                            ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)
                        </div>
                    </div>
                </div>
                <div class="flex items-center gap-2 flex-wrap">
                    ${orderBadges.join('')}
                    <div class="flex-1"></div>
                    <button class="action-btn bg-yellow-600" onclick="Positions.showSL(${p.id})">SL</button>
                    <button class="action-btn bg-green-600" onclick="Positions.showTP(${p.id})">TP</button>
                    <button class="action-btn bg-blue-600" onclick="Positions.showOCO(${p.id})">OCO</button>
                    <button class="action-btn bg-red-600" onclick="Positions.confirmClose(${p.id})">Close</button>
                </div>
            </div>`;
        }).join('');
    }

    function formatPrice(p) {
        if (p >= 1000) return p.toFixed(2);
        if (p >= 1) return p.toFixed(4);
        return p.toFixed(6);
    }

    function showModal(title, body) {
        document.getElementById('modal-title').textContent = title;
        document.getElementById('modal-body').innerHTML = body;
        document.getElementById('modal-overlay').classList.add('show');
    }

    function hideModal() {
        document.getElementById('modal-overlay').classList.remove('show');
    }

    function showSL(id) {
        showModal('Stop Loss', `
            <input id="sl-price" type="number" step="any" placeholder="Prix SL"
                class="w-full bg-gray-800 border border-gray-600 rounded px-3 py-3 mb-4 text-base">
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitSL(${id})" class="action-btn bg-yellow-600 flex-1">Placer SL</button>
            </div>
        `);
    }

    function showTP(id) {
        showModal('Take Profit', `
            <input id="tp-price" type="number" step="any" placeholder="Prix TP"
                class="w-full bg-gray-800 border border-gray-600 rounded px-3 py-3 mb-4 text-base">
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitTP(${id})" class="action-btn bg-green-600 flex-1">Placer TP</button>
            </div>
        `);
    }

    function showOCO(id) {
        showModal('OCO (SL + TP)', `
            <input id="oco-tp" type="number" step="any" placeholder="Prix TP"
                class="w-full bg-gray-800 border border-gray-600 rounded px-3 py-3 mb-3 text-base">
            <input id="oco-sl" type="number" step="any" placeholder="Prix SL"
                class="w-full bg-gray-800 border border-gray-600 rounded px-3 py-3 mb-4 text-base">
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitOCO(${id})" class="action-btn bg-blue-600 flex-1">Placer OCO</button>
            </div>
        `);
    }

    function confirmClose(id) {
        const pos = currentPositions.find(p => p.id === id);
        const label = pos ? `${pos.symbol} ${pos.side}` : `#${id}`;
        showModal('Fermer la position', `
            <p class="text-gray-400 mb-4">Fermer <strong>${label}</strong> au marche ?</p>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitClose(${id})" class="action-btn bg-red-600 flex-1">Fermer</button>
            </div>
        `);
    }

    async function apiPost(url, body) {
        try {
            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Error');
            App.toast('success', 'Ordre place');
            hideModal();
        } catch (e) {
            App.toast('error', e.message);
        }
    }

    async function submitSL(id) {
        const price = document.getElementById('sl-price').value;
        if (!price) return;
        await apiPost(`/api/positions/${id}/sl`, { price });
    }

    async function submitTP(id) {
        const price = document.getElementById('tp-price').value;
        if (!price) return;
        await apiPost(`/api/positions/${id}/tp`, { price });
    }

    async function submitOCO(id) {
        const tp = document.getElementById('oco-tp').value;
        const sl = document.getElementById('oco-sl').value;
        if (!tp || !sl) return;
        await apiPost(`/api/positions/${id}/oco`, { tp_price: tp, sl_price: sl });
    }

    async function submitClose(id) {
        await apiPost(`/api/positions/${id}/close`, {});
    }

    // Real-time updates
    WS.on('positions_snapshot', render);

    // Initial load via REST
    async function load() {
        try {
            const resp = await fetch('/api/positions');
            const data = await resp.json();
            render(data);
        } catch (e) {
            console.error('Failed to load positions', e);
        }
    }

    return {
        load, render, showSL, showTP, showOCO, confirmClose,
        submitSL, submitTP, submitOCO, submitClose, hideModal,
    };
})();
