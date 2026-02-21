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
            Charts.cleanup([]);
            return;
        }
        list.classList.remove('hidden');
        emptyEl.classList.add('hidden');

        const activeIds = positions.map(p => p.id);
        Charts.cleanup(activeIds);

        // Index existing cards by data-id
        const existing = {};
        list.querySelectorAll('.position-card').forEach(card => {
            existing[card.dataset.id] = card;
        });

        // Remove cards for closed positions
        for (const id of Object.keys(existing)) {
            if (!activeIds.includes(Number(id))) {
                existing[id].remove();
            }
        }

        // Update or create cards
        positions.forEach(p => {
            const card = existing[p.id];
            if (card) {
                PositionCards.updateCardData(card, p);
            } else {
                const tmp = document.createElement('div');
                tmp.innerHTML = PositionCards.buildCardHtml(p);
                const newCard = tmp.firstElementChild;
                list.appendChild(newCard);
                Charts.createMiniChart(`chart-pos-${p.id}`, p.id, p.symbol, {
                    entryPrice: parseFloat(p.entry_price) || 0,
                    openedAt: p.opened_at,
                    side: p.side,
                });
            }
        });
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

    function confirmCancelOrders(id) {
        const pos = currentPositions.find(p => p.id === id);
        if (!pos) return;
        const types = [];
        if (pos.sl_order_id) types.push('SL');
        if (pos.tp_order_id) types.push('TP');
        if (pos.oco_order_list_id) types.push('OCO');
        showModal('Annuler les ordres', `
            <p class="text-gray-400 mb-4">Annuler ${types.join(' + ')} sur <strong>${pos.symbol}</strong> ?</p>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Retour</button>
                <button onclick="Positions.submitCancelOrders(${id})" class="action-btn bg-orange-600 flex-1">Annuler ordres</button>
            </div>
        `);
    }

    async function submitCancelOrders(id) {
        await apiPost(`/api/positions/${id}/cancel-orders`, {});
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
        confirmCancelOrders, submitCancelOrders,
    };
})();
