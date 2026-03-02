const Cockpit = (() => {
    let _positions = [];
    let _analysis = null;
    let _recentCycles = [];
    let _prevPositionKeys = null;
    let _dayPnl = null;
    let _dayTrades = 0;
    let _marketSymbol = null;
    let _marketFirstPrice = null;
    const MAX_CYCLES = 5;

    async function load() {
        try {
            const [, anaResp, oppResp, streaksResp, cyclesResp] = await Promise.all([
                BalanceStore.load(),
                fetch('/api/analysis'),
                fetch('/api/opportunities'),
                fetch('/api/journal/streaks'),
                fetch('/api/cycles?status=closed&limit=5'),
            ]);
            if (anaResp.ok) {
                _analysis = await anaResp.json();
            }
            if (oppResp.ok) {
                const oppData = await oppResp.json();
                Opportunities.update(oppData.opportunities || []);
            }
            if (streaksResp.ok) {
                const s = await streaksResp.json();
                _dayPnl = parseFloat(s.day_pnl) || 0;
                _dayTrades = s.day_trades || 0;
            }
            if (cyclesResp.ok) {
                _recentCycles = await cyclesResp.json();
            }
            render();
            Charts.createCockpitChart('cockpit-portfolio-chart');
            Charts.loadCockpitData();
            _initMarketChart();
        } catch (e) {
            console.error('Cockpit load failed', e);
        }
    }

    function render() {
        _renderPortfolio();
        _renderMarketChart();
        _renderPositions();
        _renderBias();
        _renderRecentCycles();
        _renderWhale();
        _renderOpportunities();
    }

    function _renderPortfolio() {
        const el = document.getElementById('cockpit-portfolio');
        const totalPnl = _positions.reduce((s, p) => s + (parseFloat(p.pnl_usd) || 0), 0);
        const pnlClass = totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const pnlSign = totalPnl >= 0 ? '+' : '';
        const portfolioTotal = BalanceStore.getTotal();
        const portfolioStr = portfolioTotal !== null ? '$' + portfolioTotal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--';

        const dayTotal = (_dayPnl || 0) + totalPnl;
        const dayPnlClass = dayTotal >= 0 ? 'pnl-positive' : 'pnl-negative';
        const dayPnlSign = dayTotal >= 0 ? '+' : '';
        const dayLabel = _dayPnl !== null ? `${dayPnlSign}$${Math.abs(dayTotal).toFixed(2)}` : '--';
        const dayTradesLabel = _dayTrades > 0 ? ` (${_dayTrades} trade${_dayTrades > 1 ? 's' : ''})` : '';

        const totalSpan = el.querySelector('[data-field="total"]');
        const pnlSpan = el.querySelector('[data-field="pnl"]');
        if (totalSpan && pnlSpan) {
            totalSpan.textContent = portfolioStr;
            pnlSpan.textContent = `${pnlSign}$${Math.abs(totalPnl).toFixed(2)}`;
            pnlSpan.className = `text-base font-bold tabular-nums ${pnlClass}`;
            const daySpan = el.querySelector('[data-field="day-pnl"]');
            if (daySpan) {
                daySpan.textContent = dayLabel + dayTradesLabel;
                daySpan.className = `text-base font-bold tabular-nums ${dayPnlClass}`;
            }
            return;
        }

        el.innerHTML = `
        <div class="cockpit-card">
            <div class="flex items-center gap-2 justify-end">
                <span class="text-sm text-gray-400">Portfolio</span>
                <span class="text-lg font-bold tabular-nums" data-field="total">${portfolioStr}</span>
            </div>
            <div class="flex items-center gap-2 justify-end mt-1">
                <span class="text-sm text-gray-400">PnL ouvert</span>
                <span class="text-base font-bold tabular-nums ${pnlClass}" data-field="pnl">${pnlSign}$${Math.abs(totalPnl).toFixed(2)}</span>
            </div>
            <div class="flex items-center gap-2 justify-end mt-1">
                <span class="text-sm text-gray-400">PnL 24h</span>
                <span class="text-base font-bold tabular-nums ${dayPnlClass}" data-field="day-pnl">${dayLabel}${dayTradesLabel}</span>
            </div>
            <div id="cockpit-portfolio-chart" style="height:80px;width:100%;margin-top:8px"></div>
        </div>`;
    }

    function _renderPositions() {
        const el = document.getElementById('cockpit-positions');
        if (!_positions.length) {
            _prevPositionKeys = null;
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Aucune position ouverte</span></div>';
            return;
        }

        const keys = _positions.map(p => p.symbol + ':' + p.side).join(',');

        if (_prevPositionKeys === keys) {
            for (const p of _positions) {
                const row = el.querySelector(`[data-pos-id="${p.id}"]`);
                if (!row) continue;
                const pnl = parseFloat(p.pnl_pct) || 0;
                const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
                const pnlSign = pnl >= 0 ? '+' : '';
                const pnlUsd = parseFloat(p.pnl_usd) || 0;
                const pnlUsdSign = pnlUsd >= 0 ? '+' : '';

                const priceEl = row.querySelector('[data-field="price"]');
                const pctEl = row.querySelector('[data-field="pnl-pct"]');
                const usdEl = row.querySelector('[data-field="pnl-usd"]');

                if (priceEl) priceEl.textContent = Utils.fmtPriceCompact(p.current_price);
                if (pctEl) {
                    pctEl.textContent = `${pnlSign}${pnl.toFixed(1)}%`;
                    pctEl.className = `text-sm font-bold tabular-nums ${pnlClass}`;
                }
                if (usdEl) {
                    usdEl.textContent = `${pnlUsdSign}$${Math.abs(pnlUsd).toFixed(0)}`;
                    usdEl.className = `text-xs tabular-nums ${pnlClass}`;
                }
            }
            return;
        }

        _prevPositionKeys = keys;
        const rows = _positions.map(p => {
            const pnl = parseFloat(p.pnl_pct) || 0;
            const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            const pnlSign = pnl >= 0 ? '+' : '';
            const sideClass = p.side === 'LONG' ? 'side-long' : 'side-short';
            const symbol = p.symbol.replace('USDC', '');
            const price = Utils.fmtPriceCompact(p.current_price);
            const pnlUsd = parseFloat(p.pnl_usd) || 0;
            const pnlUsdSign = pnlUsd >= 0 ? '+' : '';
            return `<div class="cockpit-position" data-pos-id="${p.id}" onclick="App.switchTab('positions')">
                <div class="flex items-center gap-2 flex-1 min-w-0">
                    <span class="font-bold text-sm">${symbol}</span>
                    <span class="cockpit-side ${sideClass}">${p.side}</span>
                </div>
                <div class="flex items-center gap-3">
                    <span class="text-xs text-gray-400 tabular-nums" data-field="price">${price}</span>
                    <span class="text-sm font-bold tabular-nums ${pnlClass}" data-field="pnl-pct">${pnlSign}${pnl.toFixed(1)}%</span>
                    <span class="text-xs tabular-nums ${pnlClass}" data-field="pnl-usd">${pnlUsdSign}$${Math.abs(pnlUsd).toFixed(0)}</span>
                </div>
            </div>`;
        }).join('');

        el.innerHTML = `<div class="cockpit-card">${rows}</div>`;
    }

    function _renderBias() {
        const el = document.getElementById('cockpit-bias');
        if (!_analysis || !_analysis.analyses || !_analysis.analyses.length) {
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Analyse en attente...</span></div>';
            return;
        }

        const rows = _analysis.analyses.map(a => {
            if (!a.bias) return '';
            const dir = a.bias.direction;
            const conf = a.bias.confidence || 0;
            const symbol = a.symbol.replace('USDC', '');
            let barColor = '#6b7280';
            let dirClass = '';
            if (dir === 'LONG') { barColor = '#22c55e'; dirClass = 'pnl-positive'; }
            else if (dir === 'SHORT') { barColor = '#ef4444'; dirClass = 'pnl-negative'; }
            return `<div class="cockpit-bias-row" onclick="App.switchTab('analysis')">
                <div class="flex items-center justify-between mb-1">
                    <span class="text-xs font-bold">${symbol}</span>
                    <span class="text-xs font-semibold ${dirClass}">${dir} ${conf}%</span>
                </div>
                <div class="cockpit-bias-track"><div class="cockpit-bias-fill" style="width:${conf}%;background:${barColor}"></div></div>
            </div>`;
        }).join('');

        el.innerHTML = rows ? `<div class="cockpit-card">${rows}</div>` : '';
    }

    function _renderRecentCycles() {
        const el = document.getElementById('cockpit-lastfill');
        if (!_recentCycles.length) {
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Aucun cycle recent</span></div>';
            return;
        }

        const rows = _recentCycles.slice(0, MAX_CYCLES).map(c => {
            const symbol = c.symbol.replace('USDC', '');
            const sideClass = c.side === 'LONG' ? 'side-long' : 'side-short';
            const pnlPct = parseFloat(c.realized_pnl_pct) || 0;
            const pnlClass = pnlPct >= 0 ? 'pnl-positive' : 'pnl-negative';
            const pnlSign = pnlPct >= 0 ? '+' : '';
            const pnlUsd = parseFloat(c.realized_pnl) || 0;
            const pnlUsdSign = pnlUsd >= 0 ? '+' : '';
            const ago = c.closed_at ? Utils.timeAgoShort(c.closed_at) : '';
            const dur = c.duration || '';
            return `<div class="flex items-center justify-between py-1.5" onclick="App.switchTab('cycles')" style="cursor:pointer">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="text-xs font-bold">${symbol}</span>
                    <span class="cockpit-side ${sideClass}">${c.side}</span>
                    <span class="text-xs text-gray-500">${dur}</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-xs font-bold tabular-nums ${pnlClass}">${pnlSign}${pnlPct.toFixed(1)}%</span>
                    <span class="text-xs tabular-nums ${pnlClass}">${pnlUsdSign}$${Math.abs(pnlUsd).toFixed(0)}</span>
                    <span class="text-xs text-gray-500 shrink-0">${ago}</span>
                </div>
            </div>`;
        }).join('');

        el.innerHTML = `<div class="cockpit-card"><div class="text-xs text-gray-500 mb-1">Derniers cycles</div>${rows}</div>`;
    }

    function _renderWhale() {
        const el = document.getElementById('cockpit-whale');
        const alerts = _analysis && _analysis.whale_alerts ? _analysis.whale_alerts : [];
        if (!alerts.length) {
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Aucune alerte whale</span></div>';
            return;
        }

        const rows = alerts.slice(0, 10).map(w => {
            const sym = w.symbol.replace('USDC', '');
            const qty = Utils.fmtQuoteQty(w.quote_qty);
            const price = Utils.fmtPriceCompact(w.price);
            const ago = Utils.timeAgoShort(w.timestamp);
            const isBuy = w.side === 'BUY';
            const sideClass = isBuy ? 'side-long' : 'side-short';
            const label = isBuy ? 'Achat massif' : 'Vente massive';
            return `<div class="flex items-center justify-between py-1.5">
                <div class="flex items-center gap-1.5 min-w-0">
                    <span class="text-xs">&#x1F40B;</span>
                    <span class="cockpit-side ${sideClass}">${label}</span>
                    <span class="text-xs text-gray-300"><b>${qty}</b> de ${sym} \u00e0 ${price}</span>
                </div>
                <span class="text-xs text-gray-500 shrink-0 ml-2">${ago}</span>
            </div>`;
        }).join('');

        el.innerHTML = `<div class="cockpit-card cockpit-whale-card">${rows}</div>`;
    }

    function _renderOpportunities() {
        const el = document.getElementById('cockpit-opportunities');
        if (el) Opportunities.render(el);
    }

    // ── Market chart ──

    function _getMarketSymbol() {
        if (_positions.length) return _positions[0].symbol;
        return 'BTCUSDC';
    }

    function _renderMarketChart() {
        const el = document.getElementById('cockpit-market-chart');
        if (!el) return;
        const symbol = _getMarketSymbol();
        const displaySymbol = symbol.replace('USDC', '');
        // Skip re-render if symbol unchanged and card already built
        if (_marketSymbol === symbol && el.querySelector('.cockpit-card')) return;
        _marketSymbol = symbol;
        _marketFirstPrice = null;
        el.innerHTML = `
        <div class="cockpit-card">
            <div class="flex items-center justify-between mb-1">
                <div class="flex items-center gap-2">
                    <span class="text-sm font-bold">${displaySymbol}</span>
                    <span class="text-xs text-gray-500">24h</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-sm font-bold tabular-nums" id="market-chart-price">--</span>
                    <span class="text-xs tabular-nums" id="market-chart-change">--</span>
                </div>
            </div>
            <div id="cockpit-market-chart-container" style="height:120px;width:100%"></div>
        </div>`;
    }

    async function _initMarketChart() {
        _renderMarketChart();
        Charts.createCockpitMarketChart('cockpit-market-chart-container');
        const info = await Charts.loadCockpitMarketData(_marketSymbol);
        if (info) {
            _marketFirstPrice = info.price / (1 + info.change / 100);
            _updateMarketHeader(info.price, info.change);
        }
    }

    function _updateMarketHeader(price, changePct) {
        const priceEl = document.getElementById('market-chart-price');
        const changeEl = document.getElementById('market-chart-change');
        if (priceEl) priceEl.textContent = Utils.fmtPriceCompact(price);
        if (changeEl) {
            const sign = changePct >= 0 ? '+' : '';
            changeEl.textContent = `${sign}${changePct.toFixed(1)}%`;
            changeEl.className = `text-xs tabular-nums ${changePct >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
        }
    }

    // ── WS real-time updates ──

    WS.on('positions_snapshot', (data) => {
        const prev = _positions;
        _positions = data || [];
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderPortfolio();
        _renderPositions();
        // Market chart: switch symbol if needed
        const newSymbol = _getMarketSymbol();
        if (newSymbol !== _marketSymbol) _initMarketChart();
        // Position closed → refresh recent cycles
        if (prev.length > _positions.length) {
            fetch('/api/cycles?status=closed&limit=5')
                .then(r => r.ok ? r.json() : [])
                .then(c => { _recentCycles = c; _renderRecentCycles(); });
        }
    });

    WS.on('price_update', (data) => {
        if (!_marketSymbol || data.symbol !== _marketSymbol) return;
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        const price = parseFloat(data.price);
        if (!price || !isFinite(price)) return;
        if (_marketFirstPrice) {
            const changePct = ((price - _marketFirstPrice) / _marketFirstPrice) * 100;
            _updateMarketHeader(price, changePct);
        } else {
            const priceEl = document.getElementById('market-chart-price');
            if (priceEl) priceEl.textContent = Utils.fmtPriceCompact(price);
        }
    });

    WS.on('analysis_update', (data) => {
        _analysis = data;
        if (data.opportunities) Opportunities.update(data.opportunities);
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderBias();
        _renderWhale();
        _renderOpportunities();
    });

    BalanceStore.onChange(() => {
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderPortfolio();
    });

    return { load };
})();
