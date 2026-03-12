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
                Charts.updatePnl(p.id, parseFloat(p.pnl_usd) || 0);
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
        const isBelow = (orderType === 'SL' && side === 'LONG') || (orderType === 'TP' && side === 'SHORT');
        return isBelow ? entryPrice * (1 - pct / 100) : entryPrice * (1 + pct / 100);
    }

    function _usdToPrice(usd, entryPrice, qty, side, orderType) {
        const delta = usd / qty;
        const isBelow = (orderType === 'SL' && side === 'LONG') || (orderType === 'TP' && side === 'SHORT');
        return isBelow ? entryPrice - delta : entryPrice + delta;
    }

    function _toggleHtml(prefix) {
        return `
            <div class="flex gap-1 mb-3" id="${prefix}-mode-toggle">
                <button type="button" class="mode-btn active flex-1 text-sm py-1.5 rounded font-medium" data-mode="price" onclick="Positions._setMode('${prefix}','price')">Prix</button>
                <button type="button" class="mode-btn flex-1 text-sm py-1.5 rounded font-medium" data-mode="pct" onclick="Positions._setMode('${prefix}','pct')">%</button>
                <button type="button" class="mode-btn flex-1 text-sm py-1.5 rounded font-medium" data-mode="usd" onclick="Positions._setMode('${prefix}','usd')">USD</button>
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
            if (mode === 'pct') input.placeholder = 'Distance %';
            else if (mode === 'usd') input.placeholder = prefix === 'sl' ? 'Perte $' : 'Gain $';
            else input.placeholder = `Prix ${prefix.toUpperCase()}`;
        }
        // Dual inputs (OCO)
        const tpInput = document.getElementById(`${prefix}-tp-input`);
        const slInput = document.getElementById(`${prefix}-sl-input`);
        if (tpInput) {
            tpInput.value = '';
            tpInput.placeholder = mode === 'pct' ? 'TP %' : mode === 'usd' ? 'Gain $' : 'Prix TP';
        }
        if (slInput) {
            slInput.value = '';
            slInput.placeholder = mode === 'pct' ? 'SL %' : mode === 'usd' ? 'Perte $' : 'Prix SL';
        }
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
        const mode = _getMode(prefix);
        if (mode === 'pct') {
            const entry = parseFloat(pos.entry_price) || 0;
            if (!entry) return null;
            return _pctToPrice(raw, entry, pos.side, orderType);
        }
        if (mode === 'usd') {
            const entry = parseFloat(pos.entry_price) || 0;
            const qty = parseFloat(pos.quantity) || 0;
            if (!entry || !qty) return null;
            return _usdToPrice(raw, entry, qty, pos.side, orderType);
        }
        return raw;
    }

    // --- Key levels ---

    async function _loadLevels(symbol, entryPrice, qty, side, prefix, posId) {
        const el = document.getElementById(`${prefix}-levels`);
        if (!el) return;
        try {
            const resp = await fetch(`/api/analysis/${symbol}`);
            if (!resp.ok) return;
            const data = await resp.json();
            const levels = data.key_levels || [];
            if (!levels.length) return;

            const chips = levels.map(l => {
                const price = parseFloat(l.price);
                const gainPct = side === 'LONG'
                    ? ((price - entryPrice) / entryPrice * 100)
                    : ((entryPrice - price) / entryPrice * 100);
                const gainUsd = side === 'LONG'
                    ? (price - entryPrice) * qty
                    : (entryPrice - price) * qty;
                const sign = gainPct >= 0 ? '+' : '';
                const color = gainPct >= 0 ? 'text-emerald-400' : 'text-red-400';
                const usdStr = Math.abs(gainUsd) >= 100
                    ? Math.abs(gainUsd).toFixed(0)
                    : Math.abs(gainUsd).toFixed(2);
                return `<button type="button" class="level-chip text-xs px-2 py-1 rounded bg-stone-700/50 border border-stone-600 hover:border-stone-400"
                    onclick="Positions._fillLevel('${prefix}',${price},${posId})">
                    <span class="text-gray-400">${l.type}</span>
                    <span class="text-white">${Utils.fmtPrice(price)}</span>
                    <span class="${color}">${sign}${gainPct.toFixed(1)}%</span>
                    <span class="${color}">${sign}$${usdStr}</span>
                </button>`;
            }).join('');

            el.innerHTML = `<div class="text-xs text-gray-500 mb-1">Niveaux cles</div>`
                + `<div class="flex flex-wrap gap-1">${chips}</div>`;
            el.classList.remove('hidden');
        } catch (e) { /* analysis not available */ }
    }

    function _fillLevel(prefix, price, posId) {
        if (prefix === 'oco') {
            // Switch to price mode without clearing existing values
            const toggle = document.getElementById('oco-mode-toggle');
            if (toggle) toggle.querySelectorAll('.mode-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === 'price');
            });
            const pos = _getPos(posId);
            if (!pos) return;
            const entry = parseFloat(pos.entry_price) || 0;
            const isAbove = price >= entry;
            const isTPAbove = pos.side === 'LONG';
            const inputId = (isAbove === isTPAbove) ? 'oco-tp-input' : 'oco-sl-input';
            document.getElementById(inputId).value = price;
            // Update placeholders without clearing
            const tpInput = document.getElementById('oco-tp-input');
            const slInput = document.getElementById('oco-sl-input');
            if (tpInput && !tpInput.value) tpInput.placeholder = 'Prix TP';
            if (slInput && !slInput.value) slInput.placeholder = 'Prix SL';
            _updateRR(posId);
        } else {
            _setMode(prefix, 'price');
            document.getElementById(`${prefix}-input`).value = price;
            _updateRisk(posId, prefix);
        }
    }

    // --- Modals ---

    function showSL(id) {
        showModal('Stop Loss', `
            ${_toggleHtml('sl')}
            <input id="sl-input" type="number" step="any" placeholder="Prix SL"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRisk(${id},'sl')">
            <div id="sl-levels" class="mb-3 hidden"></div>
            <div id="sl-risk" class="text-center text-sm text-gray-400 mb-4">Risque --</div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-stone-700 flex-1">Annuler</button>
                <button onclick="Positions.submitSL(${id})" class="action-btn bg-yellow-600 flex-1">Placer SL</button>
            </div>
        `);
        const pos = _getPos(id);
        if (pos) _loadLevels(pos.symbol, parseFloat(pos.entry_price) || 0, parseFloat(pos.quantity) || 0, pos.side, 'sl', id);
    }

    function showTP(id) {
        showModal('Take Profit', `
            ${_toggleHtml('tp')}
            <input id="tp-input" type="number" step="any" placeholder="Prix TP"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRisk(${id},'tp')">
            <div id="tp-levels" class="mb-3 hidden"></div>
            <div id="tp-risk" class="text-center text-sm text-gray-400 mb-4">Gain --</div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-stone-700 flex-1">Annuler</button>
                <button onclick="Positions.submitTP(${id})" class="action-btn bg-emerald-600 flex-1">Placer TP</button>
            </div>
        `);
        const pos = _getPos(id);
        if (pos) _loadLevels(pos.symbol, parseFloat(pos.entry_price) || 0, parseFloat(pos.quantity) || 0, pos.side, 'tp', id);
    }

    function showOCO(id) {
        const pos = _getPos(id);
        showModal('OCO (SL + TP)', `
            ${_toggleHtml('oco')}
            <input id="oco-tp-input" type="number" step="any" placeholder="Prix TP"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRR(${id})">
            <input id="oco-sl-input" type="number" step="any" placeholder="Prix SL"
                class="w-full rounded px-3 py-3 mb-3 text-base" oninput="Positions._updateRR(${id})">
            <div id="oco-levels" class="mb-3 hidden"></div>
            <div id="oco-rr" class="text-center text-sm text-gray-400 mb-4">R:R --</div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-stone-700 flex-1">Annuler</button>
                <button onclick="Positions.submitOCO(${id})" class="action-btn bg-blue-600 flex-1">Placer OCO</button>
            </div>
        `);
        if (pos) _loadLevels(pos.symbol, parseFloat(pos.entry_price) || 0, parseFloat(pos.quantity) || 0, pos.side, 'oco', id);
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
        let price;
        if (mode === 'pct') price = _pctToPrice(raw, entry, pos.side, type.toUpperCase());
        else if (mode === 'usd') price = _usdToPrice(raw, entry, qty, pos.side, type.toUpperCase());
        else price = raw;

        let delta;
        if (pos.side === 'LONG') {
            delta = (price - entry) * qty;
        } else {
            delta = (entry - price) * qty;
        }
        const pct = ((price - entry) / entry * 100);
        const absPct = Math.abs(pct).toFixed(2);

        let mirror;
        if (mode === 'price') {
            mirror = `<div class="text-xs text-gray-500 mt-0.5">${absPct}% depuis l'entree</div>`;
        } else if (mode === 'pct') {
            mirror = `<div class="text-xs text-gray-500 mt-0.5">Prix: ${Utils.fmtPrice(price)}</div>`;
        } else {
            mirror = `<div class="text-xs text-gray-500 mt-0.5">Prix: ${Utils.fmtPrice(price)} (${absPct}%)</div>`;
        }

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
        const qty = parseFloat(pos.quantity) || 0;
        let tpPrice, slPrice;
        if (mode === 'pct') {
            tpPrice = _pctToPrice(tpRaw, entry, pos.side, 'TP');
            slPrice = _pctToPrice(slRaw, entry, pos.side, 'SL');
        } else if (mode === 'usd') {
            if (!qty) { rrEl.textContent = 'R:R --'; return; }
            tpPrice = _usdToPrice(tpRaw, entry, qty, pos.side, 'TP');
            slPrice = _usdToPrice(slRaw, entry, qty, pos.side, 'SL');
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
        const mirror = mode === 'price'
            ? `<div class="text-xs text-gray-500 mt-0.5">TP: ${tpPct}% | SL: ${slPct}%</div>`
            : `<div class="text-xs text-gray-500 mt-0.5">TP: ${Utils.fmtPrice(tpPrice)} | SL: ${Utils.fmtPrice(slPrice)}</div>`;
        rrEl.innerHTML = `<div><span class="text-blue-400 font-semibold">R:R 1:${ratio}</span>`
            + `<span class="text-gray-500 mx-2">|</span>`
            + `<span class="text-red-400">-$${riskUsd}</span>`
            + `<span class="text-gray-500 mx-1">/</span>`
            + `<span class="text-emerald-400">+$${rewardUsd}</span></div>`
            + mirror;
        rrEl.className = 'text-center text-sm font-semibold mb-4';
    }

    let _closePct = 100;

    function confirmClose(id) {
        _closePct = 100;
        const pos = _getPos(id);
        const label = pos ? `${pos.symbol} ${pos.side}` : `#${id}`;
        const qty = pos ? parseFloat(pos.quantity) || 0 : 0;
        showModal('Fermer la position', `
            <p class="text-gray-400 mb-3">Fermer <strong>${label}</strong> au marche</p>
            <div class="flex gap-2 mb-3">
                ${[25, 50, 75, 100].map(p =>
                    `<button class="close-pct-btn action-btn flex-1 ${p === 100 ? 'bg-red-600' : 'bg-stone-700'}"
                        onclick="Positions.selectClosePct(${p}, ${qty})">${p}%</button>`
                ).join('')}
            </div>
            <div class="text-sm text-gray-500 text-center mb-4">
                Quantite: <span id="close-qty-display">${qty.toFixed(6)}</span>
            </div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-stone-700 flex-1">Annuler</button>
                <button onclick="Positions.submitClose(${id})" class="action-btn bg-red-600 flex-1">Fermer</button>
            </div>
        `);
    }

    function selectClosePct(pct, totalQty) {
        _closePct = pct;
        document.querySelectorAll('.close-pct-btn').forEach(btn => {
            const active = btn.textContent.trim() === pct + '%';
            btn.className = `close-pct-btn action-btn flex-1 ${active ? 'bg-red-600' : 'bg-stone-700'}`;
        });
        document.getElementById('close-qty-display').textContent = (totalQty * pct / 100).toFixed(6);
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
                <button onclick="Positions.hideModal()" class="action-btn bg-stone-700 flex-1">Annuler</button>
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
        } else if (mode === 'usd') {
            const qty = parseFloat(pos.quantity) || 0;
            if (!qty) return;
            tpPrice = _usdToPrice(tpPrice, entry, qty, pos.side, 'TP');
            slPrice = _usdToPrice(slPrice, entry, qty, pos.side, 'SL');
        }

        await apiPost(`/api/positions/${id}/oco`, { tp_price: String(tpPrice), sl_price: String(slPrice) });
    }

    async function submitClose(id) {
        const msg = _closePct < 100 ? `${_closePct}% ferme` : 'Position fermee';
        await apiPost(`/api/positions/${id}/close`, { pct: _closePct }, msg);
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
                <button onclick="Positions.hideModal()" class="action-btn bg-stone-700 flex-1">Retour</button>
                <button onclick="Positions.submitCancelOrders(${id})" class="action-btn bg-orange-600 flex-1">Annuler ordres</button>
            </div>
        `);
    }

    async function submitCancelOrders(id) {
        await apiPost(`/api/positions/${id}/cancel-orders`, {}, 'Ordres annules');
    }

    // --- Open position modal ---

    let _openSide = 'LONG';
    let _openAccount = 'MARGIN'; // SPOT or MARGIN
    let _openPreview = null;
    let _openAmount = null; // null = MAX

    function showOpen() {
        _openSide = 'LONG';
        _openAccount = 'MARGIN';
        _openPreview = null;
        _openAmount = null;
        showModal('Ouvrir une position', `
            <div class="mb-3">
                <div class="flex gap-1 mb-2">
                    <button type="button" class="text-sm px-3 py-2 rounded bg-stone-700 hover:bg-stone-600 text-gray-300 font-medium" onclick="Positions._pickSymbol('BTCUSDC')">BTC</button>
                    <button type="button" class="text-sm px-3 py-2 rounded bg-stone-700 hover:bg-stone-600 text-gray-300 font-medium" onclick="Positions._pickSymbol('ETHUSDC')">ETH</button>
                    <button type="button" class="text-sm px-3 py-2 rounded bg-stone-700 hover:bg-stone-600 text-gray-300 font-medium" onclick="Positions._pickSymbol('BNBUSDC')">BNB</button>
                    <input id="open-symbol" type="text" placeholder="ou saisir..."
                        class="flex-1 rounded px-3 py-2 text-sm uppercase min-w-0"
                        oninput="Positions._previewOpen()" autocomplete="off">
                </div>
            </div>
            <div class="flex gap-1 mb-2" id="open-account-toggle">
                <button type="button" class="open-account-btn flex-1 text-sm py-2 rounded font-medium bg-stone-700"
                    data-account="SPOT" onclick="Positions._setAccount('SPOT')">Spot</button>
                <button type="button" class="open-account-btn active flex-1 text-sm py-2 rounded font-medium bg-blue-600"
                    data-account="MARGIN" onclick="Positions._setAccount('MARGIN')">Margin x5</button>
            </div>
            <div class="flex gap-1 mb-3" id="open-side-toggle">
                <button type="button" class="open-side-btn active flex-1 text-sm py-2 rounded font-medium bg-emerald-600"
                    data-side="LONG" onclick="Positions._setSide('LONG')">LONG</button>
                <button type="button" class="open-side-btn flex-1 text-sm py-2 rounded font-medium bg-stone-700"
                    data-side="SHORT" onclick="Positions._setSide('SHORT')">SHORT</button>
            </div>
            <div class="mb-3">
                <label class="block text-xs text-gray-500 mb-1">Montant USDC</label>
                <div class="flex gap-1" id="open-amount-presets">
                    <button type="button" class="open-amount-btn text-sm px-3 py-2 rounded bg-stone-700 hover:bg-stone-600 text-gray-300 font-medium flex-1" data-pct="25" onclick="Positions._setAmount('25')">25%</button>
                    <button type="button" class="open-amount-btn text-sm px-3 py-2 rounded bg-stone-700 hover:bg-stone-600 text-gray-300 font-medium flex-1" data-pct="50" onclick="Positions._setAmount('50')">50%</button>
                    <button type="button" class="open-amount-btn text-sm px-3 py-2 rounded bg-stone-700 hover:bg-stone-600 text-gray-300 font-medium flex-1" data-pct="75" onclick="Positions._setAmount('75')">75%</button>
                    <button type="button" class="open-amount-btn active text-sm px-3 py-2 rounded bg-blue-600 text-white font-medium flex-1" data-pct="100" onclick="Positions._setAmount(null)">MAX</button>
                </div>
            </div>
            <div class="mb-3">
                <input id="open-price" type="number" step="any" placeholder="Prix (vide = Market)"
                    class="w-full rounded px-3 py-3 text-base"
                    oninput="Positions._previewOpen()">
            </div>
            <div id="open-preview" class="text-sm text-gray-400 text-center mb-4">
                Entrez un symbole pour voir le preview
            </div>
            <div class="flex gap-2">
                <button onclick="Positions.hideModal()" class="action-btn bg-stone-700 flex-1">Annuler</button>
                <button id="open-submit-btn" onclick="Positions.submitOpen()" class="action-btn open-position-btn flex-1" disabled style="opacity:0.5">Ouvrir</button>
            </div>
        `);
    }

    function _pickSymbol(symbol) {
        const input = document.getElementById('open-symbol');
        if (input) {
            input.value = symbol;
            _previewOpen();
        }
    }

    function _setAccount(account) {
        _openAccount = account;
        const toggle = document.getElementById('open-account-toggle');
        if (toggle) {
            toggle.querySelectorAll('.open-account-btn').forEach(btn => {
                const active = btn.dataset.account === account;
                btn.classList.toggle('active', active);
                btn.className = `open-account-btn flex-1 text-sm py-2 rounded font-medium ${
                    active ? 'bg-blue-600 text-white' : 'bg-stone-700 text-gray-300'
                }`;
            });
        }
        // Spot: force LONG, disable SHORT
        const shortBtn = document.querySelector('.open-side-btn[data-side="SHORT"]');
        if (shortBtn) {
            if (account === 'SPOT') {
                shortBtn.disabled = true;
                shortBtn.style.opacity = '0.3';
                shortBtn.style.pointerEvents = 'none';
                if (_openSide === 'SHORT') _setSide('LONG');
            } else {
                shortBtn.disabled = false;
                shortBtn.style.opacity = '';
                shortBtn.style.pointerEvents = '';
            }
        }
        _previewOpen();
    }

    function _setSide(side) {
        if (_openAccount === 'SPOT' && side === 'SHORT') return;
        _openSide = side;
        const toggle = document.getElementById('open-side-toggle');
        if (!toggle) return;
        toggle.querySelectorAll('.open-side-btn').forEach(btn => {
            const active = btn.dataset.side === side;
            btn.classList.toggle('active', active);
            btn.className = `open-side-btn flex-1 text-sm py-2 rounded font-medium ${
                active ? (side === 'LONG' ? 'bg-emerald-600' : 'bg-red-600') : 'bg-stone-700'
            }`;
        });
    }

    function _setAmount(pct) {
        _openAmount = pct; // null = 100% (MAX), '25'/'50'/'75' otherwise
        const container = document.getElementById('open-amount-presets');
        if (!container) return;
        container.querySelectorAll('.open-amount-btn').forEach(btn => {
            const isActive = pct === null ? btn.dataset.pct === '100' : btn.dataset.pct === pct;
            btn.classList.toggle('active', isActive);
            btn.className = `open-amount-btn text-sm px-3 py-2 rounded font-medium flex-1 ${
                isActive ? 'bg-blue-600 text-white' : 'bg-stone-700 hover:bg-stone-600 text-gray-300'
            }`;
        });
        _previewOpen();
    }

    let _previewTimer = null;

    function _previewOpen() {
        clearTimeout(_previewTimer);
        _previewTimer = setTimeout(_fetchPreview, 400);
    }

    async function _fetchPreview() {
        const symbolInput = document.getElementById('open-symbol');
        const previewEl = document.getElementById('open-preview');
        const submitBtn = document.getElementById('open-submit-btn');
        if (!symbolInput || !previewEl) return;

        const symbol = symbolInput.value.trim().toUpperCase();
        if (symbol.length < 5) {
            previewEl.textContent = 'Entrez un symbole pour voir le preview';
            if (submitBtn) { submitBtn.disabled = true; submitBtn.style.opacity = '0.5'; }
            _openPreview = null;
            return;
        }

        previewEl.innerHTML = '<span class="text-gray-500">Chargement...</span>';
        try {
            const resp = await fetch(`/api/positions/open/preview?symbol=${encodeURIComponent(symbol)}&account_type=${_openAccount}`);
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || 'Erreur');
            }
            const data = await resp.json();
            _openPreview = data;

            const priceInput = document.getElementById('open-price');
            const usePrice = priceInput && priceInput.value ? priceInput.value : data.current_price;
            const usdcFree = parseFloat(data.usdc_free);
            const leverage = parseFloat(data.leverage);
            const pctValue = _openAmount ? parseInt(_openAmount) : 100;
            const effectiveAmount = usdcFree * pctValue / 100;
            const notional = effectiveAmount * leverage * 0.98;
            const qty = notional / parseFloat(usePrice);
            const amountLabel = _openAmount ? `$${effectiveAmount.toFixed(2)} (${pctValue}%)` : `$${usdcFree.toFixed(2)} (MAX)`;
            const isSpot = _openAccount === 'SPOT';
            const balanceLabel = isSpot ? 'USDC dispo (spot)' : 'USDC dispo (cross)';
            const notionalLabel = isSpot ? 'Notionnel' : `Notionnel (x${leverage} × 0.98)`;

            previewEl.innerHTML = `
                <div class="space-y-1">
                    <div class="flex justify-between"><span class="text-gray-500">${balanceLabel}</span><span class="text-white font-medium">$${usdcFree.toFixed(2)}</span></div>
                    <div class="flex justify-between"><span class="text-gray-500">Montant utilise</span><span class="text-blue-400 font-medium">${amountLabel}</span></div>
                    <div class="flex justify-between"><span class="text-gray-500">Prix courant</span><span class="text-white font-medium">${Utils.fmtPrice(parseFloat(data.current_price))}</span></div>
                    ${isSpot ? '' : `<div class="flex justify-between"><span class="text-gray-500">Leverage</span><span class="text-white font-medium">x${leverage}</span></div>`}
                    <div class="flex justify-between"><span class="text-gray-500">${notionalLabel}</span><span class="text-blue-400 font-medium">$${notional.toFixed(2)}</span></div>
                    <div class="flex justify-between"><span class="text-gray-500">Quantite</span><span class="text-emerald-400 font-medium">${qty.toFixed(6)}</span></div>
                </div>
            `;
            if (submitBtn) { submitBtn.disabled = false; submitBtn.style.opacity = '1'; }
        } catch (e) {
            previewEl.innerHTML = `<span class="text-red-400">${e.message}</span>`;
            if (submitBtn) { submitBtn.disabled = true; submitBtn.style.opacity = '0.5'; }
            _openPreview = null;
        }
    }

    async function submitOpen() {
        const symbolInput = document.getElementById('open-symbol');
        const priceInput = document.getElementById('open-price');
        if (!symbolInput) return;

        const symbol = symbolInput.value.trim().toUpperCase();
        if (!symbol) return;

        const body = { symbol, side: _openSide, account_type: _openAccount };
        if (priceInput && priceInput.value) {
            body.price = priceInput.value;
        }
        if (_openAmount && _openPreview) {
            const usdcFree = parseFloat(_openPreview.usdc_free);
            const amount = (usdcFree * parseInt(_openAmount) / 100).toFixed(2);
            body.amount_usdc = amount;
        }

        const pctLabel = _openAmount ? `${_openAmount}%` : 'MAX';
        const typeLabel = body.price ? 'LIMIT' : 'MARKET';
        const accountLabel = _openAccount === 'SPOT' ? 'SPOT' : 'MARGIN';
        await apiPost('/api/positions/open', body, `${accountLabel} ${_openSide} ${symbol} ${typeLabel} ${pctLabel} place`);
    }

    // Real-time updates
    WS.on('positions_snapshot', (data, msg) => {
        render(data);
        if (msg && msg.trailing_mode) {
            _trailingMode = msg.trailing_mode;
            _updateModeToggle();
        }
    });
    BalanceStore.onChange(() => {
        if (currentPositions.length) {
            const total = currentPositions.reduce((s, p) => s + (parseFloat(p.pnl_usd) || 0), 0);
            _updateToolbarPortfolio(total);
        }
    });

    // --- Trailing manual mode ---

    async function confirmPending(id) {
        await apiPost(`/api/settings/trailing/pending/${id}/confirm`, {}, 'Ordres places');
    }

    async function rejectPending(id) {
        await apiPost(`/api/settings/trailing/pending/${id}/reject`, {}, 'Proposition refusee');
    }

    let _trailingMode = 'auto';

    async function loadTrailingMode() {
        try {
            const resp = await fetch('/api/settings/trailing/mode');
            const data = await resp.json();
            _trailingMode = data.mode || 'auto';
            _updateModeToggle();
        } catch (e) { /* ignore */ }
    }

    const _MODE_CYCLE = ['auto', 'confirmed', 'manual'];

    async function toggleTrailingMode() {
        const idx = _MODE_CYCLE.indexOf(_trailingMode);
        const newMode = _MODE_CYCLE[(idx + 1) % _MODE_CYCLE.length];
        try {
            const resp = await fetch('/api/settings/trailing/mode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: newMode }),
            });
            if (!resp.ok) throw new Error('Failed');
            _trailingMode = newMode;
            _updateModeToggle();
            const labels = { auto: 'Mode auto reactive', confirmed: 'Mode confirmed active', manual: 'Mode manuel active' };
            App.toast('success', labels[newMode]);
        } catch (e) {
            App.toast('error', 'Erreur changement mode');
        }
    }

    function _updateModeToggle() {
        const btn = document.getElementById('trailing-mode-btn');
        if (!btn) return;
        const styles = {
            manual: { text: 'MANUEL', cls: 'bg-amber-600 text-white hover:bg-amber-500' },
            confirmed: { text: 'CONFIRMED', cls: 'bg-blue-600 text-white hover:bg-blue-500' },
            auto: { text: 'AUTO', cls: 'bg-emerald-600 text-white hover:bg-emerald-500' },
        };
        const s = styles[_trailingMode] || styles.auto;
        btn.textContent = s.text;
        btn.className = `px-3 py-1.5 text-xs font-bold rounded cursor-pointer transition-colors ${s.cls}`;
    }

    // Initial load via REST
    async function load() {
        try {
            const resp = await fetch('/api/positions');
            const data = await resp.json();
            render(data);
        } catch (e) {
            console.error('Failed to load positions', e);
        }
        loadTrailingMode();
    }

    // Sort change listener
    document.addEventListener('DOMContentLoaded', () => {
        const sel = sortSelect();
        if (sel) sel.addEventListener('change', () => render(currentPositions));
    });

    function getActiveSymbols() {
        const map = {};
        for (const p of currentPositions) {
            map[p.symbol] = p.side || 'LONG';
        }
        return map;
    }

    return {
        load, render, showSL, showTP, showOCO, confirmClose, confirmSecure,
        submitSL, submitTP, submitOCO, submitClose, submitSecure, hideModal,
        confirmCancelOrders, submitCancelOrders, selectClosePct,
        _setMode, _updateRisk, _updateRR, _fillLevel, getActiveSymbols,
        showOpen, submitOpen, _setSide, _setAccount, _setAmount, _previewOpen, _pickSymbol,
        confirmPending, rejectPending, toggleTrailingMode,
    };
})();
