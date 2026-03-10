const PositionCards = (() => {

    function formatPrice(p) {
        if (p >= 1000) return p.toFixed(2);
        if (p >= 1) return p.toFixed(4);
        return p.toFixed(6);
    }

    const QUOTE_SUFFIXES = ['USDC', 'USDT', 'BUSD', 'BNB', 'BTC', 'ETH'];
    function _extractBase(symbol) {
        for (const q of QUOTE_SUFFIXES) {
            if (symbol.endsWith(q) && symbol.length > q.length) return symbol.slice(0, -q.length);
        }
        return symbol;
    }

    function _formatQty(q) {
        if (q >= 1000) return q.toFixed(2);
        if (q >= 1) return q.toFixed(4);
        if (q >= 0.001) return q.toFixed(6);
        return q.toFixed(8);
    }

    function _staleDot(priceAge) {
        if (priceAge == null) return '<span class="stale-dot stale" title="Pas de prix"></span>';
        if (priceAge > 10) return `<span class="stale-dot stale" title="Prix: ${Math.round(priceAge)}s"></span>`;
        return '<span class="stale-dot fresh" title="Prix live"></span>';
    }

    function buildCardHtml(p) {
        const pnl = parseFloat(p.pnl_usd) || 0;
        const pnlPct = parseFloat(p.pnl_pct) || 0;
        const fees = parseFloat(p.entry_fees_usd) || 0;
        const exitFees = parseFloat(p.exit_fees_est) || 0;
        const totalFees = fees + exitFees;
        const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const sideClass = p.side === 'LONG' ? 'side-long' : 'side-short';
        const sideBg = p.side === 'LONG' ? 'bg-emerald-900/30 text-emerald-400' : 'bg-red-900/30 text-red-400';
        const entry = parseFloat(p.entry_price) || 0;
        const current = parseFloat(p.current_price) || 0;
        const qty = parseFloat(p.quantity) || 0;
        const value = (current * qty).toFixed(2);
        const baseAsset = _extractBase(p.symbol);
        const grossPnl = p.side === 'LONG'
            ? (current - entry) * qty
            : (entry - current) * qty;

        return `
        <div class="position-card" data-id="${p.id}">
            <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-2">
                    <span class="font-bold text-base">${p.symbol}</span>
                    <span class="badge ${sideBg}">${p.side}</span>
                    <span class="text-xs text-gray-500 font-medium">${p.market_type.replace('_', ' ')}</span>
                </div>
                <span class="text-xs text-gray-500 tabular-nums" data-field="duration">${p.duration || ''}</span>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm mb-3">
                <div>
                    <div class="metric-label mb-0.5">Entrée</div>
                    <div class="font-medium tabular-nums" data-field="entry">${formatPrice(entry)}</div>
                </div>
                <div>
                    <div class="metric-label mb-0.5">Actuel <span data-field="stale-dot">${_staleDot(p.price_age)}</span></div>
                    <div class="font-medium tabular-nums" data-field="current">${formatPrice(current)}</div>
                </div>
                <div>
                    <div class="metric-label mb-0.5">Valeur</div>
                    <div class="font-medium tabular-nums" data-field="value">$${value}</div>
                    <div class="text-gray-500 text-xs tabular-nums" data-field="qty">${_formatQty(qty)} ${baseAsset}</div>
                </div>
                <div>
                    <div class="metric-label mb-0.5">PnL net</div>
                    <div class="${pnlClass} font-bold tabular-nums" data-field="pnl">
                        ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)
                    </div>
                    <div class="text-gray-600 text-xs tabular-nums" data-field="pnl-detail">brut ${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)} | fees $${totalFees.toFixed(2)}</div>
                </div>
            </div>
            <div class="mini-chart-container" id="chart-pos-${p.id}" style="height:120px"></div>
            <div class="flex items-center gap-2 flex-wrap mt-3" data-field="actions">
                ${_buildBadgesHtml(p)}
                <div class="flex-1"></div>
                <button class="action-btn bg-yellow-600" onclick="Positions.showSL(${p.id})">SL</button>
                <button class="action-btn bg-emerald-600" onclick="Positions.showTP(${p.id})">TP</button>
                <button class="action-btn bg-blue-600" onclick="Positions.showOCO(${p.id})">OCO</button>
                <button class="action-btn bg-cyan-600" onclick="Positions.confirmSecure(${p.id})">Secure</button>
                <button class="action-btn bg-red-600" onclick="Positions.confirmClose(${p.id})">Close</button>
            </div>
        </div>`;
    }

    function _distancePct(orderPrice, entryPrice) {
        if (!orderPrice || !entryPrice) return null;
        const op = parseFloat(orderPrice);
        const ep = parseFloat(entryPrice);
        if (!ep) return null;
        return ((op - ep) / ep * 100).toFixed(1);
    }

    function _pnlAtPrice(p, targetPrice) {
        const entry = parseFloat(p.entry_price);
        const qty = parseFloat(p.quantity);
        const target = parseFloat(targetPrice);
        if (!entry || !qty || !target) return null;
        const gross = p.side === 'SHORT' ? (entry - target) * qty : (target - entry) * qty;
        const entryFees = parseFloat(p.entry_fees_usd) || 0;
        const exitFees = target * qty * 0.001;
        return gross - entryFees - exitFees;
    }

    function _fmtPnlUsd(usd) {
        const sign = usd >= 0 ? '+' : '-';
        return `${sign}$${Math.abs(usd).toFixed(0)}`;
    }

    function _buildBadgesHtml(p) {
        const badges = [];
        const hasOrders = p.sl_order_id || p.tp_order_id || p.oco_order_list_id;

        if (p.sl_order_id || (p.oco_order_list_id && p.sl_price)) {
            const price = parseFloat(p.sl_price);
            const rawDist = _distancePct(p.sl_price, p.entry_price);
            const dist = rawDist !== null ? (p.side === 'SHORT' ? -rawDist : +rawDist) : null;
            const priceStr = price ? formatPrice(price) : '';
            const pnlUsd = _pnlAtPrice(p, p.sl_price);
            const usdStr = pnlUsd !== null ? ` ${_fmtPnlUsd(pnlUsd)}` : '';
            const distStr = dist !== null ? ` (${dist > 0 ? '+' : ''}${parseFloat(dist).toFixed(1)}%${usdStr})` : '';
            const label = priceStr ? `SL ${priceStr}${distStr}` : 'SL';
            const slInProfit = dist !== null && dist > 0;
            const slBg = slInProfit ? 'bg-emerald-900/40 text-emerald-400' : 'bg-red-900/40 text-red-400';
            badges.push(`<span class="badge ${slBg} tabular-nums">${label}</span>`);
        }
        if (p.tp_order_id || (p.oco_order_list_id && p.tp_price)) {
            const price = parseFloat(p.tp_price);
            const rawDist = _distancePct(p.tp_price, p.entry_price);
            const dist = rawDist !== null ? (p.side === 'SHORT' ? -rawDist : +rawDist) : null;
            const priceStr = price ? formatPrice(price) : '';
            const pnlUsd = _pnlAtPrice(p, p.tp_price);
            const usdStr = pnlUsd !== null ? ` ${_fmtPnlUsd(pnlUsd)}` : '';
            const distStr = dist !== null ? ` (${dist > 0 ? '+' : ''}${parseFloat(dist).toFixed(1)}%${usdStr})` : '';
            const label = priceStr ? `TP ${priceStr}${distStr}` : 'TP';
            badges.push(`<span class="badge bg-emerald-900/40 text-emerald-400 tabular-nums">${label}</span>`);
        }
        if (p.oco_order_list_id && !p.sl_price && !p.tp_price) {
            badges.push('<span class="badge bg-blue-900/40 text-blue-400">OCO</span>');
        }
        if (hasOrders) badges.push(`<button class="badge bg-orange-900/40 text-orange-400 cursor-pointer hover:bg-orange-800/50 transition-colors" onclick="Positions.confirmCancelOrders(${p.id})">&#x2715;</button>`);

        if (p.trailing === 'trailing') {
            badges.push('<span class="badge" style="background:rgba(201,149,107,0.2);color:#c9956b">TRAIL</span>');
        } else if (p.trailing === 'watching') {
            badges.push('<span class="badge bg-stone-700/40 text-gray-400">TRAIL wait</span>');
        } else if (p.trailing === 'override') {
            badges.push('<span class="badge bg-yellow-900/40 text-yellow-400">TRAIL off</span>');
        } else if (p.trailing === 'naked') {
            badges.push('<span class="badge bg-red-900/50 text-red-400 animate-pulse">NAKED</span>');
        } else if (!p.trailing) {
            badges.push('<span class="badge bg-red-900/30 text-red-500">NO TRAIL</span>');
        }

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

        const baseAsset = _extractBase(p.symbol);
        const f = field => card.querySelector(`[data-field="${field}"]`);

        const durEl = f('duration');
        if (durEl) durEl.textContent = p.duration || '';

        const entryEl = f('entry');
        if (entryEl) entryEl.textContent = formatPrice(entry);

        const curEl = f('current');
        if (curEl) curEl.textContent = formatPrice(current);

        const dotEl = f('stale-dot');
        if (dotEl) dotEl.innerHTML = _staleDot(p.price_age);

        const valEl = f('value');
        if (valEl) valEl.textContent = `$${value}`;

        const qtyEl = f('qty');
        if (qtyEl) qtyEl.textContent = `${_formatQty(qty)} ${baseAsset}`;

        const pnlEl = f('pnl');
        if (pnlEl) {
            pnlEl.className = `${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'} font-bold tabular-nums`;
            pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)`;
        }

        const detailEl = f('pnl-detail');
        if (detailEl) {
            detailEl.textContent = `brut ${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)} | fees $${totalFees.toFixed(2)}`;
        }

        const actionsEl = f('actions');
        if (actionsEl) {
            actionsEl.innerHTML = `
                ${_buildBadgesHtml(p)}
                <div class="flex-1"></div>
                <button class="action-btn bg-yellow-600" onclick="Positions.showSL(${p.id})">SL</button>
                <button class="action-btn bg-emerald-600" onclick="Positions.showTP(${p.id})">TP</button>
                <button class="action-btn bg-blue-600" onclick="Positions.showOCO(${p.id})">OCO</button>
                <button class="action-btn bg-cyan-600" onclick="Positions.confirmSecure(${p.id})">Secure</button>
                <button class="action-btn bg-red-600" onclick="Positions.confirmClose(${p.id})">Close</button>
            `;
        }
    }

    return { buildCardHtml, updateCardData };
})();
