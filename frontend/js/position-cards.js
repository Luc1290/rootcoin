const PositionCards = (() => {

    function formatPrice(p) {
        if (p >= 1000) return p.toFixed(2);
        if (p >= 1) return p.toFixed(4);
        return p.toFixed(6);
    }

    function buildCardHtml(p) {
        const pnl = parseFloat(p.pnl_usd) || 0;
        const pnlPct = parseFloat(p.pnl_pct) || 0;
        const fees = parseFloat(p.entry_fees_usd) || 0;
        const exitFees = parseFloat(p.exit_fees_est) || 0;
        const totalFees = fees + exitFees;
        const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const sideClass = p.side === 'LONG' ? 'side-long' : 'side-short';
        const entry = parseFloat(p.entry_price) || 0;
        const current = parseFloat(p.current_price) || 0;
        const qty = parseFloat(p.quantity) || 0;
        const value = (current * qty).toFixed(2);
        const grossPnl = p.side === 'LONG'
            ? (current - entry) * qty
            : (entry - current) * qty;

        return `
        <div class="position-card" data-id="${p.id}">
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                    <span class="font-semibold">${p.symbol}</span>
                    <span class="text-xs font-bold ${sideClass}">${p.side}</span>
                    <span class="text-xs text-gray-500">${p.market_type.replace('_', ' ')}</span>
                </div>
                <span class="text-xs text-gray-500" data-field="duration">${p.duration || ''}</span>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm mb-2">
                <div>
                    <div class="text-gray-500 text-xs">Entry</div>
                    <div data-field="entry">${formatPrice(entry)}</div>
                </div>
                <div>
                    <div class="text-gray-500 text-xs">Current</div>
                    <div data-field="current">${formatPrice(current)}</div>
                </div>
                <div>
                    <div class="text-gray-500 text-xs">Value</div>
                    <div data-field="value">$${value}</div>
                </div>
                <div>
                    <div class="text-gray-500 text-xs">PnL net</div>
                    <div class="${pnlClass} font-semibold" data-field="pnl">
                        ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)
                    </div>
                    <div class="text-gray-600 text-xs" data-field="pnl-detail">brut ${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)} | fees $${totalFees.toFixed(2)}</div>
                </div>
            </div>
            <div class="mini-chart-container" id="chart-pos-${p.id}" style="height:120px"></div>
            <div class="flex items-center gap-2 flex-wrap mt-2" data-field="actions">
                ${_buildBadgesHtml(p)}
                <div class="flex-1"></div>
                <button class="action-btn bg-yellow-600" onclick="Positions.showSL(${p.id})">SL</button>
                <button class="action-btn bg-green-600" onclick="Positions.showTP(${p.id})">TP</button>
                <button class="action-btn bg-blue-600" onclick="Positions.showOCO(${p.id})">OCO</button>
                <button class="action-btn bg-red-600" onclick="Positions.confirmClose(${p.id})">Close</button>
            </div>
        </div>`;
    }

    function _buildBadgesHtml(p) {
        const badges = [];
        const hasOrders = p.sl_order_id || p.tp_order_id || p.oco_order_list_id;
        if (p.sl_order_id) badges.push('<span class="text-xs bg-red-900/50 text-red-400 px-1.5 py-0.5 rounded">SL</span>');
        if (p.tp_order_id) badges.push('<span class="text-xs bg-green-900/50 text-green-400 px-1.5 py-0.5 rounded">TP</span>');
        if (p.oco_order_list_id) badges.push('<span class="text-xs bg-blue-900/50 text-blue-400 px-1.5 py-0.5 rounded">OCO</span>');
        if (hasOrders) badges.push(`<button class="text-xs bg-orange-900/50 text-orange-400 px-1.5 py-0.5 rounded cursor-pointer hover:bg-orange-800/50" onclick="Positions.confirmCancelOrders(${p.id})">&#x2715;</button>`);
        return badges.join('');
    }

    function updateCardData(card, p) {
        const pnl = parseFloat(p.pnl_usd) || 0;
        const pnlPct = parseFloat(p.pnl_pct) || 0;
        const fees = parseFloat(p.entry_fees_usd) || 0;
        const exitFees = parseFloat(p.exit_fees_est) || 0;
        const totalFees = fees + exitFees;
        const entry = parseFloat(p.entry_price) || 0;
        const current = parseFloat(p.current_price) || 0;
        const qty = parseFloat(p.quantity) || 0;
        const value = (current * qty).toFixed(2);
        const grossPnl = p.side === 'LONG'
            ? (current - entry) * qty
            : (entry - current) * qty;

        const f = field => card.querySelector(`[data-field="${field}"]`);

        const durEl = f('duration');
        if (durEl) durEl.textContent = p.duration || '';

        const entryEl = f('entry');
        if (entryEl) entryEl.textContent = formatPrice(entry);

        const curEl = f('current');
        if (curEl) curEl.textContent = formatPrice(current);

        const valEl = f('value');
        if (valEl) valEl.textContent = `$${value}`;

        const pnlEl = f('pnl');
        if (pnlEl) {
            pnlEl.className = `${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'} font-semibold`;
            pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)`;
        }

        const detailEl = f('pnl-detail');
        if (detailEl) {
            detailEl.textContent = `brut ${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)} | fees $${totalFees.toFixed(2)}`;
        }

        // Update order badges + action buttons
        const actionsEl = f('actions');
        if (actionsEl) {
            actionsEl.innerHTML = `
                ${_buildBadgesHtml(p)}
                <div class="flex-1"></div>
                <button class="action-btn bg-yellow-600" onclick="Positions.showSL(${p.id})">SL</button>
                <button class="action-btn bg-green-600" onclick="Positions.showTP(${p.id})">TP</button>
                <button class="action-btn bg-blue-600" onclick="Positions.showOCO(${p.id})">OCO</button>
                <button class="action-btn bg-red-600" onclick="Positions.confirmClose(${p.id})">Close</button>
            `;
        }
    }

    return { buildCardHtml, updateCardData };
})();
