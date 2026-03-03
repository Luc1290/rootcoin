const Positions = (() => {
    const container = () => document.getElementById('positions-list');
    const empty = () => document.getElementById('positions-empty');
    const toolbar = () => document.getElementById('positions-toolbar');
    const sortSelect = () => document.getElementById('positions-sort');
    let currentPositions = [];

    function _sortPositions(positions) {
        const sel = sortSelect();
        const key = sel ? sel.value : 'pnl';
        const sorted = [...positions];
        switch (key) {
            case 'pnl':
                sorted.sort((a, b) => (parseFloat(b.pnl_usd) || 0) - (parseFloat(a.pnl_usd) || 0));
                break;
            case 'value': {
                const val = p => (parseFloat(p.current_price) || 0) * (parseFloat(p.quantity) || 0);
                sorted.sort((a, b) => val(b) - val(a));
                break;
            }
            case 'duration':
                sorted.sort((a, b) => (a.opened_at || '').localeCompare(b.opened_at || ''));
                break;
            case 'symbol':
                sorted.sort((a, b) => a.symbol.localeCompare(b.symbol));
                break;
        }
        return sorted;
    }

    function _updateHeaderPnl(positions) {
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

        _updateToolbarPortfolio(total);
    }

    function _updateToolbarPortfolio(totalPnl) {
        const totalEl = document.getElementById('pos-portfolio-total');
        const pnlEl = document.getElementById('pos-portfolio-pnl');
        if (!totalEl || !pnlEl) return;

        const portfolioTotal = BalanceStore.getTotal();
        totalEl.textContent = portfolioTotal !== null
            ? '$' + portfolioTotal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : '--';

        const sign = totalPnl >= 0 ? '+' : '';
        pnlEl.textContent = `${sign}$${Math.abs(totalPnl).toFixed(2)}`;
        pnlEl.className = `font-bold tabular-nums ${totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
    }

    function render(positions) {
        currentPositions = positions;
        _updateHeaderPnl(positions);
        const list = container();
        const emptyEl = empty();
        const tb = toolbar();

        if (!positions.length) {
            list.classList.add('hidden');
            emptyEl.classList.remove('hidden');
            if (tb) tb.classList.add('hidden');
            Charts.cleanup([]);
            return;
        }
        list.classList.remove('hidden');
        emptyEl.classList.add('hidden');
        if (tb) tb.classList.remove('hidden');

        const sorted = _sortPositions(positions);
        const activeIds = sorted.map(p => p.id);
        Charts.cleanup(activeIds);

        const existing = {};
        list.querySelectorAll('.position-card').forEach(card => {
            existing[card.dataset.id] = card;
        });

        for (const id of Object.keys(existing)) {
            if (!activeIds.includes(Number(id))) {
                existing[id].remove();
            }
        }

        // Update or create cards, then reorder DOM to match sort
        sorted.forEach(p => {
            const card = existing[p.id];
            if (card) {
                PositionCards.updateCardData(card, p);
                Charts.updateOrderLines(p.id, p.sl_price, p.tp_price);
            } else {
                const tmp = document.createElement('div');
                tmp.innerHTML = PositionCards.buildCardHtml(p);
                const newCard = tmp.firstElementChild;
                list.appendChild(newCard);
                Charts.createMiniChart(`chart-pos-${p.id}`, p.id, p.symbol, {
                    entryPrice: parseFloat(p.entry_price) || 0,
                    openedAt: p.opened_at,
                    side: p.side,
                    slPrice: parseFloat(p.sl_price) || 0,
                    tpPrice: parseFloat(p.tp_price) || 0,
                });
            }
        });

        // Reorder DOM only if order actually changed
        const currentOrder = [...list.querySelectorAll('.position-card')].map(c => c.dataset.id);
        const targetOrder = sorted.map(p => String(p.id));
        if (currentOrder.length !== targetOrder.length || currentOrder.some((id, i) => id !== targetOrder[i])) {
            sorted.forEach(p => {
                const card = list.querySelector(`.position-card[data-id="${p.id}"]`);
                if (card) list.appendChild(card);
            });
        }
    }

    function showModal(title, body) {
        document.getElementById('modal-title').textContent = title;
        document.getElementById('modal-body').innerHTML = body;
        document.getElementById('modal-overlay').classList.add('show');
    }

    function hideModal() {
        document.getElementById('modal-overlay').classList.remove('show');
    }

    // --- % / $ toggle helpers ---

    function _getPos(id) {
        return currentPositions.find(p => p.id === id);
    }

    function _pctToPrice(pct, entryPrice, side, orderType) {
        // SL LONG: entry * (1 - pct/100), SL SHORT: entry * (1 + pct/100)
        // TP LONG: entry * (1 + pct/100), TP SHORT: entry * (1 - pct/100)
        const isBelow = (orderType === 'SL' && side === 'LONG') || (orderType === 'TP' && side === 'SHORT');
        return isBelow ? entryPrice * (1 - pct / 100) : entryPrice * (1 + pct / 100);
    }

    function _toggleHtml(prefix) {
        return `
            <div class="flex gap-1 mb-3" id="${prefix}-mode-toggle">
                <button type="button" class="mode-btn active flex-1 text-sm py-1.5 rounded font-medium" data-mode="price" onclick="Positions._setMode('${prefix}','price')">Prix $</button>
                <button type="button" class="mode-btn flex-1 text-sm py-1.5 rounded font-medium" data-mode="pct" onclick="Positions._setMode('${prefix}','pct')">%</button>
            </div>`;
    }

    function _setMode(prefix, mode) {
        const toggle = document.getElementById(`${prefix}-mode-toggle`);
        if (!toggle) return;
        toggle.querySelectorAll('.mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        // Single input (SL/TP)
        const input = document.getElementById(`${prefix}-input`);
        if (input) {
            input.value = '';
            input.placeholder = mode === 'pct' ? 'Distance %' : `Prix ${prefix.toUpperCase()}`;
        }
        // Dual inputs (OCO)
        const tpInput = document.getElementById(`${prefix}-tp-input`);
        const slInput = document.getElementById(`${prefix}-sl-input`);
        if (tpInput) { tpInput.value = ''; tpInput.placeholder = mode === 'pct' ? 'TP %' : 'Prix TP'; }
        if (slInput) { slInput.value = ''; slInput.placeholder = mode === 'pct' ? 'SL %' : 'Prix SL'; }
    }

    function _getMode(prefix) {
        const toggle = document.getElementById(`${prefix}-mode-toggle`);
        if (!toggle) return 'price';
        const active = toggle.querySelector('.mode-btn.active');
        return active ? active.dataset.mode : 'price';
    }

    function _resolvePrice(inputId, prefix, pos, orderType) {
        const raw = parseFloat(document.getElementById(inputId).value);
        if (!raw || isNaN(raw)) return null;
        if (_getMode(prefix) === 'pct') {
            const entry = parseFloat(pos.entry_price) || 0;
            if (!entry) return null;
            return _pctToPrice(raw, entry, pos.side, orderType);
        }
        return raw;
    }

    // --- Modals ---

    function showSL(id) {
        showModal('Stop Loss', `
            ${_toggleHtml('sl')}
            <input id="sl-input" type="number" step="any" placeholder="Prix SL"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRisk(${id},'sl')">
            <div id="sl-risk" class="text-center text-sm text-gray-400 mb-4">Risque --</div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitSL(${id})" class="action-btn bg-yellow-600 flex-1">Placer SL</button>
            </div>
        `);
    }

    function showTP(id) {
        showModal('Take Profit', `
            ${_toggleHtml('tp')}
            <input id="tp-input" type="number" step="any" placeholder="Prix TP"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRisk(${id},'tp')">
            <div id="tp-risk" class="text-center text-sm text-gray-400 mb-4">Gain --</div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitTP(${id})" class="action-btn bg-emerald-600 flex-1">Placer TP</button>
            </div>
        `);
    }

    function showOCO(id) {
        const pos = _getPos(id);
        showModal('OCO (SL + TP)', `
            ${_toggleHtml('oco')}
            <input id="oco-tp-input" type="number" step="any" placeholder="Prix TP"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRR(${id})">
            <input id="oco-sl-input" type="number" step="any" placeholder="Prix SL"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRR(${id})">
            <div id="oco-rr" class="text-center text-sm text-gray-400 mb-4">R:R --</div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitOCO(${id})" class="action-btn bg-blue-600 flex-1">Placer OCO</button>
            </div>
        `);
    }

    function _updateRisk(id, type) {
        const elId = type === 'sl' ? 'sl-risk' : 'tp-risk';
        const el = document.getElementById(elId);
        if (!el) return;
        const pos = _getPos(id);
        if (!pos) return;

        const entry = parseFloat(pos.entry_price) || 0;
        const qty = parseFloat(pos.quantity) || 0;
        const raw = parseFloat(document.getElementById(`${type}-input`).value);
        if (!raw || !entry || !qty) {
            el.textContent = type === 'sl' ? 'Risque --' : 'Gain --';
            el.className = 'text-center text-sm text-gray-400 mb-4';
            return;
        }

        const mode = _getMode(type);
        const price = mode === 'pct' ? _pctToPrice(raw, entry, pos.side, type.toUpperCase()) : raw;

        let delta;
        if (pos.side === 'LONG') {
            delta = (price - entry) * qty;
        } else {
            delta = (entry - price) * qty;
        }
        const pct = ((price - entry) / entry * 100);
        const absPct = Math.abs(pct).toFixed(2);

        const mirror = mode === 'pct'
            ? `<div class="text-xs text-gray-500 mt-0.5">Prix: ${Utils.fmtPrice(price)}</div>`
            : `<div class="text-xs text-gray-500 mt-0.5">${absPct}% depuis l'entree</div>`;

        if (type === 'sl') {
            const loss = Math.abs(delta);
            el.innerHTML = `<div>Risque -$${loss.toFixed(2)} (${absPct}%)</div>${mirror}`;
            el.className = 'text-center text-sm font-semibold text-red-400 mb-4';
        } else {
            const gain = Math.abs(delta);
            el.innerHTML = `<div>Gain +$${gain.toFixed(2)} (${absPct}%)</div>${mirror}`;
            el.className = 'text-center text-sm font-semibold text-emerald-400 mb-4';
        }
    }

    function _updateRR(id) {
        const rrEl = document.getElementById('oco-rr');
        if (!rrEl) return;
        const pos = _getPos(id);
        if (!pos) { rrEl.textContent = 'R:R --'; return; }

        const entry = parseFloat(pos.entry_price) || 0;
        const tpRaw = parseFloat(document.getElementById('oco-tp-input').value);
        const slRaw = parseFloat(document.getElementById('oco-sl-input').value);
        if (!tpRaw || !slRaw || !entry) { rrEl.textContent = 'R:R --'; return; }

        const mode = _getMode('oco');
        let tpPrice, slPrice;
        if (mode === 'pct') {
            tpPrice = _pctToPrice(tpRaw, entry, pos.side, 'TP');
            slPrice = _pctToPrice(slRaw, entry, pos.side, 'SL');
        } else {
            tpPrice = tpRaw;
            slPrice = slRaw;
        }

        let reward, risk;
        if (pos.side === 'LONG') {
            reward = tpPrice - entry;
            risk = entry - slPrice;
        } else {
            reward = entry - tpPrice;
            risk = slPrice - entry;
        }

        const qty = parseFloat(pos.quantity) || 0;
        const riskUsd = Math.abs(risk * qty).toFixed(2);
        const rewardUsd = Math.abs(reward * qty).toFixed(2);
        const tpPct = Math.abs((tpPrice - entry) / entry * 100).toFixed(2);
        const slPct = Math.abs((slPrice - entry) / entry * 100).toFixed(2);

        if (risk <= 0 || reward <= 0) {
            rrEl.innerHTML = 'R:R --';
            rrEl.className = 'text-center text-sm text-gray-400 mb-4';
            return;
        }
        const ratio = (reward / risk).toFixed(1);
        const mirror = mode === 'pct'
            ? `<div class="text-xs text-gray-500 mt-0.5">TP: ${Utils.fmtPrice(tpPrice)} | SL: ${Utils.fmtPrice(slPrice)}</div>`
            : `<div class="text-xs text-gray-500 mt-0.5">TP: ${tpPct}% | SL: ${slPct}%</div>`;
        rrEl.innerHTML = `<div><span class="text-blue-400 font-semibold">R:R 1:${ratio}</span>`
            + `<span class="text-gray-500 mx-2">|</span>`
            + `<span class="text-red-400">-$${riskUsd}</span>`
            + `<span class="text-gray-500 mx-1">/</span>`
            + `<span class="text-emerald-400">+$${rewardUsd}</span></div>`
            + mirror;
        rrEl.className = 'text-center text-sm font-semibold mb-4';
    }

    function confirmClose(id) {
        const pos = _getPos(id);
        const label = pos ? `${pos.symbol} ${pos.side}` : `#${id}`;
        showModal('Fermer la position', `
            <p class="text-gray-400 mb-4">Fermer <strong>${label}</strong> au marche ?</p>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitClose(${id})" class="action-btn bg-red-600 flex-1">Fermer</button>
            </div>
        `);
    }

    function confirmSecure(id) {
        const pos = _getPos(id);
        if (!pos) return;
        const label = `${pos.symbol} ${pos.side}`;
        const entry = parseFloat(pos.entry_price) || 0;
        const current = parseFloat(pos.current_price) || 0;
        const qty = parseFloat(pos.quantity) || 0;
        const halfQty = qty / 2;
        const remaining = qty - halfQty;

        const slPrice = pos.side === 'LONG' ? entry * 1.002 : entry * 0.998;
        const inProfit = pos.side === 'LONG' ? current > slPrice : current < slPrice;
        const warning = !inProfit
            ? '<p class="text-red-400 text-sm mb-2">Position pas assez en profit pour securiser.</p>'
            : '';

        showModal('Securiser la position', `
            <p class="text-gray-400 mb-3">Securiser <strong>${label}</strong> :</p>
            <div class="text-sm space-y-1 mb-4">
                <div class="flex justify-between">
                    <span class="text-gray-500">Vente marche (50%)</span>
                    <span class="font-medium">${halfQty.toFixed(6)}</span>
                </div>
                <div class="flex justify-between">
                    <span class="text-gray-500">SL breakeven +0.2%</span>
                    <span class="font-medium">${Utils.fmtPrice(slPrice)}</span>
                </div>
                <div class="flex justify-between">
                    <span class="text-gray-500">Quantite restante</span>
                    <span class="font-medium">${remaining.toFixed(6)}</span>
                </div>
            </div>
            ${warning}
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-gray-700 flex-1">Annuler</button>
                <button onclick="Positions.submitSecure(${id})" class="action-btn bg-cyan-600 flex-1"
                    ${!inProfit ? 'disabled style="opacity:0.5;cursor:not-allowed"' : ''}>Securiser</button>
            </div>
        `);
    }

    async function submitSecure(id) {
        await apiPost(`/api/positions/${id}/secure`, {}, 'Position securisee');
    }

    async function apiPost(url, body, successMsg = 'Ordre place') {
        try {
            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Error');
            App.toast('success', successMsg);
            hideModal();
        } catch (e) {
            App.toast('error', e.message);
        }
    }

    async function submitSL(id) {
        const pos = _getPos(id);
        if (!pos) return;
        const price = _resolvePrice('sl-input', 'sl', pos, 'SL');
        if (!price) return;
        await apiPost(`/api/positions/${id}/sl`, { price: String(price) });
    }

    async function submitTP(id) {
        const pos = _getPos(id);
        if (!pos) return;
        const price = _resolvePrice('tp-input', 'tp', pos, 'TP');
        if (!price) return;
        await apiPost(`/api/positions/${id}/tp`, { price: String(price) });
    }

    async function submitOCO(id) {
        const pos = _getPos(id);
        if (!pos) return;
        const entry = parseFloat(pos.entry_price) || 0;
        const mode = _getMode('oco');

        let tpPrice = parseFloat(document.getElementById('oco-tp-input').value);
        let slPrice = parseFloat(document.getElementById('oco-sl-input').value);
        if (!tpPrice || !slPrice) return;

        if (mode === 'pct') {
            tpPrice = _pctToPrice(tpPrice, entry, pos.side, 'TP');
            slPrice = _pctToPrice(slPrice, entry, pos.side, 'SL');
        }

        await apiPost(`/api/positions/${id}/oco`, { tp_price: String(tpPrice), sl_price: String(slPrice) });
    }

    async function submitClose(id) {
        await apiPost(`/api/positions/${id}/close`, {});
    }

    function confirmCancelOrders(id) {
        const pos = _getPos(id);
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
        await apiPost(`/api/positions/${id}/cancel-orders`, {}, 'Ordres annules');
    }

    // Real-time updates
    WS.on('positions_snapshot', render);
    BalanceStore.onChange(() => {
        if (currentPositions.length) {
            const total = currentPositions.reduce((s, p) => s + (parseFloat(p.pnl_usd) || 0), 0);
            _updateToolbarPortfolio(total);
        }
    });

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

    // Sort change listener
    document.addEventListener('DOMContentLoaded', () => {
        const sel = sortSelect();
        if (sel) sel.addEventListener('change', () => render(currentPositions));
    });

    return {
        load, render, showSL, showTP, showOCO, confirmClose, confirmSecure,
        submitSL, submitTP, submitOCO, submitClose, submitSecure, hideModal,
        confirmCancelOrders, submitCancelOrders,
        _setMode, _updateRisk, _updateRR,
    };
})();
