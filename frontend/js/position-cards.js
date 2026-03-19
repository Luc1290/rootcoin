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

    function _pnlCapitalStr(pnl) {
        const total = BalanceStore.getTotal();
        if (!total) return '';
        const pct = pnl / total * 100;
        const sign = pct >= 0 ? '+' : '';
        return ` | ${sign}${pct.toFixed(2)}% solde`;
    }

    function _buildRRHtml(p) {
        const sl = parseFloat(p.sl_price);
        const tp = parseFloat(p.tp_price);
        const current = parseFloat(p.current_price) || 0;
        const qty = parseFloat(p.quantity) || 0;
        const hasOrders = p.sl_order_id || p.tp_order_id || p.oco_order_list_id || p.pending_confirmation;
        if (!sl || !tp || !current || !qty || !hasOrders) return '';

        let risk, reward;
        if (p.side === 'LONG') {
            risk = current - sl;
            reward = tp - current;
        } else {
            risk = sl - current;
            reward = current - tp;
        }

        const riskUsd = Math.abs(risk * qty);
        const rewardUsd = Math.abs(reward * qty);

        // Price already past TP or SL
        if (risk <= 0 || reward <= 0) {
            if (reward <= 0) {
                return `<div class="rr-bar" data-field="rr">
                    <span class="text-emerald-400 text-xs font-medium">TP atteint — pense a securiser</span>
                </div>`;
            }
            return `<div class="rr-bar" data-field="rr">
                <span class="text-red-400 text-xs font-medium">SL depasse — attention</span>
            </div>`;
        }

        const ratio = reward / risk;
        const ratioStr = ratio.toFixed(1);

        // Progress: 0% = at SL, 100% = at TP
        const totalRange = Math.abs(tp - sl);
        const fromSl = p.side === 'LONG' ? current - sl : sl - current;
        const progressPct = Math.min(100, Math.max(0, (fromSl / totalRange) * 100));

        // Dynamic explanation
        let hint, hintClass;
        if (progressPct > 85) {
            hint = 'Proche du TP, pense a securiser';
            hintClass = 'text-emerald-400';
        } else if (progressPct < 15) {
            hint = 'Proche du SL, attention';
            hintClass = 'text-red-400';
        } else if (ratio >= 3) {
            hint = `Tu risques $${riskUsd.toFixed(0)} pour gagner $${rewardUsd.toFixed(0)} — excellent ratio`;
            hintClass = 'text-emerald-400';
        } else if (ratio >= 1.5) {
            hint = `Tu risques $${riskUsd.toFixed(0)} pour gagner $${rewardUsd.toFixed(0)} — ratio favorable`;
            hintClass = 'text-blue-400';
        } else if (ratio >= 1) {
            hint = `Tu risques $${riskUsd.toFixed(0)} pour gagner $${rewardUsd.toFixed(0)} — ratio correct`;
            hintClass = 'text-gray-400';
        } else {
            hint = `Tu risques $${riskUsd.toFixed(0)} pour gagner $${rewardUsd.toFixed(0)} — risque > gain`;
            hintClass = 'text-orange-400';
        }

        const rrColor = ratio >= 2 ? 'text-emerald-400' : ratio >= 1 ? 'text-blue-400' : 'text-orange-400';

        return `<div class="rr-bar" data-field="rr">
            <div class="flex items-center gap-2 mb-1">
                <span class="${rrColor} text-xs font-bold tabular-nums">R:R 1:${ratioStr}</span>
                <span class="text-gray-600 text-xs">|</span>
                <span class="text-red-400 text-xs tabular-nums">-$${riskUsd.toFixed(0)}</span>
                <span class="text-gray-600 text-xs">/</span>
                <span class="text-emerald-400 text-xs tabular-nums">+$${rewardUsd.toFixed(0)}</span>
                <div class="flex-1 h-1.5 bg-stone-700 rounded-full overflow-hidden ml-1">
                    <div class="h-full rounded-full transition-all duration-500" style="width:${progressPct.toFixed(0)}%;background:linear-gradient(90deg,#ef4444,#eab308,#22c55e)"></div>
                </div>
            </div>
            <div class="${hintClass} text-xs">${hint}</div>
        </div>`;
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
                    <div class="metric-label mb-0.5" style="color:#3b82f6">Entrée</div>
                    <div class="font-medium tabular-nums" style="color:#3b82f6" data-field="entry">${formatPrice(entry)}</div>
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
                        ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}% position${_pnlCapitalStr(pnl)})
                    </div>
                    <div class="text-gray-600 text-xs tabular-nums" data-field="pnl-detail">brut ${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)} | fees $${totalFees.toFixed(2)}</div>
                </div>
            </div>
            ${_buildRRHtml(p)}
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

        if (p.sl_price && (p.sl_order_id || p.oco_order_list_id || p.pending_confirmation)) {
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
        if (p.tp_price && (p.tp_order_id || p.oco_order_list_id || p.pending_confirmation)) {
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

        // Pending confirmation badge + buttons (manual mode)
        if (p.pending_confirmation) {
            badges.push('<span class="badge bg-amber-900/50 text-amber-400 animate-pulse">EN ATTENTE</span>');
            badges.push(`<button class="badge bg-emerald-900/40 text-emerald-400 cursor-pointer hover:bg-emerald-800/50 transition-colors" onclick="event.stopPropagation();Positions.confirmPending(${p.id})">\u2713</button>`);
            badges.push(`<button class="badge bg-red-900/40 text-red-400 cursor-pointer hover:bg-red-800/50 transition-colors" onclick="event.stopPropagation();Positions.rejectPending(${p.id})">\u2717</button>`);
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
            pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}% position${_pnlCapitalStr(pnl)})`;
        }

        const detailEl = f('pnl-detail');
        if (detailEl) {
            detailEl.textContent = `brut ${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)} | fees $${totalFees.toFixed(2)}`;
        }

        const rrEl = f('rr');
        const newRR = _buildRRHtml(p);
        if (rrEl && newRR) {
            const tmp = document.createElement('div');
            tmp.innerHTML = newRR;
            const newRREl = tmp.firstElementChild;
            if (newRREl) rrEl.replaceWith(newRREl);
        } else if (rrEl && !newRR) {
            rrEl.remove();
        } else if (!rrEl && newRR) {
            const chartEl = card.querySelector('.mini-chart-container');
            if (chartEl) {
                const tmp = document.createElement('div');
                tmp.innerHTML = newRR;
                chartEl.before(tmp.firstElementChild);
            }
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
