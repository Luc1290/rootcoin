const Journal = (() => {
    let _initialized = false;
    let _calendarYear = new Date().getFullYear();
    let _equityChart = null;
    let _equitySeries = null;
    let _ddSeries = null;
    let _currentHours = 720;
    let _entries = [];
    let _offset = 0;
    const PAGE_SIZE = 30;
    let _symbols = new Set();

    function init() {
        if (_initialized) return;
        _initialized = true;

        document.querySelectorAll('#equity-ranges .chart-range-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('#equity-ranges .chart-range-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                _currentHours = parseInt(btn.dataset.hours);
                _loadEquity();
            });
        });

        document.getElementById('journal-year-prev').addEventListener('click', () => {
            _calendarYear--;
            _loadCalendar();
        });
        document.getElementById('journal-year-next').addEventListener('click', () => {
            _calendarYear++;
            _loadCalendar();
        });

        document.getElementById('journal-symbol-filter').addEventListener('change', () => {
            _offset = 0;
            _entries = [];
            _loadEntries();
        });

        document.getElementById('journal-load-more').addEventListener('click', _loadEntries);
    }

    async function load() {
        _offset = 0;
        _entries = [];
        await Promise.all([_loadEquity(), _loadStreaks(), _loadCalendar(), _loadEntries()]);
    }

    // ── Equity curve ────────────────────────────────────────

    async function _loadEquity() {
        try {
            const resp = await fetch(`/api/journal/equity?hours=${_currentHours}`);
            if (!resp.ok) return;
            const data = await resp.json();

            document.getElementById('journal-max-dd').textContent =
                data.max_drawdown_pct !== '0' ? `-${data.max_drawdown_pct}%` : '0%';
            document.getElementById('journal-current-dd').textContent =
                data.current_drawdown_pct !== '0' ? `-${data.current_drawdown_pct}%` : '0%';

            const currentDd = parseFloat(data.current_drawdown_pct);
            const ddEl = document.getElementById('journal-current-dd');
            ddEl.className = 'font-bold text-base ' + (currentDd > 0 ? 'pnl-negative' : 'text-gray-400');

            if (data.points.length > 0) {
                const lastVal = parseFloat(data.points[data.points.length - 1].total_usd);
                document.getElementById('journal-portfolio-val').textContent = `$${lastVal.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
            }

            _renderEquityChart(data.points);
        } catch (e) {
            console.error('Journal equity load failed', e);
        }
    }

    function _renderEquityChart(points) {
        const container = document.getElementById('journal-equity-chart');
        if (!points.length) {
            container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">Pas de donnees portfolio</div>';
            return;
        }

        if (!_equityChart) {
            _equityChart = LightweightCharts.createChart(container, {
                width: container.clientWidth,
                height: 250,
                layout: { background: { color: 'transparent' }, textColor: '#9ca3af' },
                grid: {
                    vertLines: { color: 'rgba(255,255,255,0.03)' },
                    horzLines: { color: 'rgba(255,255,255,0.03)' },
                },
                rightPriceScale: { borderColor: '#374151' },
                timeScale: { borderColor: '#374151', timeVisible: true, secondsVisible: false },
                crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
            });
            _equitySeries = _equityChart.addAreaSeries({
                lineColor: '#22c55e',
                topColor: 'rgba(34,197,94,0.15)',
                bottomColor: 'rgba(34,197,94,0)',
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: true,
            });

            const ro = new ResizeObserver(() => {
                if (_equityChart) _equityChart.applyOptions({ width: container.clientWidth });
            });
            ro.observe(container);
        }

        const seen = new Set();
        const eqData = [];
        for (const p of points) {
            const t = Math.floor(new Date(p.snapshot_at + (p.snapshot_at.endsWith('Z') ? '' : 'Z')).getTime() / 1000);
            if (!seen.has(t) && !isNaN(t)) {
                seen.add(t);
                eqData.push({ time: t, value: parseFloat(p.total_usd) });
            }
        }
        eqData.sort((a, b) => a.time - b.time);
        _equitySeries.setData(eqData);
        _equityChart.timeScale().fitContent();
    }

    // ── Streak tracker ─────────────────────────────────────

    async function _loadStreaks() {
        try {
            const resp = await fetch('/api/journal/streaks');
            if (!resp.ok) return;
            const d = await resp.json();

            const flame = document.getElementById('streak-flame');
            const countEl = document.getElementById('streak-current-count');
            const labelEl = document.getElementById('streak-current-label');

            if (d.current_streak > 0) {
                const isWin = d.current_streak_type === 'win';
                countEl.textContent = d.current_streak;
                countEl.className = 'streak-value ' + (isWin ? 'pnl-positive' : 'pnl-negative');
                labelEl.textContent = isWin
                    ? `trade${d.current_streak > 1 ? 's' : ''} gagnant${d.current_streak > 1 ? 's' : ''}`
                    : `trade${d.current_streak > 1 ? 's' : ''} perdant${d.current_streak > 1 ? 's' : ''}`;
                flame.classList.toggle('streak-flame-active', isWin);
            } else {
                countEl.textContent = '0';
                countEl.className = 'streak-value text-gray-400';
                labelEl.textContent = 'Aucun trade';
                flame.classList.remove('streak-flame-active');
            }

            const winrateEl = document.getElementById('streak-month-winrate');
            const detailEl = document.getElementById('streak-month-detail');
            if (d.month_trades > 0) {
                winrateEl.textContent = d.month_win_rate + '%';
                winrateEl.className = 'streak-value ' + (d.month_win_rate >= 50 ? 'pnl-positive' : 'pnl-negative');
                const pnl = parseFloat(d.month_pnl);
                const pnlStr = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
                const portfolioStr = d.month_portfolio_change !== 0
                    ? ` · ${d.month_portfolio_change > 0 ? '+' : ''}${d.month_portfolio_change}%`
                    : '';
                detailEl.textContent = `${d.month_wins}/${d.month_trades} · ${pnlStr}${portfolioStr}`;
            } else {
                winrateEl.textContent = '--';
                winrateEl.className = 'streak-value text-gray-400';
                detailEl.textContent = 'Aucun trade ce mois';
            }

            const bestCountEl = document.getElementById('streak-best-count');
            const bestLabelEl = document.getElementById('streak-best-label');
            if (d.best_streak > 0) {
                bestCountEl.textContent = d.best_streak;
                bestCountEl.className = 'streak-value pnl-positive';
                bestLabelEl.textContent = d.best_streak_month
                    ? `wins · ${d.best_streak_month}`
                    : `wins consecutifs`;
            } else {
                bestCountEl.textContent = '0';
                bestCountEl.className = 'streak-value text-gray-400';
                bestLabelEl.textContent = 'Meilleure serie';
            }
        } catch (e) {
            console.error('Journal streaks load failed', e);
        }
    }

    // ── Calendar heatmap ────────────────────────────────────

    async function _loadCalendar() {
        document.getElementById('journal-year-label').textContent = _calendarYear;
        try {
            const tzOffset = -new Date().getTimezoneOffset();
            const resp = await fetch(`/api/journal/calendar?year=${_calendarYear}&tz_offset=${tzOffset}`);
            if (!resp.ok) return;
            const data = await resp.json();
            _renderCalendar(data);
        } catch (e) {
            console.error('Journal calendar load failed', e);
        }
    }

    function _renderCalendar(data) {
        const container = document.getElementById('journal-calendar');
        const dayMap = {};
        for (const d of data.days) {
            dayMap[d.date] = d;
        }

        const year = data.year;
        const jan1 = new Date(year, 0, 1);
        const dec31 = new Date(year, 11, 31);
        const startDay = jan1.getDay(); // 0=Sun

        // Build weeks grid: 7 rows x ~53 cols
        const weeks = [];
        const dt = new Date(jan1);
        // Rewind to start of week
        dt.setDate(dt.getDate() - startDay);

        while (dt <= dec31 || dt.getDay() !== 0) {
            const weekIdx = weeks.length === 0 ? 0 : weeks.length;
            if (dt.getDay() === 0) weeks.push([]);
            const dateStr = _fmtDate(dt);
            const inYear = dt.getFullYear() === year;
            weeks[weeks.length - 1].push({ date: dateStr, inYear });
            dt.setDate(dt.getDate() + 1);
            if (weeks[weeks.length - 1].length === 7 && (dt > dec31 && dt.getDay() === 0)) break;
        }

        // Pad last week
        while (weeks.length > 0 && weeks[weeks.length - 1].length < 7) {
            const d2 = new Date(dt);
            weeks[weeks.length - 1].push({ date: _fmtDate(d2), inYear: false });
            dt.setDate(dt.getDate() + 1);
        }

        // Month labels
        const months = ['Jan', 'Fev', 'Mar', 'Avr', 'Mai', 'Jun', 'Jul', 'Aou', 'Sep', 'Oct', 'Nov', 'Dec'];
        let monthHtml = '<div class="flex" style="padding-left:28px;gap:3px;margin-bottom:2px">';
        let lastMonth = -1;
        for (let w = 0; w < weeks.length; w++) {
            const firstDay = weeks[w].find(d => d.inYear);
            const m = firstDay ? new Date(firstDay.date).getMonth() : -1;
            if (m !== lastMonth && m >= 0) {
                monthHtml += `<span class="journal-cal-month" style="width:${w === 0 ? 14 : 14}px">${months[m]}</span>`;
                lastMonth = m;
            } else {
                monthHtml += `<span style="width:14px"></span>`;
            }
        }
        monthHtml += '</div>';

        // Day rows
        const dayLabels = ['', 'L', '', 'M', '', 'V', ''];
        let gridHtml = '';
        for (let row = 0; row < 7; row++) {
            gridHtml += '<div class="journal-cal-row">';
            gridHtml += `<span class="journal-cal-label">${dayLabels[row]}</span>`;
            for (let w = 0; w < weeks.length; w++) {
                if (row < weeks[w].length) {
                    const cell = weeks[w][row];
                    const d = dayMap[cell.date];
                    const color = cell.inYear ? _dayColor(d) : 'transparent';
                    const title = d
                        ? `${cell.date}: ${parseFloat(d.pnl) >= 0 ? '+' : ''}$${d.pnl} (${d.trades} trade${d.trades > 1 ? 's' : ''})`
                        : cell.inYear ? `${cell.date}: pas de trade` : '';
                    gridHtml += `<div class="journal-cal-cell" style="background:${color}" data-date="${cell.date}" title="${title}"></div>`;
                }
            }
            gridHtml += '</div>';
        }

        container.innerHTML = monthHtml + gridHtml;

        // Click handler
        container.querySelectorAll('.journal-cal-cell').forEach(cell => {
            cell.addEventListener('click', () => {
                const date = cell.dataset.date;
                const d = dayMap[date];
                _showDayDetail(date, d);
            });
        });
    }

    function _dayColor(d) {
        if (!d) return 'rgba(255,255,255,0.04)';
        const pnl = parseFloat(d.pnl);
        if (pnl === 0) return 'rgba(255,255,255,0.04)';
        if (pnl > 0) {
            if (pnl >= 50) return '#166534';
            if (pnl >= 10) return '#22c55e';
            return '#86efac';
        }
        if (pnl <= -50) return '#991b1b';
        if (pnl <= -10) return '#ef4444';
        return '#fca5a5';
    }

    function _showDayDetail(date, d) {
        const el = document.getElementById('journal-day-detail');
        if (!d) {
            el.innerHTML = `<div class="journal-day-detail"><span class="text-sm text-gray-400">${date} — Pas de trade</span></div>`;
            el.classList.remove('hidden');
            return;
        }
        const pnl = parseFloat(d.pnl);
        const cls = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const winRate = d.trades > 0 ? Math.round(d.wins / d.trades * 100) : 0;
        el.innerHTML = `
            <div class="journal-day-detail">
                <div class="flex items-center justify-between mb-1">
                    <span class="font-bold text-sm">${date}</span>
                    <span class="${cls} font-bold">${pnl >= 0 ? '+' : ''}$${d.pnl}</span>
                </div>
                <div class="text-xs text-gray-400">
                    ${d.trades} trade${d.trades > 1 ? 's' : ''} · ${d.wins} win${d.wins > 1 ? 's' : ''} · Win rate ${winRate}%
                </div>
            </div>`;
        el.classList.remove('hidden');
    }

    // ── Trade entries ───────────────────────────────────────

    async function _loadEntries() {
        try {
            const sym = document.getElementById('journal-symbol-filter').value;
            const params = new URLSearchParams({ limit: PAGE_SIZE, offset: _offset });
            if (sym) params.set('symbol', sym);
            const resp = await fetch(`/api/journal/entries?${params}`);
            if (!resp.ok) return;
            const data = await resp.json();

            // Collect symbols for filter
            for (const e of data) _symbols.add(e.symbol);
            _updateSymbolFilter();

            _entries = _entries.concat(data);
            _offset += data.length;
            _renderEntries();

            const btn = document.getElementById('journal-load-more');
            if (data.length < PAGE_SIZE) {
                btn.classList.add('hidden');
            } else {
                btn.classList.remove('hidden');
            }
        } catch (e) {
            console.error('Journal entries load failed', e);
        }
    }

    function _updateSymbolFilter() {
        const sel = document.getElementById('journal-symbol-filter');
        const current = sel.value;
        const sorted = [..._symbols].sort();
        const opts = ['<option value="">Tous</option>'];
        for (const s of sorted) {
            opts.push(`<option value="${s}"${s === current ? ' selected' : ''}>${s}</option>`);
        }
        sel.innerHTML = opts.join('');
    }

    function _renderEntries() {
        const container = document.getElementById('journal-entries');
        const empty = document.getElementById('journal-empty');

        if (!_entries.length) {
            container.innerHTML = '';
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');
        container.innerHTML = _entries.map(_buildEntryCard).join('');
    }

    function _buildEntryCard(e) {
        const pnl = e.realized_pnl ? parseFloat(e.realized_pnl) : 0;
        const pnlPct = e.realized_pnl_pct ? parseFloat(e.realized_pnl_pct) : 0;
        const isWin = pnl > 0;
        const cardCls = isWin ? 'snapshot-win' : 'snapshot-loss';
        const pnlCls = isWin ? 'pnl-positive' : 'pnl-negative';
        const sideColor = e.side === 'LONG' ? 'text-green-400' : 'text-red-400';

        const exitReason = _getExitReason(e);
        const exitBadge = exitReason
            ? `<span class="exit-badge exit-badge-${exitReason.toLowerCase()}">${exitReason}</span>`
            : '';

        const openCtx = _formatContext(e.open_snapshot);
        const closeCtx = _formatContext(e.close_snapshot);

        return `
        <div class="journal-snapshot-card ${cardCls}">
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                    <span class="font-bold text-sm">${e.symbol}</span>
                    <span class="text-xs font-bold ${sideColor}">${e.side}</span>
                    <span class="text-xs text-gray-500">${e.market_type}</span>
                    ${exitBadge}
                </div>
                <span class="${pnlCls} font-bold text-sm">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</span>
            </div>

            <div class="grid grid-cols-2 gap-3 text-xs mb-2">
                <div>
                    <div class="text-gray-500 mb-1 font-medium">ENTRY</div>
                    <div class="text-gray-300">$${parseFloat(e.entry_price).toLocaleString('en-US', { maximumFractionDigits: 6 })}</div>
                    <div class="text-gray-500">${_fmtTime(e.opened_at)}</div>
                </div>
                <div>
                    <div class="text-gray-500 mb-1 font-medium">EXIT</div>
                    <div class="text-gray-300">${e.exit_price ? '$' + parseFloat(e.exit_price).toLocaleString('en-US', { maximumFractionDigits: 6 }) : '--'}</div>
                    <div class="text-gray-500">${_fmtTime(e.closed_at)}</div>
                </div>
            </div>

            <div class="flex items-center gap-3 text-xs text-gray-500 mb-2">
                <span>Duree: ${e.duration || '--'}</span>
                <span>Fees: $${e.total_fees_usd}</span>
                <span>Qty: ${parseFloat(e.quantity).toFixed(6)}</span>
            </div>

            ${(openCtx || closeCtx) ? `
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2 pt-2 border-t border-gray-800">
                ${openCtx ? `<div><div class="text-xs text-blue-400 font-medium mb-1">Contexte ouverture</div>${openCtx}</div>` : ''}
                ${closeCtx ? `<div><div class="text-xs text-purple-400 font-medium mb-1">Contexte fermeture</div>${closeCtx}</div>` : ''}
            </div>` : ''}
        </div>`;
    }

    function _getExitReason(e) {
        if (e.close_snapshot && e.close_snapshot.exit_reason) {
            return e.close_snapshot.exit_reason;
        }
        return null;
    }

    function _formatContext(snap) {
        if (!snap || !snap.data) return '';
        const d = snap.data;
        const rows = [];

        if (d.bias && d.bias.direction) {
            const biasColor = d.bias.direction === 'LONG' ? 'text-green-400'
                : d.bias.direction === 'SHORT' ? 'text-red-400' : 'text-gray-400';
            rows.push(['Biais', `<span class="${biasColor}">${d.bias.direction}</span> ${d.bias.confidence ? d.bias.confidence + '%' : ''}`]);
        }

        if (d.technical) {
            if (d.technical.rsi_1h != null) {
                const rsi = d.technical.rsi_1h;
                const rsiColor = rsi > 70 ? 'text-red-400' : rsi < 30 ? 'text-green-400' : 'text-gray-300';
                rows.push(['RSI 1h', `<span class="${rsiColor}">${rsi.toFixed(1)}</span>`]);
            }
            if (d.technical.bb_position_1h != null) {
                rows.push(['BB pos', `${(d.technical.bb_position_1h * 100).toFixed(0)}%`]);
            }
            if (d.technical.buy_sell_1h != null) {
                const bs = d.technical.buy_sell_1h;
                const bsColor = bs > 0 ? 'text-green-400' : bs < 0 ? 'text-red-400' : 'text-gray-300';
                rows.push(['B/S', `<span class="${bsColor}">${bs > 0 ? '+' : ''}${bs.toFixed(1)}</span>`]);
            }
        }

        if (d.macro) {
            if (d.macro.dxy) rows.push(['DXY', `${d.macro.dxy.value} ${_trendIcon(d.macro.dxy.trend)}`]);
            if (d.macro.vix) rows.push(['VIX', `${d.macro.vix.value} ${_trendIcon(d.macro.vix.trend)}`]);
        }

        if (d.microstructure) {
            if (d.microstructure.orderbook_imbalance != null) {
                const imb = d.microstructure.orderbook_imbalance;
                const imbColor = imb > 0.1 ? 'text-green-400' : imb < -0.1 ? 'text-red-400' : 'text-gray-300';
                rows.push(['OB imb', `<span class="${imbColor}">${(imb * 100).toFixed(0)}%</span>`]);
            }
            if (d.microstructure.whale_recent_count > 0) {
                rows.push(['Whales', `${d.microstructure.whale_recent_count} (${d.microstructure.whale_recent_bias || '?'})`]);
            }
        }

        if (!rows.length) return '';
        return `<div class="journal-context-grid">${rows.map(([k, v]) =>
            `<span class="journal-context-label">${k}</span><span class="journal-context-value">${v}</span>`
        ).join('')}</div>`;
    }

    function _trendIcon(trend) {
        if (trend === 'up') return '<span class="text-green-400">&#9650;</span>';
        if (trend === 'down') return '<span class="text-red-400">&#9660;</span>';
        return '<span class="text-gray-500">&#9654;</span>';
    }

    // ── Helpers ──────────────────────────────────────────────

    function _fmtDate(dt) {
        const y = dt.getFullYear();
        const m = String(dt.getMonth() + 1).padStart(2, '0');
        const d = String(dt.getDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
    }

    function _fmtTime(iso) {
        if (!iso) return '--';
        const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
        return d.toLocaleString('fr-FR', {
            day: '2-digit', month: '2-digit',
            hour: '2-digit', minute: '2-digit',
        });
    }

    return { init, load };
})();
