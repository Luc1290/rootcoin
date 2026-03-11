const Cockpit = (() => {
    let _positions = [];
    let _analysis = null;
    let _dayPnl = null;
    let _dayTrades = 0;
    let _lastDayPnlFetch = 0;
    let _news = [];
    let _recentCycles = [];
    let _prevPositionKeys = null;
    let _posChartIds = {};
    let _marketSymbol = null;
    let _marketFirstPrice = null;

    function _trailBadge(p) {
        if (p.trailing === 'trailing') return '<span class="badge" style="font-size:9px;padding:1px 5px;background:rgba(201,149,107,0.2);color:#c9956b">TRAIL</span>';
        if (p.trailing === 'watching') return '<span class="badge bg-stone-700/40 text-gray-400" style="font-size:9px;padding:1px 5px">TRAIL wait</span>';
        if (p.trailing === 'override') return '<span class="badge bg-yellow-900/40 text-yellow-400" style="font-size:9px;padding:1px 5px">TRAIL off</span>';
        if (p.trailing === 'naked') return '<span class="badge bg-red-900/50 text-red-400 animate-pulse" style="font-size:9px;padding:1px 5px">NAKED</span>';
        return '<span class="badge bg-red-900/30 text-red-500" style="font-size:9px;padding:1px 5px">NO TRAIL</span>';
    }

    function _cockpitStaleDot(priceAge) {
        if (priceAge == null) return '<span class="stale-dot stale"></span>';
        if (priceAge > 10) return '<span class="stale-dot stale"></span>';
        return '<span class="stale-dot fresh"></span>';
    }

    async function load() {
        try {
            const [, anaResp, oppResp, streaksResp, newsResp, cyclesResp] = await Promise.all([
                BalanceStore.load(),
                fetch('/api/analysis'),
                fetch('/api/opportunities'),
                fetch('/api/journal/streaks'),
                fetch('/api/news'),
                fetch('/api/cycles?status=closed&limit=10'),
            ]);
            if (anaResp.ok) _analysis = await anaResp.json();
            if (oppResp.ok) {
                const oppData = await oppResp.json();
                Opportunities.update(oppData.opportunities || []);
            }
            if (streaksResp.ok) {
                const s = await streaksResp.json();
                _dayPnl = parseFloat(s.day_pnl) || 0;
                _dayTrades = s.day_trades || 0;
            }
            if (newsResp.ok) {
                const newsData = await newsResp.json();
                _news = Array.isArray(newsData) ? newsData : (newsData.items || []);
            }
            if (cyclesResp.ok) {
                _recentCycles = await cyclesResp.json();
                if (!Array.isArray(_recentCycles)) _recentCycles = [];
            }
            render();
            Charts.createCockpitChart('cockpit-portfolio-chart');
            Charts.loadCockpitData();
            _initMarketChart();
            _loadTrackRecord();
            // Retry news if empty (backend may not have fetched yet)
            if (!_news.length) _retryNews();
        } catch (e) {
            console.error('Cockpit load failed', e);
        }
    }

    function render() {
        _renderPortfolio();
        _renderPositionCharts();
        _renderOpportunities();
        _renderContext();
    }

    // ── Portfolio (unchanged) ────────────────────────────────

    function _renderPortfolio() {
        const el = document.getElementById('cockpit-portfolio');
        const totalPnl = _positions.reduce((s, p) => s + (parseFloat(p.pnl_usd) || 0), 0);
        Charts.updateCockpitColor(totalPnl, _positions.length > 0);
        const portfolioTotal = BalanceStore.getTotal();
        const portfolioStr = portfolioTotal !== null ? 'Solde $' + portfolioTotal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--';

        // 24h PnL (realized + unrealized)
        const dayTotal = (_dayPnl || 0) + totalPnl;
        const dayPnlClass = dayTotal >= 0 ? 'pnl-positive' : 'pnl-negative';
        const daySign = dayTotal >= 0 ? '+' : '-';
        const dayPctStr = (portfolioTotal && portfolioTotal > 0 && _dayPnl !== null)
            ? `${dayTotal >= 0 ? '+' : ''}${(dayTotal / portfolioTotal * 100).toFixed(2)}%`
            : '';
        const dayStr = _dayPnl !== null
            ? `Gains 24h ${daySign}$${Math.abs(dayTotal).toFixed(2)}${dayPctStr ? ' (' + dayPctStr + ')' : ''}${_dayTrades > 0 ? '  ·  ' + _dayTrades + ' trade' + (_dayTrades > 1 ? 's' : '') : ''}`
            : '';

        // PnL ouvert (only if positions)
        const hasPositions = _positions.length > 0;
        let openStr = '';
        let openClass = '';
        if (hasPositions) {
            const openSign = totalPnl >= 0 ? '+' : '';
            openClass = totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            let weightedPct = 0, totalCost = 0;
            for (const p of _positions) {
                const cost = (parseFloat(p.entry_price) || 0) * (parseFloat(p.quantity) || 0);
                weightedPct += (parseFloat(p.pnl_pct) || 0) * cost;
                totalCost += cost;
            }
            weightedPct = totalCost > 0 ? weightedPct / totalCost : 0;
            openStr = `${openSign}$${Math.abs(totalPnl).toFixed(2)} (${weightedPct >= 0 ? '+' : ''}${weightedPct.toFixed(2)}%)`;
        }

        // Fast update path
        const totalSpan = el.querySelector('[data-field="total"]');
        if (totalSpan) {
            totalSpan.textContent = portfolioStr;
            const dayEl = el.querySelector('[data-field="day"]');
            if (dayEl) { dayEl.textContent = dayStr; dayEl.className = `text-sm tabular-nums ${dayPnlClass}`; }
            const openEl = el.querySelector('[data-field="open"]');
            if (openEl) {
                if (hasPositions) {
                    openEl.textContent = openStr;
                    openEl.className = `text-sm font-bold tabular-nums ${openClass}`;
                    openEl.parentElement.classList.remove('hidden');
                } else {
                    openEl.parentElement.classList.add('hidden');
                }
            }
            return;
        }

        el.innerHTML = `
        <div class="cockpit-card">
            <div class="flex items-center gap-3 justify-end flex-wrap">
                <span class="text-lg font-bold tabular-nums" data-field="total">${portfolioStr}</span>
                ${dayStr ? `<span class="cockpit-sep"></span><span class="text-sm tabular-nums ${dayPnlClass}" data-field="day">${dayStr}</span>` : '<span data-field="day"></span>'}
            </div>
            <div class="flex items-center gap-3 justify-end mt-1 ${hasPositions ? '' : 'hidden'}">
                <span class="text-xs text-gray-500">PnL ouvert</span>
                <span class="text-sm font-bold tabular-nums ${openClass}" data-field="open">${openStr}</span>
            </div>
            <div id="cockpit-portfolio-chart" style="height:80px;width:100%;margin-top:8px"></div>
        </div>`;
    }

    // ── Position mini-charts ─────────────────────────────────

    function _renderPositionCharts() {
        const el = document.getElementById('cockpit-positions');
        if (!_positions.length) {
            _destroyPosCharts();
            _prevPositionKeys = null;
            // Show market chart as fallback
            _renderMarketChart(el);
            return;
        }

        const keys = _positions.map(p => `${p.id}:${p.symbol}:${p.side}`).join(',');
        if (_prevPositionKeys === keys) {
            // Just update PnL overlays for existing charts
            _updatePosOverlays();
            return;
        }

        _prevPositionKeys = keys;
        _destroyPosCharts();

        const cards = _positions.map(p => {
            const sym = p.symbol.replace('USDC', '');
            const dirClass = p.side === 'LONG' ? 'long' : 'short';
            const pnl = parseFloat(p.pnl_pct) || 0;
            const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            const pnlSign = pnl >= 0 ? '+' : '';
            const pnlUsd = parseFloat(p.pnl_usd) || 0;
            const pnlUsdSign = pnlUsd >= 0 ? '+' : '';
            const containerId = `pos-chart-${p.id}`;

            const entryPrice = parseFloat(p.entry_price) || 0;
            const qty = parseFloat(p.quantity) || 0;
            const slPrice = parseFloat(p.sl_price) || 0;
            const tpPrice = parseFloat(p.tp_price) || 0;

            let levelsHtml = '';
            if (entryPrice) {
                const dot = '<span style="color:#555;margin:0 2px">&middot;</span>';
                const parts = [];
                parts.push(`<span style="color:#c9956b">Entry <b>${Utils.fmtPriceCompact(entryPrice)}</b></span>`);
                if (slPrice) {
                    const rawDist = ((slPrice - entryPrice) / entryPrice * 100);
                    const dist = p.side === 'SHORT' ? -rawDist : rawDist;
                    const slInProfit = dist > 0;
                    const slColor = slInProfit ? '#22c55e' : '#ef4444';
                    const gross = p.side === 'SHORT' ? (entryPrice - slPrice) * qty : (slPrice - entryPrice) * qty;
                    const exitFees = slPrice * qty * 0.001;
                    const slPnl = gross - (parseFloat(p.entry_fees_usd) || 0) - exitFees;
                    const slPnlStr = `${slPnl >= 0 ? '+' : '-'}$${Math.abs(slPnl).toFixed(0)}`;
                    const distStr = `${dist > 0 ? '+' : ''}${dist.toFixed(1)}%`;
                    parts.push(`<span style="color:${slColor}">SL <b>${Utils.fmtPriceCompact(slPrice)}</b>${dot}<span style="opacity:0.7">${distStr}</span>${dot}${slPnlStr}</span>`);
                }
                if (tpPrice) {
                    const rawDist = ((tpPrice - entryPrice) / entryPrice * 100);
                    const dist = p.side === 'SHORT' ? -rawDist : rawDist;
                    const gross = p.side === 'SHORT' ? (entryPrice - tpPrice) * qty : (tpPrice - entryPrice) * qty;
                    const exitFees = tpPrice * qty * 0.001;
                    const tpPnl = gross - (parseFloat(p.entry_fees_usd) || 0) - exitFees;
                    const tpPnlStr = `${tpPnl >= 0 ? '+' : '-'}$${Math.abs(tpPnl).toFixed(0)}`;
                    const distStr = `${dist > 0 ? '+' : ''}${dist.toFixed(1)}%`;
                    parts.push(`<span style="color:#22c55e">TP <b>${Utils.fmtPriceCompact(tpPrice)}</b>${dot}<span style="opacity:0.7">${distStr}</span>${dot}${tpPnlStr}</span>`);
                }
                levelsHtml = `<div class="flex flex-wrap gap-x-3 gap-y-0 mt-1" style="font-size:10px;opacity:0.8">${parts.join('')}</div>`;
            }

            return `<div class="mini-chart-card ${dirClass}" data-pos-id="${p.id}" onclick="App.switchTab('positions')" style="cursor:pointer">
                <div class="flex items-center justify-between mb-1">
                    <div class="flex items-center gap-2">
                        <span class="card-type-tag tag-position">Position</span>
                        <span class="text-sm font-bold">${sym}</span>
                        <span class="cockpit-side side-${p.side.toLowerCase()}">${p.side}</span>${_trailBadge(p)}
                    </div>
                    <div class="flex items-center gap-2">
                        <span class="text-xs text-gray-400 tabular-nums" data-field="price">${Utils.fmtPriceCompact(p.current_price)}</span><span data-field="stale-dot">${_cockpitStaleDot(p.price_age)}</span>
                        <span class="text-sm font-bold tabular-nums ${pnlClass}" data-field="pnl-pct">${pnlSign}${pnl.toFixed(1)}%</span>
                        <span class="text-xs tabular-nums ${pnlClass}" data-field="pnl-usd">${pnlUsdSign}$${Math.abs(pnlUsd).toFixed(0)}</span>
                    </div>
                </div>
                <div id="${containerId}" style="height:140px;width:100%"></div>
                ${levelsHtml}
            </div>`;
        }).join('');

        el.innerHTML = `<div class="space-y-2">${cards}</div>`;

        // Create mini-charts for each position
        for (const p of _positions) {
            const containerId = `pos-chart-${p.id}`;
            const entryPrice = parseFloat(p.entry_price) || 0;
            const slPrice = parseFloat(p.sl_price) || 0;
            const tpPrice = parseFloat(p.tp_price) || 0;

            const chartId = MiniTradeChart.create(containerId, {
                symbol: p.symbol,
                height: 140,
                entryPrice,
                slPrice,
                tpPrice,
                showLineLabels: false,
            });

            if (chartId) {
                _posChartIds[p.id] = chartId;
                if (p.opened_at) MiniTradeChart.addMarker(chartId, p.opened_at, p.side || 'LONG', p.entry_price);

                // Dynamic interval: scale up to ensure entry is visible within ~1000 candles max
                let interval = '1m', lookback = 1440;
                if (p.opened_at) {
                    const ageMin = (Date.now() - new Date(p.opened_at).getTime()) / 60000;
                    if (ageMin > 40000) { // > 27 days
                        interval = '4h';
                        lookback = Math.max(120, Math.min(1000, Math.ceil((ageMin / 240) * 1.5) + 50));
                    } else if (ageMin > 10000) { // > 7 days
                        interval = '1h';
                        lookback = Math.max(120, Math.min(1000, Math.ceil((ageMin / 60) * 1.5) + 50));
                    } else if (ageMin > 3000) { // > 2 days
                        interval = '15m';
                        lookback = Math.max(120, Math.min(1000, Math.ceil((ageMin / 15) * 1.5) + 50));
                    } else if (ageMin > 1380) { // > 23h
                        interval = '5m';
                        lookback = Math.max(120, Math.min(1440, Math.ceil((ageMin / 5) * 1.5) + 50));
                    } else {
                        // For new positions, show at least 240 mins (4h) of context to keep arrows small
                        lookback = Math.max(240, Math.min(1440, Math.ceil(ageMin * 1.5) + 60));
                    }
                }
                MiniTradeChart.fetchAndRender(chartId, p.symbol, interval, lookback);
            }
        }
    }

    function _updatePosOverlays() {
        for (const p of _positions) {
            const card = document.querySelector(`[data-pos-id="${p.id}"]`);
            if (!card) continue;
            const pnl = parseFloat(p.pnl_pct) || 0;
            const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            const pnlSign = pnl >= 0 ? '+' : '';
            const pnlUsd = parseFloat(p.pnl_usd) || 0;
            const pnlUsdSign = pnlUsd >= 0 ? '+' : '';

            const priceEl = card.querySelector('[data-field="price"]');
            const pctEl = card.querySelector('[data-field="pnl-pct"]');
            const usdEl = card.querySelector('[data-field="pnl-usd"]');

            if (priceEl) priceEl.textContent = Utils.fmtPriceCompact(p.current_price);
            const dotEl = card.querySelector('[data-field="stale-dot"]');
            if (dotEl) dotEl.innerHTML = _cockpitStaleDot(p.price_age);
            if (pctEl) {
                pctEl.textContent = `${pnlSign}${pnl.toFixed(1)}%`;
                pctEl.className = `text-sm font-bold tabular-nums ${pnlClass}`;
            }
            if (usdEl) {
                usdEl.textContent = `${pnlUsdSign}$${Math.abs(pnlUsd).toFixed(0)}`;
                usdEl.className = `text-xs tabular-nums ${pnlClass}`;
            }

            // Update SL/TP lines if changed
            const chartId = _posChartIds[p.id];
            if (chartId) {
                MiniTradeChart.updateLevels(chartId, {
                    slPrice: parseFloat(p.sl_price) || 0,
                    tpPrice: parseFloat(p.tp_price) || 0,
                });
            }
        }
    }

    function _destroyPosCharts() {
        for (const chartId of Object.values(_posChartIds)) {
            MiniTradeChart.destroy(chartId);
        }
        _posChartIds = {};
    }

    // ── Market chart (fallback when no positions) ──────────

    function _getMarketSymbol() {
        if (_positions.length) return _positions[0].symbol;
        return 'BTCUSDC';
    }

    function _renderMarketChart(el) {
        const symbol = _getMarketSymbol();
        const displaySymbol = symbol.replace('USDC', '');
        if (_marketSymbol === symbol && el.querySelector('.cockpit-card')) return;
        _marketSymbol = symbol;
        _marketFirstPrice = null;
        el.innerHTML = `
        <div class="cockpit-card">
            <div class="flex items-center justify-between mb-1">
                <div class="flex items-center gap-2">
                    <span class="card-type-tag tag-market">Marche</span>
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
        if (_positions.length) return; // positions have their own charts
        const el = document.getElementById('cockpit-positions');
        if (!el) return;
        _renderMarketChart(el);
        Charts.createCockpitMarketChart('cockpit-market-chart-container');
        const info = await Charts.loadCockpitMarketData(_getMarketSymbol());
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

    // ── Opportunities ────────────────────────────────────────

    function _renderOpportunities() {
        const el = document.getElementById('cockpit-opportunities');
        if (el) Opportunities.render(el, 1);
    }

    // ── Track Record ─────────────────────────────────────────

    let _trackHistory = [];
    let _trackStats = {};

    async function _loadTrackRecord() {
        try {
            const [histResp, statsResp] = await Promise.all([
                fetch('/api/opportunities/history?limit=50'),
                fetch('/api/opportunities/stats'),
            ]);

            if (histResp.ok) _trackHistory = (await histResp.json()).history || [];
            if (statsResp.ok) _trackStats = await statsResp.json();

            _renderContext();
        } catch (e) {
            console.error('Track record load failed', e);
        }
    }

    const REF_SIZE = 40000; // reference position $40k

    function _buildTrackRecordCard() {
        const history = _trackHistory;
        const stats = _trackStats;
        if (!history.length && !stats.total) return '';

        const winRate = stats.win_rate || 0;
        const total = stats.total || 0;
        const totalPnl = stats.total_pnl_pct || 0;
        const totalPnlClass = totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const totalUsd = totalPnl / 100 * REF_SIZE;
        const totalUsdStr = `${totalUsd >= 0 ? '+' : '-'}$${Math.abs(totalUsd).toFixed(0)}`;
        const avgWin = stats.avg_win_pct || 0;
        const avgLoss = stats.avg_loss_pct || 0;

        const rows = history.map(r => {
            const sym = r.symbol.replace('USDC', '');
            const dirIcon = r.direction === 'LONG' ? '&#x2191;' : '&#x2193;';
            const dirClass = r.direction === 'LONG' ? 'pnl-positive' : 'pnl-negative';
            const statusCls = r.status;
            const statusLabel = r.status === 'tp_hit' ? 'TP' : r.status === 'sl_hit' ? 'SL' : r.status === 'expired' ? 'Exp' : r.status === 'taken' ? 'Ouvert' : r.status;
            const pnl = r.outcome_pnl_pct ? parseFloat(r.outcome_pnl_pct) : null;
            const pnlStr = pnl !== null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%` : '--';
            const pnlClass = pnl !== null ? (pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : 'text-gray-500';
            const pnlUsd = pnl !== null ? pnl / 100 * REF_SIZE : null;
            const pnlUsdStr = pnlUsd !== null ? `${pnlUsd >= 0 ? '+' : '-'}$${Math.abs(pnlUsd).toFixed(0)}` : '';
            const ago = r.detected_at ? Utils.timeAgoShort(r.detected_at) : '';

            // Duration: time between detection and resolution
            let durationStr = '';
            if (r.detected_at && r.resolved_at) {
                const ms = new Date(r.resolved_at) - new Date(r.detected_at);
                const mins = Math.floor(ms / 60000);
                if (mins < 60) durationStr = `${mins}m`;
                else if (mins < 1440) durationStr = `${Math.floor(mins / 60)}h${mins % 60 ? (mins % 60) + 'm' : ''}`;
                else durationStr = `${Math.floor(mins / 1440)}j`;
            }

            // R:R badge
            const rr = r.rr ? parseFloat(r.rr) : null;
            const rrStr = rr !== null ? `${rr.toFixed(1)}` : '';
            const rrColor = rr !== null ? (rr >= 2 ? '#c9956b' : rr >= 1.5 ? '#a78b6d' : '#6b7280') : '';

            return `<div class="track-record-row">
                <div class="flex items-center gap-1" style="min-width:0">
                    <span class="text-xs font-bold">${sym}</span>
                    <span class="text-xs ${dirClass}">${dirIcon}</span>
                    <span class="track-record-status ${statusCls}">${statusLabel}</span>
                    ${rrStr ? `<span class="text-xs tabular-nums font-semibold" style="font-size:9px;color:${rrColor}">${rrStr}R</span>` : ''}
                    ${durationStr ? `<span class="text-xs text-gray-500" style="font-size:9px">${durationStr}</span>` : ''}
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                    <span class="text-xs font-bold tabular-nums ${pnlClass}">${pnlStr}</span>
                    ${pnlUsdStr ? `<span class="text-xs tabular-nums ${pnlClass}" style="font-size:9px;opacity:0.8">${pnlUsdStr}</span>` : ''}
                    <span class="text-xs text-gray-500">${ago}</span>
                </div>
            </div>`;
        }).join('');

        return `<div class="cockpit-card" style="border-left:3px solid #c9956b">
            <div class="flex items-center justify-between mb-1">
                <span class="text-xs text-gray-500">Track Record</span>
                <span class="text-xs text-gray-500">/ $${(REF_SIZE/1000).toFixed(0)}k</span>
            </div>
            <div class="flex items-center gap-3 text-xs mb-2">
                <span class="text-gray-400">${total} sig</span>
                <span class="font-bold ${winRate >= 50 ? 'pnl-positive' : 'pnl-negative'}">${winRate}%</span>
                <span class="font-bold ${totalPnlClass}">${totalUsdStr}</span>
                <span class="text-gray-500" style="font-size:9px">moy W <span class="pnl-positive">+${avgWin.toFixed(2)}%</span> L <span class="pnl-negative">${avgLoss.toFixed(2)}%</span></span>
            </div>
            <div style="max-height:290px;overflow-y:auto">${rows}</div>
        </div>`;
    }

    // ── Context (macro + whales + news — separate cards) ────

    function _renderContext() {
        const el = document.getElementById('cockpit-context');
        if (!el) return;

        // Preserve open/closed state of collapsible sections
        const macroWasOpen = el.querySelector('.context-macro')?.hasAttribute('open') ?? true;

        const macroHtml = _buildMacroCard(macroWasOpen);
        const whaleHtml = _buildWhaleCard();
        const cyclesHtml = _buildCyclesCard();
        const trackHtml = _buildTrackRecordCard();
        const newsHtml = _buildNewsCard();

        // Macro + Whales + Cycles + Track Record side by side, News full-width below
        el.innerHTML = `
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">${macroHtml}${whaleHtml}${cyclesHtml}${trackHtml}</div>
            ${newsHtml}`;
    }

    const _cryptoImpact = {
        dxy: 'inverse', vix: 'inverse', nasdaq: 'direct', sp500: 'direct', gold: 'inverse',
        us10y: 'inverse', spread: 'spread', oil: 'inverse', usdjpy: 'direct',
        mstr: 'direct', ibit: 'direct', googl: 'direct', nvda: 'direct',
        cac40: 'direct', dax: 'direct', eurusd: 'inverse',
    };
    const _macroLabels = {
        dxy: 'DXY', vix: 'VIX', nasdaq: 'NDQ', sp500: 'S&P', gold: 'Gold',
        us10y: '10Y', spread: '10-5Y', oil: 'Oil', usdjpy: 'JPY',
        mstr: 'MSTR', ibit: 'IBIT', googl: 'GOOGL', nvda: 'NVDA',
        cac40: 'CAC', dax: 'DAX', eurusd: 'EUR',
    };

    function _buildMacroCard(wasOpen) {
        if (!_analysis || !_analysis.macro || !_analysis.macro.indicators) return '';
        const openAttr = wasOpen !== false ? ' open' : '';

        const indicators = _analysis.macro.indicators;
        const items = Object.entries(indicators).map(([name, data]) => {
            const label = _macroLabels[name] || name;
            const val = data.value !== undefined ? parseFloat(data.value) : null;
            const valStr = val !== null ? val.toFixed(name === 'vix' ? 1 : 2) : '--';
            const change = parseFloat(data.change_pct || 0);
            const changeStr = change ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : '';

            // Trend arrow (asset direction)
            const trendIcon = change > 0 ? '&#x25B2;' : change < 0 ? '&#x25BC;' : '&#x2022;';
            const trendClass = change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : 'text-gray-400';

            // Crypto impact
            const impact = _cryptoImpact[name];
            let impactClass = 'text-gray-400';
            let impactIcon = '&#x2022;';
            if (impact === 'spread') {
                const sv = parseFloat(data.value || 0);
                impactClass = sv < 0 ? 'pnl-negative' : sv > 0.5 ? 'pnl-positive' : 'text-yellow-400';
                impactIcon = sv < 0 ? '&#x25BC;' : sv > 0.5 ? '&#x25B2;' : '&#x2022;';
            } else if (impact === 'inverse') {
                impactClass = change < 0 ? 'pnl-positive' : change > 0 ? 'pnl-negative' : 'text-gray-400';
                impactIcon = change < 0 ? '&#x25B2;' : change > 0 ? '&#x25BC;' : '&#x2022;';
            } else if (impact === 'direct') {
                impactClass = change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : 'text-gray-400';
                impactIcon = change > 0 ? '&#x25B2;' : change < 0 ? '&#x25BC;' : '&#x2022;';
            }

            return `<div class="macro-row">
                <span class="macro-label">${label}</span>
                <span class="macro-val tabular-nums">${valStr}</span>
                <span class="macro-trend ${trendClass}">${trendIcon}</span>
                <span class="macro-chg tabular-nums">${changeStr}</span>
                <span class="macro-impact ${impactClass}" title="crypto">c${impactIcon}</span>
            </div>`;
        }).join('');

        return `<details class="context-section context-macro cockpit-card"${openAttr}>
            <summary>Macro</summary>
            <div>${items}</div>
        </details>`;
    }

    function _buildWhaleCard() {
        const alerts = _analysis && _analysis.whale_alerts ? _analysis.whale_alerts : [];
        if (!alerts.length) return '';

        const rows = alerts.slice(0, 10).map(w => {
            const sym = w.symbol.replace('USDC', '');
            const qty = Utils.fmtQuoteQty(w.quote_qty);
            const price = Utils.fmtPriceCompact(w.price);
            const ago = Utils.timeAgoShort(w.timestamp);
            const isBuy = w.side === 'BUY';
            const sideClass = isBuy ? 'side-long' : 'side-short';
            const label = isBuy ? 'Achat' : 'Vente';
            return `<div class="flex items-center gap-1.5 py-0.5 text-xs leading-tight">
                <span class="cockpit-side ${sideClass}">${label}</span>
                <span class="text-gray-300"><b>${qty}</b> ${sym} @ ${price}</span>
                <span class="text-gray-500">&middot; ${ago}</span>
            </div>`;
        }).join('');

        return `<div class="cockpit-card cockpit-whale-card">
            <div class="text-xs text-gray-500 mb-1">Whales</div>
            ${rows}
        </div>`;
    }

    function _buildCyclesCard() {
        if (!_recentCycles.length) return '';

        const rows = _recentCycles.slice(0, 10).map(c => {
            const sym = c.symbol.replace('USDC', '');
            const pnl = c.realized_pnl_pct ? parseFloat(c.realized_pnl_pct) : null;
            const pnlStr = pnl !== null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%` : '--';
            const pnlClass = pnl !== null ? (pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : 'text-gray-500';
            const pnlUsd = c.realized_pnl ? parseFloat(c.realized_pnl) : null;
            const pnlUsdStr = pnlUsd !== null ? `${pnlUsd >= 0 ? '+' : ''}$${Math.abs(pnlUsd).toFixed(0)}` : '';
            const dirIcon = c.side === 'LONG' ? '&#x2191;' : '&#x2193;';
            const dirClass = c.side === 'LONG' ? 'pnl-positive' : 'pnl-negative';
            const ago = c.closed_at ? Utils.timeAgoShort(c.closed_at) : '';
            const dur = c.duration || '';

            return `<div class="flex items-center gap-1.5 py-0.5 text-xs leading-tight">
                <span class="${dirClass}">${dirIcon}</span>
                <span class="text-gray-300 font-semibold">${sym}</span>
                <span class="font-bold tabular-nums ${pnlClass}">${pnlStr}</span>
                ${pnlUsdStr ? `<span class="tabular-nums ${pnlClass}" style="font-size:10px">${pnlUsdStr}</span>` : ''}
                <span class="text-gray-500">&middot; ${dur || ago}</span>
            </div>`;
        }).join('');

        return `<div class="cockpit-card" style="border-left:3px solid #a78b6d;cursor:pointer" onclick="App.switchTab('cycles')">
            <div class="text-xs text-gray-500 mb-1">Derniers cycles</div>
            ${rows}
        </div>`;
    }

    function _buildNewsCard() {
        if (!_news || !_news.length) return '';

        const crypto = [], macro = [], general = [];
        for (const item of _news) {
            const cat = item.category || '';
            const feed = item.feed || '';
            if (cat === 'Markets' || cat === 'crypto' || feed === 'google_crypto') crypto.push(item);
            else if (cat === 'macro' || feed === 'google_macro') macro.push(item);
            else general.push(item);
        }

        return `<div class="cockpit-card mt-2">
            <div class="text-xs text-gray-500 mb-2">News</div>
            <div class="news-grid">
                ${_buildNewsColumn('Crypto', crypto, 'news-cat-crypto')}
                ${_buildNewsColumn('Macro', macro, 'news-cat-macro')}
                ${_buildNewsColumn('General', general, 'news-cat-other')}
            </div>
        </div>`;
    }

    function _buildNewsColumn(title, items, catClass) {
        const rows = items.slice(0, 6).map(n => {
            const t = n.title_fr || n.title || '';
            const ago = n.published_at ? Utils.timeAgoShort(n.published_at) : '';
            const src = n.source || '';
            const href = n.link || '#';
            return `<a href="${href}" target="_blank" rel="noopener" class="news-item">
                <div class="text-xs text-gray-200 leading-snug line-clamp-2">${t}</div>
                <div class="flex items-center gap-2 mt-0.5">
                    <span class="text-xs text-gray-600">${src}</span>
                    <span class="text-xs text-gray-600">${ago}</span>
                </div>
            </a>`;
        }).join('');

        return `<div class="news-column">
            <div class="flex items-center gap-2 mb-2">
                <span class="news-category ${catClass}">${title}</span>
                <span class="text-xs text-gray-600">${items.length}</span>
            </div>
            <div class="news-list">${rows || '<div class="text-xs text-gray-600 py-2">Aucune</div>'}</div>
        </div>`;
    }

    // ── WS real-time updates ─────────────────────────────────

    WS.on('positions_snapshot', (data) => {
        const prev = _positions;
        _positions = data || [];
        window._cockpitPositions = _positions;
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        // Refresh realized 24h PnL every 30s
        const now = Date.now();
        if (now - _lastDayPnlFetch > 30000) {
            _lastDayPnlFetch = now;
            fetch('/api/journal/streaks').then(r => r.ok ? r.json() : null).then(s => {
                if (!s) return;
                _dayPnl = parseFloat(s.day_pnl) || 0;
                _dayTrades = s.day_trades || 0;
                _renderPortfolio();
            }).catch(() => {});
        }
        _renderPortfolio();
        _renderPositionCharts();
        // Switch to market chart if positions closed, or to position charts if opened
        const newSymbol = _getMarketSymbol();
        if (!_positions.length && newSymbol !== _marketSymbol) _initMarketChart();
    });

    WS.on('price_update', (data) => {
        if (!_marketSymbol || data.symbol !== _marketSymbol || _positions.length) return;
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

    WS.on('kline_update', (data) => {
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        if (data.interval !== '5m') return;
        // Feed kline to position mini-charts
        for (const p of _positions) {
            const chartId = _posChartIds[p.id];
            if (chartId && p.symbol === data.symbol) {
                MiniTradeChart.appendCandle(chartId, {
                    time: data.open_time,
                    open: data.open,
                    high: data.high,
                    low: data.low,
                    close: data.close,
                });
            }
        }
    });

    WS.on('analysis_update', (data) => {
        _analysis = data;
        if (data.opportunities) Opportunities.update(data.opportunities);
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderOpportunities();
        _renderContext();
    });

    BalanceStore.onChange(() => {
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderPortfolio();
    });

    async function _retryNews(attempt = 0) {
        if (attempt >= 3) return;
        await new Promise(r => setTimeout(r, 10_000)); // wait 10s
        try {
            const resp = await fetch('/api/news');
            if (resp.ok) {
                const newsData = await resp.json();
                _news = Array.isArray(newsData) ? newsData : (newsData.items || []);
                if (_news.length) {
                    _renderContext();
                    return;
                }
            }
        } catch {}
        _retryNews(attempt + 1);
    }

    // Periodic news refresh (every 2 min)
    setInterval(async () => {
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        try {
            const resp = await fetch('/api/news');
            if (resp.ok) {
                const newsData = await resp.json();
                _news = Array.isArray(newsData) ? newsData : (newsData.items || []);
                _renderContext();
            }
        } catch {}
    }, 120_000);

    return { load };
})();
