const KlineChart = (() => {
    let _symbol = 'BTCUSDC';
    let _interval = '5m';
    let _mainChart = null;
    let _candleSeries = null;
    let _maSeries = {};
    let _bbSeries = {};
    let _volChart = null;
    let _volSeries = null;
    let _rsiChart = null;
    let _rsiSeries = null;
    let _obvChart = null;
    let _obvSeries = null;
    let _macdChart = null;
    let _macdLineSeries = null;
    let _macdSignalSeries = null;
    let _macdHistSeries = null;
    let _bsChart = null;
    let _bsSeries = null;
    let _cycleSeries = [];
    let _cyclesCache = { symbol: null, data: null };
    let _cyclesRendered = { symbol: null, interval: null };
    let _activeCycleRefs = []; // {area, entryPrice}
    let _cycleInfos = []; // {openTs, closeTs, data} for tooltip
    let _cycleTooltipEl = null;
    let _activeIndicators = new Set(['ma', 'volume', 'obv', 'rsi', 'macd', 'buy_sell', 'cycles', 'orders']);
    let _loading = false;
    let _initialized = false;
    let _syncing = false;
    let _subscribedStream = null;
    let _crosshairSyncing = false;
    let _chartRegistry = [];
    let _seriesDataMap = {};
    let _currentPrice = null;
    let _priceLabelLine = null;
    let _countdownTimer = null;
    const _observers = [];
    let _orderPriceLines = [];
    let _levelPriceLines = [];
    let _alertPriceLines = [];
    let _cachedPositions = null;
    let _pendingOrders = [];
    let _orderScalePrices = [];
    let _orderScaleForce = true;
    let _cachedAnalysis = null;
    let _lastCandleTime = null;
    let _liveData = null; // cached kline arrays for live indicator updates

    function _observeResize(el, chart) {
        const ro = new ResizeObserver(entries => {
            const cr = entries[0].contentRect;
            if (cr.width > 0) chart.applyOptions({ width: cr.width });
            if (cr.height > 0) chart.applyOptions({ height: cr.height });
        });
        ro.observe(el);
        _observers.push(ro);
    }

    const C = {
        bg: 'transparent',
        text: '#6b7280',
        grid: 'rgba(255, 255, 255, 0.03)',
        border: 'rgba(255, 255, 255, 0.06)',
        // Binance palette
        upCandle: '#26b87a',
        downCandle: '#e05565',
        wickUp: '#26b87a',
        wickDown: '#e05565',
        volUp: 'rgba(38, 184, 122, 0.30)',
        volDown: 'rgba(224, 85, 101, 0.30)',
        ma7: '#fbbf24',
        ma25: '#60a5fa',
        ma99: '#a78bfa',
        bb: 'rgba(167, 139, 250, 0.4)',
        rsi: '#fbbf24',
        obv: '#22d3ee',
        macdLine: '#60a5fa',
        macdSignal: '#fbbf24',
        macdHistUp: 'rgba(0,255,135,0.45)',
        macdHistDown: 'rgba(255,60,100,0.45)',
        bsBuy: 'rgba(0,255,135,0.45)',
        bsSell: 'rgba(255,60,100,0.45)',
        buy: '#00ff87',
        sell: '#ff3c64',
    };

    function init() {
        if (_initialized) return;
        _initialized = true;

        document.querySelectorAll('.chart-interval-btn[data-interval]').forEach(btn => {
            btn.addEventListener('click', () => {
                _interval = btn.dataset.interval;
                document.querySelectorAll('.chart-interval-btn[data-interval]').forEach(b =>
                    b.classList.toggle('active', b === btn));
                loadChart();
            });
        });

        const resetBtn = document.getElementById('chart-reset-btn');
        if (resetBtn) resetBtn.addEventListener('click', _resetView);

        const sel = document.getElementById('chart-symbol');
        if (sel) sel.addEventListener('change', () => {
            _symbol = sel.value;
            _userSelected = true;
            loadChart();
        });

        document.querySelectorAll('.indicator-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.classList.toggle('active');
                const ind = btn.dataset.ind;
                if (_activeIndicators.has(ind)) _activeIndicators.delete(ind);
                else _activeIndicators.add(ind);
                _applyVisibility();
                loadChart();
            });
        });

        _createMainChart();
        _applyVisibility();
        window.addEventListener('resize', Utils.throttle(_resizeMainChart, 200));
        _loadSymbols();
    }

    async function _loadSymbols() {
        try {
            const base = ['BTCUSDC', 'ETHUSDC', 'BNBUSDC'];
            const resp = await fetch('/api/klines/symbols');
            const symbols = await resp.json();
            _updateSymbolSelect([...new Set([...base, ...symbols])]);
        } catch (e) { /* ignore */ }
    }

    let _userSelected = false; // true once user manually picked a symbol or first auto-select done

    async function selectActivePosition() {
        if (_userSelected) return; // don't override user's manual choice
        try {
            const resp = await fetch('/api/positions');
            const positions = await resp.json();
            if (positions && positions.length > 0) {
                positions.sort((a, b) => (b.opened_at || '').localeCompare(a.opened_at || ''));
                const preferred = positions[0].symbol;
                if (_symbol !== preferred) {
                    _symbol = preferred;
                    const sel = document.getElementById('chart-symbol');
                    if (sel) sel.value = _symbol;
                }
            }
            _userSelected = true; // first auto-select done, don't override again
            _cachedPositions = positions;
        } catch (e) { /* ignore */ }
    }

    function _updateSymbolSelect(symbols) {
        const sel = document.getElementById('chart-symbol');
        if (!sel) return;
        // Always keep the current symbol in the list so user selection isn't lost
        const unique = [...new Set([_symbol, ...symbols])];
        sel.innerHTML = unique.map(s =>
            `<option value="${s}" ${s === _symbol ? 'selected' : ''}>${s}</option>`
        ).join('');
    }

    function _resetView() {
        const all = [_mainChart, ...(_chartRegistry.map(r => r.chart))];
        for (const c of all) {
            if (!c) continue;
            c.timeScale().fitContent();
            c.priceScale('right').applyOptions({ autoScale: true });
        }
    }

    const SUB_HEIGHTS = { volume: 80, buy_sell: 70, rsi: 100, obv: 100, macd: 90 };
    const MIN_MAIN = 400;

    function _calcMainHeight() {
        const el = document.getElementById('kline-chart-main');
        if (!el) return 500;
        const stack = el.closest('.chart-stack');
        if (!stack) return 500;
        const stackTop = stack.getBoundingClientRect().top;
        const viewH = window.innerHeight;
        const available = viewH - stackTop - 16; // 16px bottom margin
        let subTotal = 0;
        for (const [ind, h] of Object.entries(SUB_HEIGHTS)) {
            if (_activeIndicators.has(ind)) subTotal += h + 19; // +1 border-top + 18 label
        }
        return Math.max(MIN_MAIN, available - subTotal);
    }

    function _resizeMainChart() {
        const h = _calcMainHeight();
        const el = document.getElementById('kline-chart-main');
        if (el) el.style.height = h + 'px';
        if (_mainChart) _mainChart.applyOptions({ height: h });
    }

    function _isMobile() { return window.innerWidth < 1024 || 'ontouchstart' in window; }

    function _chartOptions(height, showTimeScale) {
        return {
            width: 0,
            height: height,
            layout: { background: { color: C.bg }, textColor: C.text, fontSize: 10 },
            grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
            rightPriceScale: { borderColor: C.border, minimumWidth: 88, scaleMargins: { top: 0.08, bottom: 0.05 } },
            timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false, visible: showTimeScale, rightOffset: 5, minBarSpacing: _isMobile() ? 3 : 0.5, lockVisibleTimeRangeOnResize: true },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: 'rgba(255,255,255,0.1)', width: 1, style: LightweightCharts.LineStyle.Solid, labelBackgroundColor: '#3d3836' },
                horzLine: { color: 'rgba(255,255,255,0.1)', width: 1, style: LightweightCharts.LineStyle.Solid, labelBackgroundColor: '#3d3836' },
            },
        };
    }

    function _createMainChart() {
        const el = document.getElementById('kline-chart-main');
        if (!el || _mainChart) return;

        const mainH = _calcMainHeight();
        el.style.height = mainH + 'px';
        _mainChart = LightweightCharts.createChart(el, _chartOptions(mainH, true));
        _mainChart.applyOptions({ width: el.clientWidth });

        _candleSeries = _mainChart.addCandlestickSeries({
            upColor: C.upCandle, downColor: C.downCandle,
            borderVisible: false,
            wickUpColor: C.wickUp, wickDownColor: C.wickDown,
            lastValueVisible: false,
            priceLineVisible: false,
            autoscaleInfoProvider: (original) => {
                const res = original();
                if (!res || !res.priceRange || !_orderScaleForce || !_orderScalePrices.length) return res;
                for (const p of _orderScalePrices) {
                    res.priceRange.minValue = Math.min(res.priceRange.minValue, p);
                    res.priceRange.maxValue = Math.max(res.priceRange.maxValue, p);
                }
                return res;
            },
        });

        // Floating % label next to crosshair
        const pctLabel = document.createElement('div');
        pctLabel.style.cssText = 'position:absolute;right:0;padding:2px 6px;font-size:12px;font-weight:600;pointer-events:none;z-index:10;display:none;white-space:nowrap;background:rgba(0,0,0,0.75);border-radius:3px;';
        el.style.position = 'relative';
        el.appendChild(pctLabel);

        // Cycle tooltip element
        _cycleTooltipEl = document.createElement('div');
        _cycleTooltipEl.className = 'cycle-tooltip';
        el.appendChild(_cycleTooltipEl);

        _mainChart.subscribeCrosshairMove(param => {
            if (!param.point || !_currentPrice) {
                pctLabel.style.display = 'none';
                _cycleTooltipEl.style.display = 'none';
                return;
            }
            const price = _candleSeries.coordinateToPrice(param.point.y);
            if (price == null) { pctLabel.style.display = 'none'; return; }
            const pct = ((price - _currentPrice) / _currentPrice * 100);
            const sign = pct >= 0 ? '+' : '';
            const color = pct >= 0 ? C.upCandle : C.downCandle;
            pctLabel.textContent = `${sign}${pct.toFixed(2)}%`;
            pctLabel.style.color = color;
            pctLabel.style.top = (param.point.y + 12) + 'px';
            pctLabel.style.display = 'block';

            // Cycle tooltip
            const t = param.time;
            if (t && _cycleInfos.length) {
                const hit = _cycleInfos.find(ci => t >= ci.openTs && t <= ci.closeTs);
                if (hit) {
                    _cycleTooltipEl.innerHTML = _buildCycleTooltip(hit.data);
                    // Position near crosshair
                    const x = Math.min(param.point.x + 16, el.clientWidth - 200);
                    _cycleTooltipEl.style.left = x + 'px';
                    _cycleTooltipEl.style.top = '8px';
                    _cycleTooltipEl.style.display = 'block';
                } else {
                    _cycleTooltipEl.style.display = 'none';
                }
            } else {
                _cycleTooltipEl.style.display = 'none';
            }
        });

        _mainChart.timeScale().subscribeVisibleLogicalRangeChange(() => {
            _orderScaleForce = false;
        });

        _maSeries = {};
        [[7, C.ma7], [25, C.ma25], [99, C.ma99]].forEach(([p, c]) => {
            _maSeries['ma_' + p] = _mainChart.addLineSeries({
                color: c, lineWidth: 1,
                lastValueVisible: false, priceLineVisible: false,
            });
        });

        _bbSeries.upper = _mainChart.addLineSeries({
            color: C.bb, lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });
        _bbSeries.lower = _mainChart.addLineSeries({
            color: C.bb, lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });

        _registerChart(_mainChart, _candleSeries, 'candle');
        _observeResize(el, _mainChart);
    }

    function _ensureSubChart(chartRef, seriesRef, elId, height, seriesFactory) {
        if (chartRef.chart) return;
        const el = document.getElementById(elId);
        if (!el) return;

        chartRef.chart = LightweightCharts.createChart(el, _chartOptions(height, false));
        chartRef.chart.applyOptions({ width: el.clientWidth });

        seriesRef.series = seriesFactory(chartRef.chart);

        _syncTimeScales(_mainChart, chartRef.chart);
        _observeResize(el, chartRef.chart);
    }

    function _ensureVolChart() {
        if (_volChart) return;
        const el = document.getElementById('kline-chart-volume');
        if (!el) return;

        _volChart = LightweightCharts.createChart(el, _chartOptions(SUB_HEIGHTS.volume, false));
        _volChart.applyOptions({ width: el.clientWidth });

        _volSeries = _volChart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            base: 0,
        });
        _volChart.priceScale('right').applyOptions({ scaleMargins: { top: 0.15, bottom: 0 }, entireTextOnly: true });

        _syncTimeScales(_mainChart, _volChart);
        _registerChart(_volChart, _volSeries, 'volume');
        _observeResize(el, _volChart);
    }

    function _ensureRsiChart() {
        if (_rsiChart) return;
        const el = document.getElementById('kline-chart-rsi');
        if (!el) return;

        _rsiChart = LightweightCharts.createChart(el, _chartOptions(SUB_HEIGHTS.rsi, false));
        _rsiChart.applyOptions({ width: el.clientWidth });

        _rsiSeries = _rsiChart.addLineSeries({
            color: C.rsi, lineWidth: 1.5,
            lastValueVisible: true, priceLineVisible: false,
            autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        });
        _rsiSeries.createPriceLine({ price: 70, color: 'rgba(239,83,80,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true });
        _rsiSeries.createPriceLine({ price: 30, color: 'rgba(38,166,154,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true });

        _syncTimeScales(_mainChart, _rsiChart);
        _registerChart(_rsiChart, _rsiSeries, 'rsi');
        _observeResize(el, _rsiChart);
    }

    function _ensureObvChart() {
        if (_obvChart) return;
        const el = document.getElementById('kline-chart-obv');
        if (!el) return;

        _obvChart = LightweightCharts.createChart(el, _chartOptions(SUB_HEIGHTS.obv, false));
        _obvChart.applyOptions({ width: el.clientWidth });

        _obvSeries = _obvChart.addLineSeries({
            color: C.obv, lineWidth: 1.5,
            lastValueVisible: true, priceLineVisible: false,
        });
        _obvChart.priceScale('right').applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });

        _syncTimeScales(_mainChart, _obvChart);
        _registerChart(_obvChart, _obvSeries, 'obv');
        _observeResize(el, _obvChart);
    }

    function _ensureMacdChart() {
        if (_macdChart) return;
        const el = document.getElementById('kline-chart-macd');
        if (!el) return;

        _macdChart = LightweightCharts.createChart(el, _chartOptions(SUB_HEIGHTS.macd, false));
        _macdChart.applyOptions({ width: el.clientWidth });

        _macdHistSeries = _macdChart.addHistogramSeries({
            lastValueVisible: false, priceLineVisible: false,
        });
        _macdLineSeries = _macdChart.addLineSeries({
            color: C.macdLine, lineWidth: 1.5,
            lastValueVisible: false, priceLineVisible: false,
        });
        _macdSignalSeries = _macdChart.addLineSeries({
            color: C.macdSignal, lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });

        _macdChart.priceScale('right').applyOptions({ autoScale: true });
        _syncTimeScales(_mainChart, _macdChart);
        _registerChart(_macdChart, _macdLineSeries, 'macd');
        _observeResize(el, _macdChart);
    }

    function _ensureBsChart() {
        if (_bsChart) return;
        const el = document.getElementById('kline-chart-buysell');
        if (!el) return;

        _bsChart = LightweightCharts.createChart(el, _chartOptions(SUB_HEIGHTS.buy_sell, false));
        _bsChart.applyOptions({ width: el.clientWidth });

        _bsSeries = _bsChart.addHistogramSeries({
            lastValueVisible: false, priceLineVisible: false,
        });
        _bsSeries.createPriceLine({ price: 0, color: 'rgba(255,255,255,0.1)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: false });

        _syncTimeScales(_mainChart, _bsChart);
        _registerChart(_bsChart, _bsSeries, 'bs');
        _observeResize(el, _bsChart);
    }

    function _syncTimeScales(master, slave) {
        master.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (_syncing || !range) return;
            _syncing = true;
            slave.timeScale().setVisibleLogicalRange(range);
            _syncing = false;
        });
        slave.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (_syncing || !range) return;
            _syncing = true;
            master.timeScale().setVisibleLogicalRange(range);
            _syncing = false;
        });
    }

    function _alignPriceScales() {
        const charts = [_mainChart, _volChart, _rsiChart, _obvChart, _macdChart, _bsChart].filter(Boolean);
        if (charts.length <= 1) return;
        let maxW = 88;
        for (const c of charts) {
            try { maxW = Math.max(maxW, c.priceScale('right').width()); } catch (_) {}
        }
        for (const c of charts) {
            c.priceScale('right').applyOptions({ minimumWidth: maxW });
        }
    }

    function _registerChart(chart, series, dataMapKey) {
        if (_chartRegistry.some(e => e.chart === chart)) return;
        _chartRegistry.push({ chart, series, dataMapKey });
        chart.subscribeCrosshairMove(param => {
            if (_crosshairSyncing) return;
            _crosshairSyncing = true;
            for (const entry of _chartRegistry) {
                if (entry.chart === chart) continue;
                if (param.time) {
                    const price = _seriesDataMap[entry.dataMapKey]?.get(param.time);
                    if (price != null) {
                        entry.chart.setCrosshairPosition(price, param.time, entry.series);
                    }
                } else {
                    entry.chart.clearCrosshairPosition();
                }
            }
            _crosshairSyncing = false;
        });
    }

    function _applyVisibility() {
        const ids = {
            'kline-wrap-volume': 'volume', 'kline-wrap-rsi': 'rsi',
            'kline-wrap-obv': 'obv', 'kline-wrap-macd': 'macd',
            'kline-wrap-buysell': 'buy_sell',
        };
        for (const [id, ind] of Object.entries(ids)) {
            const el = document.getElementById(id);
            if (el) el.classList.toggle('hidden', !_activeIndicators.has(ind));
        }
        _resizeMainChart();
        requestAnimationFrame(_alignPriceScales);
    }

    let _headerChange24h = null;
    function _updatePriceHeader(price, change24h) {
        if (change24h != null) _headerChange24h = change24h;
        const priceEl = document.getElementById('chart-header-price');
        const changeEl = document.getElementById('chart-header-change');
        if (priceEl && price != null) priceEl.textContent = Utils.fmtPrice(price);
        if (changeEl && _headerChange24h != null) {
            const v = parseFloat(_headerChange24h);
            changeEl.textContent = `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
            changeEl.className = 'chart-header-change ' + (v >= 0 ? 'pnl-positive' : 'pnl-negative');
        }
    }

    function _toTs(isoStr) {
        return Math.floor(new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z').getTime() / 1000);
    }

    function _buildCycleTooltip(c) {
        const sym = c.symbol ? c.symbol.replace('USDC', '') : '';
        const side = c.side || '';
        const sideClass = side === 'LONG' ? 'pnl-positive' : 'pnl-negative';
        const entry = c.entry_price ? Utils.fmtPrice(parseFloat(c.entry_price)) : '--';

        if (c.is_active) {
            const pnl = c.pnl_pct ? parseFloat(c.pnl_pct) : 0;
            const pnlUsd = c.pnl_usd ? parseFloat(c.pnl_usd) : 0;
            const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            const pnlSign = pnl >= 0 ? '+' : '';
            const dur = c.duration || '';
            return `<div class="ct-header"><span class="${sideClass}">${side}</span> ${sym} <span class="ct-status ct-active">En cours</span></div>`
                + `<div class="ct-row">Entry <b>${entry}</b></div>`
                + `<div class="ct-row ${pnlClass}">${pnlSign}${pnl.toFixed(2)}% (${pnlSign}$${Math.abs(pnlUsd).toFixed(0)})</div>`
                + (dur ? `<div class="ct-row ct-dim">${dur}</div>` : '');
        }

        const pnl = c.realized_pnl_pct ? parseFloat(c.realized_pnl_pct) : 0;
        const grossUsd = c.realized_pnl ? parseFloat(c.realized_pnl) : 0;
        const feesUsd = c.total_fees_usd ? parseFloat(c.total_fees_usd) : 0;
        const pnlUsd = grossUsd - feesUsd;
        const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const pnlSign = pnl >= 0 ? '+' : '';
        const exit = c.exit_price ? Utils.fmtPrice(parseFloat(c.exit_price)) : '--';
        const fees = c.total_fees_usd ? parseFloat(c.total_fees_usd) : 0;
        const dur = c.duration || '';
        const statusLabel = pnl >= 0 ? 'Win' : 'Loss';
        const statusClass = pnl >= 0 ? 'ct-win' : 'ct-loss';

        return `<div class="ct-header"><span class="${sideClass}">${side}</span> ${sym} <span class="ct-status ${statusClass}">${statusLabel}</span></div>`
            + `<div class="ct-row">Entry <b>${entry}</b> &rarr; <b>${exit}</b></div>`
            + `<div class="ct-row ${pnlClass}">${pnlSign}${pnl.toFixed(2)}% (${pnlUsd >= 0 ? '+' : '-'}$${Math.abs(pnlUsd).toFixed(2)})</div>`
            + (fees ? `<div class="ct-row ct-dim">Fees $${fees.toFixed(2)}</div>` : '')
            + (dur ? `<div class="ct-row ct-dim">${dur}</div>` : '');
    }

    async function _subscribeWS() {
        const key = _symbol + ':' + _interval;
        if (_subscribedStream === key) return;
        if (_subscribedStream) {
            const [oldSym, oldInt] = _subscribedStream.split(':');
            fetch(`/api/klines/${oldSym}/unsubscribe?interval=${oldInt}`, { method: 'POST' }).catch(() => {});
        }
        _subscribedStream = key;
        fetch(`/api/klines/${_symbol}/subscribe?interval=${_interval}`, { method: 'POST' }).catch(() => {});
    }

    async function loadChart() {
        if (_loading) return;
        _loading = true;
        _orderScaleForce = true;
        // Invalidate active cycles cache so position overlays refresh
        if (_cyclesCache.data?.some(c => c.is_active)) {
            _cyclesCache = { symbol: null, data: null };
            _cyclesRendered = { symbol: null, interval: null };
        }
        _subscribeWS();

        try {
            const indList = [..._activeIndicators].filter(i => i !== 'cycles').join(',');
            const resp = await fetch(`/api/klines/${_symbol}?interval=${_interval}&indicators=${indList}&limit=750`);
            if (!resp.ok) throw new Error(await resp.text());
            const data = await resp.json();
            const klines = data.klines;
            if (!klines || !klines.length) { _loading = false; return; }

            const candles = klines.map(k => ({
                time: _toTs(k.open_time),
                open: parseFloat(k.open),
                high: parseFloat(k.high),
                low: parseFloat(k.low),
                close: parseFloat(k.close),
            }));
            _candleSeries.setData(candles);
            _currentPrice = candles[candles.length - 1].close;
            _lastCandleTime = candles[candles.length - 1].time;
            _updatePriceHeader(_currentPrice, null);
            _updatePriceLabelLine(_currentPrice);

            // Cache kline data for live indicator computation
            _liveData = {
                closes: candles.map(c => c.close),
                highs: candles.map(c => c.high),
                lows: candles.map(c => c.low),
                volumes: klines.map(k => parseFloat(k.volume)),
                takerBuy: klines.map(k => parseFloat(k.taker_buy_vol || '0')),
            };

            // Build crosshair sync data maps
            _seriesDataMap.candle = new Map(candles.map(c => [c.time, c.close]));
            _seriesDataMap.volume = new Map(klines.map(k => [_toTs(k.open_time), parseFloat(k.volume)]));
            const ind = data.indicators || {};
            if (ind.rsi) {
                _seriesDataMap.rsi = new Map();
                ind.rsi.forEach((v, i) => { if (v != null) _seriesDataMap.rsi.set(candles[i].time, v); });
            }
            if (ind.obv) {
                _seriesDataMap.obv = new Map();
                ind.obv.forEach((v, i) => { if (v != null) _seriesDataMap.obv.set(candles[i].time, v); });
            }
            if (ind.macd_line) {
                _seriesDataMap.macd = new Map();
                ind.macd_line.forEach((v, i) => { if (v != null) _seriesDataMap.macd.set(candles[i].time, v); });
            }
            if (ind.buy_sell) {
                _seriesDataMap.bs = new Map();
                ind.buy_sell.forEach((v, i) => { if (v != null) _seriesDataMap.bs.set(candles[i].time, v); });
            }

            // Volume (sub-chart separe)
            if (_activeIndicators.has('volume')) {
                _ensureVolChart();
                _volSeries.setData(klines.map(k => ({
                    time: _toTs(k.open_time),
                    value: parseFloat(k.volume),
                    color: parseFloat(k.close) >= parseFloat(k.open) ? C.volUp : C.volDown,
                })));
            } else if (_volSeries) {
                _volSeries.setData([]);
            }


            // MA
            ['ma_7', 'ma_25', 'ma_99'].forEach(key => {
                const series = _maSeries[key];
                if (_activeIndicators.has('ma') && ind[key]) {
                    series.setData(ind[key]
                        .map((v, i) => v != null ? { time: candles[i].time, value: v } : null)
                        .filter(Boolean));
                } else {
                    series.setData([]);
                }
            });

            // BB
            if (_activeIndicators.has('bb') && ind.bb_upper) {
                _bbSeries.upper.setData(ind.bb_upper
                    .map((v, i) => v != null ? { time: candles[i].time, value: v } : null)
                    .filter(Boolean));
                _bbSeries.lower.setData(ind.bb_lower
                    .map((v, i) => v != null ? { time: candles[i].time, value: v } : null)
                    .filter(Boolean));
            } else {
                _bbSeries.upper.setData([]);
                _bbSeries.lower.setData([]);
            }

            // RSI
            if (_activeIndicators.has('rsi') && ind.rsi) {
                _ensureRsiChart();
                _rsiSeries.setData(ind.rsi
                    .map((v, i) => v != null ? { time: candles[i].time, value: v } : { time: candles[i].time }));
            } else if (_rsiSeries) {
                _rsiSeries.setData([]);
            }

            // OBV
            if (_activeIndicators.has('obv') && ind.obv) {
                _ensureObvChart();
                _obvSeries.setData(ind.obv
                    .map((v, i) => v != null ? { time: candles[i].time, value: v } : { time: candles[i].time }));
            } else if (_obvSeries) {
                _obvSeries.setData([]);
            }

            // MACD
            if (_activeIndicators.has('macd') && ind.macd_line) {
                _ensureMacdChart();
                _macdLineSeries.setData(ind.macd_line
                    .map((v, i) => v != null ? { time: candles[i].time, value: v } : { time: candles[i].time }));
                _macdSignalSeries.setData(ind.macd_signal
                    .map((v, i) => v != null ? { time: candles[i].time, value: v } : { time: candles[i].time }));
                _macdHistSeries.setData(ind.macd_hist
                    .map((v, i) => v != null ? {
                        time: candles[i].time, value: v,
                        color: v >= 0 ? C.macdHistUp : C.macdHistDown,
                    } : { time: candles[i].time }));
            } else {
                if (_macdLineSeries) _macdLineSeries.setData([]);
                if (_macdSignalSeries) _macdSignalSeries.setData([]);
                if (_macdHistSeries) _macdHistSeries.setData([]);
            }

            // Buy/Sell Pressure
            if (_activeIndicators.has('buy_sell') && ind.buy_sell) {
                _ensureBsChart();
                _bsSeries.setData(ind.buy_sell
                    .map((v, i) => v != null ? {
                        time: candles[i].time, value: v,
                        color: v >= 0 ? C.bsBuy : C.bsSell,
                    } : { time: candles[i].time }));
            } else if (_bsSeries) {
                _bsSeries.setData([]);
            }

            // Cycles overlay
            if (_activeIndicators.has('cycles')) {
                await _loadCycles(candles);
            } else {
                _clearCycles();
            }

            // Order + Level + Alert overlay lines
            await _fetchPendingOrders();
            _renderOrderLines();
            _renderLevelLines();
            _renderAlertLines();

            // Show last ~360 candles with right padding
            const total = candles.length;
            const visible = Math.min(total, 360);
            const rightPad = Math.round(visible / 10); // Increased padding (from /13 to /10)

            // Use rightOffset for padding (consistent per-chart property,
            // not included in synced logical range → no drift between charts)
            requestAnimationFrame(() => {
                _alignPriceScales();
                const allCharts = [_mainChart, _volChart, _rsiChart, _obvChart, _macdChart, _bsChart].filter(Boolean);
                const range = {
                    from: total - visible,
                    to: total - 1 + rightPad, // Include padding in the visible range
                };
                for (const c of allCharts) {
                    c.timeScale().applyOptions({ rightOffset: rightPad });
                    c.timeScale().setVisibleLogicalRange(range);
                }
            });

            _startCountdown();
        } catch (e) {
            console.error('KlineChart: load failed', e);
        } finally {
            _loading = false;
        }
    }

    const _intervalMs = {
        '1m': 60000, '3m': 180000, '5m': 300000, '15m': 900000,
        '30m': 1800000, '1h': 3600000, '2h': 7200000, '4h': 14400000,
        '6h': 21600000, '8h': 28800000, '12h': 43200000, '1d': 86400000,
    };

    function _startCountdown() {
        if (_countdownTimer) clearInterval(_countdownTimer);

        function tick() {
            const btn = document.querySelector(`.chart-interval-btn[data-interval="${_interval}"]`);
            if (!btn) return;
            const ms = _intervalMs[_interval];
            if (!ms) { btn.textContent = _interval; return; }
            const now = Date.now();
            const remaining = ms - (now % ms);
            const totalSecs = Math.floor(remaining / 1000);
            const h = Math.floor(totalSecs / 3600);
            const m = Math.floor((totalSecs % 3600) / 60);
            const s = totalSecs % 60;
            let timeStr;
            if (h > 0) timeStr = h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
            else if (m > 0) timeStr = m + ':' + String(s).padStart(2, '0');
            else timeStr = s + 's';
            const urgent = totalSecs <= 10;
            btn.innerHTML = `${_interval} <span class="candle-countdown-time${urgent ? ' candle-countdown-urgent' : ''}">${timeStr}</span>`;
        }

        // Reset all buttons to plain text first
        document.querySelectorAll('.chart-interval-btn[data-interval]').forEach(b => {
            b.textContent = b.dataset.interval;
        });

        tick();
        _countdownTimer = setInterval(tick, 1000);
    }

    let _entryPriceLines = [];

    function _clearCycles() {
        _cycleSeries.forEach(s => _mainChart.removeSeries(s));
        _cycleSeries = [];
        _entryPriceLines.forEach(l => _candleSeries.removePriceLine(l));
        _entryPriceLines = [];
        _activeCycleRefs = [];
        _cycleInfos = [];
        if (_candleSeries) _candleSeries.setMarkers([]);
        _cyclesRendered = { symbol: null, interval: null };
    }

    async function _loadCycles(candles) {
        // Same symbol + interval: series already correct, skip entirely
        if (_cyclesRendered.symbol === _symbol && _cyclesRendered.interval === _interval) {
            return;
        }

        try {
            let cycles;
            const hasActiveCached = _cyclesCache.symbol === _symbol
                && _cyclesCache.data?.some(c => c.is_active);
            if (_cyclesCache.symbol === _symbol && !hasActiveCached) {
                cycles = _cyclesCache.data;
            } else {
                const resp = await fetch(`/api/cycles?symbol=${_symbol}&limit=50`);
                cycles = await resp.json();
                _cyclesCache = { symbol: _symbol, data: cycles };
            }

            _cycleSeries.forEach(s => _mainChart.removeSeries(s));
            _cycleSeries = [];
            _entryPriceLines.forEach(l => _candleSeries.removePriceLine(l));
            _entryPriceLines = [];
            _activeCycleRefs = [];
            _cycleInfos = [];
            _candleSeries.setMarkers([]);
            if (!cycles.length || !candles.length) {
                _cyclesRendered = { symbol: _symbol, interval: _interval };
                return;
            }

            const intSec = (_intervalMs[_interval] || 900000) / 1000;

            cycles.forEach(c => {
                if (!c.opened_at) return;
                const openTs = _toTs(c.opened_at);
                const closeTs = c.is_active ? 0 : (c.closed_at ? _toTs(c.closed_at) : 0);
                
                // Filter candles covered by the cycle. 
                // A candle at 10:00 (4h) covers until 14:00. 
                // We include the candle if the trade was open at any point during it.
                const cycleCandles = candles.filter(cd => {
                    const cdEnd = cd.time + intSec;
                    if (c.is_active) return cdEnd > openTs;
                    return cdEnd > openTs && cd.time <= closeTs;
                });

                if (!cycleCandles.length) return;

                let color;
                if (c.is_active) {
                    color = 'rgba(230,170,60,';   // vivid gold
                } else if (c.realized_pnl_pct && parseFloat(c.realized_pnl_pct) > 0) {
                    color = 'rgba(90,180,105,';   // rich olive
                } else {
                    color = 'rgba(200,90,80,';    // deep terracotta
                }

                const pad = 0.013;
                const rawVals = cycleCandles.map(cd => cd.high * (1 + pad));
                let avg = 0;
                for (const v of rawVals) avg += v;
                avg /= rawVals.length;
                const compress = 0.3; 
                const values = rawVals.map(v => avg + (v - avg) * compress);
                
                const areaData = cycleCandles.map((cd, i) => ({
                    time: cd.time,
                    value: values[i],
                }));

                const opTop = c.is_active ? '0.06)' : '0.04)';
                const opBot = c.is_active ? '0.22)' : '0.15)';
                const opLine = c.is_active ? '0.45)' : '0.30)';
                const area = _mainChart.addAreaSeries({
                    topColor: color + opTop,
                    bottomColor: color + opBot,
                    lineColor: color + opLine,
                    lineWidth: 1,
                    lastValueVisible: false,
                    priceLineVisible: false,
                    crosshairMarkerVisible: false,
                });
                area.setData(areaData);
                _cycleSeries.push(area);
                _cycleInfos.push({ 
                    openTs: cycleCandles[0].time, 
                    closeTs: cycleCandles[cycleCandles.length-1].time + intSec, 
                    data: c 
                });

                // Entry price line (active cycles only)
                if (c.entry_price && c.is_active) {
                    const ep = parseFloat(c.entry_price);
                    _entryPriceLines.push(_candleSeries.createPriceLine({
                        price: ep,
                        color: '#3b82f6',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: true,
                        title: 'E',
                    }));
                    _activeCycleRefs.push({ area, entryPrice: ep, pad });
                }
            });

            _cyclesRendered = { symbol: _symbol, interval: _interval };
        } catch (e) {
            console.error('KlineChart: cycles failed', e);
        }
    }

    // ── Current price label line ("P") ─────────────────────
    let _prevClose = null;
    function _updatePriceLabelLine(price) {
        if (!_candleSeries || !price) return;
        const up = _prevClose == null || price >= _prevClose;
        const color = up ? C.upCandle : C.downCandle;
        if (_priceLabelLine) {
            _priceLabelLine.applyOptions({ price, color });
        } else {
            _priceLabelLine = _candleSeries.createPriceLine({
                price,
                color,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.SparseDotted,
                axisLabelVisible: true,
                title: 'P',
            });
        }
        _prevClose = price;
    }

    // ── Order lines (SL/TP) ────────────────────────────────
    function _clearOrderLines() {
        _orderPriceLines.forEach(l => _candleSeries.removePriceLine(l));
        _orderPriceLines = [];
        _orderScalePrices = [];
    }

    function _renderOrderLines() {
        _clearOrderLines();
        _orderScalePrices = [];
        if (!_candleSeries || !_activeIndicators.has('orders')) return;

        if (_cachedPositions) {
            const pos = _cachedPositions.find(p => p.symbol === _symbol);
            if (pos) {
                if (pos.sl_price) {
                    const p = parseFloat(pos.sl_price);
                    _orderScalePrices.push(p);
                    _orderPriceLines.push(_candleSeries.createPriceLine({
                        price: p,
                        color: C.sell,
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: 'SL',
                    }));
                }
                if (pos.tp_price) {
                    const p = parseFloat(pos.tp_price);
                    _orderScalePrices.push(p);
                    _orderPriceLines.push(_candleSeries.createPriceLine({
                        price: p,
                        color: C.buy,
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: 'TP',
                    }));
                }
            }
        }

        // Pending open orders (limit orders not yet filled)
        for (const o of _pendingOrders) {
            if (o.symbol !== _symbol) continue;
            const price = parseFloat(o.price) || parseFloat(o.stopPrice);
            if (!price) continue;
            const isBuy = o.side === 'BUY';
            const label = isBuy ? 'BUY' : 'SELL';
            _orderPriceLines.push(_candleSeries.createPriceLine({
                price,
                color: isBuy ? '#3b82f6' : '#f59e0b',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.SparseDotted,
                axisLabelVisible: true,
                title: `${label} ${o.type}`,
            }));
        }
    }

    async function _fetchPendingOrders() {
        try {
            const resp = await fetch(`/api/orders/open?symbol=${_symbol}`);
            if (resp.ok) {
                const orders = await resp.json();
                // Filter out orders already shown as position SL/TP lines
                const posPrices = {};
                if (_cachedPositions) {
                    for (const p of _cachedPositions) {
                        if (p.sl_price || p.tp_price) {
                            posPrices[p.symbol] = {
                                sl: p.sl_price ? parseFloat(p.sl_price) : null,
                                tp: p.tp_price ? parseFloat(p.tp_price) : null,
                            };
                        }
                    }
                }
                _pendingOrders = orders.filter(o => {
                    const pp = posPrices[o.symbol];
                    if (!pp) return true;
                    // Skip OCO-member orders (SL/TP already drawn from position)
                    if (o.orderListId && o.orderListId !== -1) return false;
                    // Skip individual orders whose price matches position SL or TP
                    const price = parseFloat(o.price) || 0;
                    const stop = parseFloat(o.stopPrice) || 0;
                    if (pp.sl && (price === pp.sl || stop === pp.sl)) return false;
                    if (pp.tp && (price === pp.tp || stop === pp.tp)) return false;
                    return true;
                });
            }
        } catch { /* ignore */ }
    }

    // ── Level lines (support/resistance) ─────────────────
    function _clearLevelLines() {
        _levelPriceLines.forEach(l => _candleSeries.removePriceLine(l));
        _levelPriceLines = [];
    }

    function _renderLevelLines() {
        _clearLevelLines();
        if (!_candleSeries || !_cachedAnalysis || !_activeIndicators.has('levels')) return;
        const analyses = _cachedAnalysis.analyses || [];
        const analysis = analyses.find(a => a.symbol === _symbol);
        if (!analysis || !analysis.key_levels) return;

        const cp = _currentPrice || parseFloat(analysis.current_price) || 0;

        for (const lvl of analysis.key_levels) {
            const price = parseFloat(lvl.price);
            if (!price) continue;
            const above = price >= cp;
            const t = lvl.type;
            const color = (t === 'PP' || t === 'W_PP')
                ? 'rgba(59,130,246,0.5)'
                : (t === 'VWAP')
                ? 'rgba(156,39,176,0.5)'
                : (t === 'PDC')
                ? 'rgba(255,167,38,0.5)'
                : above ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)';
            _levelPriceLines.push(_candleSeries.createPriceLine({
                price,
                color,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: lvl.label || lvl.type,
            }));
        }
    }

    // ── Client-side indicator helpers (last value only) ──────────

    function _lastSMA(arr, period) {
        if (arr.length < period) return null;
        let s = 0;
        for (let i = arr.length - period; i < arr.length; i++) s += arr[i];
        return s / period;
    }

    function _lastBB(closes) {
        const p = 20;
        if (closes.length < p) return null;
        let sum = 0;
        for (let i = closes.length - p; i < closes.length; i++) sum += closes[i];
        const mean = sum / p;
        let variance = 0;
        for (let i = closes.length - p; i < closes.length; i++) variance += (closes[i] - mean) ** 2;
        const std = Math.sqrt(variance / p);
        return { upper: mean + 2 * std, lower: mean - 2 * std };
    }

    function _lastRSI(closes, period) {
        if (closes.length < period + 1) return null;
        let avgGain = 0, avgLoss = 0;
        for (let i = 1; i <= period; i++) {
            const d = closes[i] - closes[i - 1];
            if (d > 0) avgGain += d; else avgLoss -= d;
        }
        avgGain /= period;
        avgLoss /= period;
        for (let i = period + 1; i < closes.length; i++) {
            const d = closes[i] - closes[i - 1];
            avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period;
            avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period;
        }
        return avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
    }

    function _lastOBV(closes, volumes) {
        if (closes.length < 2) return null;
        let obv = 0;
        for (let i = 1; i < closes.length; i++) {
            if (closes[i] > closes[i - 1]) obv += volumes[i];
            else if (closes[i] < closes[i - 1]) obv -= volumes[i];
        }
        return obv;
    }

    function _emaArray(arr, period) {
        if (arr.length < period) return [];
        let val = 0;
        for (let i = 0; i < period; i++) val += arr[i];
        val /= period;
        const result = [val];
        const k = 2 / (period + 1);
        for (let i = period; i < arr.length; i++) {
            val = (arr[i] - val) * k + val;
            result.push(val);
        }
        return result;
    }

    function _lastMACD(closes) {
        if (closes.length < 26) return null;
        const ema12 = _emaArray(closes, 12);
        const ema26 = _emaArray(closes, 26);
        const macdLine = [];
        for (let i = 0; i < ema26.length; i++) {
            macdLine.push(ema12[i + 14] - ema26[i]);
        }
        if (macdLine.length < 9) return null;
        const signalArr = _emaArray(macdLine, 9);
        const lastMacd = macdLine[macdLine.length - 1];
        const lastSignal = signalArr[signalArr.length - 1];
        return { line: lastMacd, signal: lastSignal, hist: lastMacd - lastSignal };
    }

    function _lastBS(volumes, takerBuy) {
        const i = volumes.length - 1;
        if (i < 0 || !volumes[i]) return null;
        const raw = (takerBuy[i] / volumes[i]) * 100 - 50;
        const p = 20;
        if (i >= p) {
            let avg = 0;
            for (let j = i - p; j < i; j++) avg += volumes[j];
            avg /= p;
            const w = avg > 0 ? Math.min(volumes[i] / avg, 1.5) : 1;
            return raw * w;
        }
        return raw;
    }

    function _updateLiveIndicators(t) {
        const d = _liveData;
        if (!d) return;
        if (_activeIndicators.has('ma')) {
            for (const [p, key] of [[7, 'ma_7'], [25, 'ma_25'], [99, 'ma_99']]) {
                const v = _lastSMA(d.closes, p);
                if (v != null && _maSeries[key]) _maSeries[key].update({ time: t, value: v });
            }
        }
        if (_activeIndicators.has('bb')) {
            const bb = _lastBB(d.closes);
            if (bb) {
                _bbSeries.upper.update({ time: t, value: bb.upper });
                _bbSeries.lower.update({ time: t, value: bb.lower });
            }
        }
        if (_activeIndicators.has('rsi') && _rsiSeries) {
            const v = _lastRSI(d.closes, 14);
            if (v != null) _rsiSeries.update({ time: t, value: v });
        }
        if (_activeIndicators.has('obv') && _obvSeries) {
            const v = _lastOBV(d.closes, d.volumes);
            if (v != null) _obvSeries.update({ time: t, value: v });
        }
        if (_activeIndicators.has('macd') && _macdLineSeries) {
            const m = _lastMACD(d.closes);
            if (m) {
                _macdLineSeries.update({ time: t, value: m.line });
                _macdSignalSeries.update({ time: t, value: m.signal });
                _macdHistSeries.update({ time: t, value: m.hist, color: m.hist >= 0 ? C.macdHistUp : C.macdHistDown });
            }
        }
        if (_activeIndicators.has('buy_sell') && _bsSeries) {
            const v = _lastBS(d.volumes, d.takerBuy);
            if (v != null) _bsSeries.update({ time: t, value: v, color: v >= 0 ? C.bsBuy : C.bsSell });
        }
    }

    // Live candle update from WS
    function _onKlineUpdate(data) {
        if (!_candleSeries || data.symbol !== _symbol || data.interval !== _interval) return;
        const t = Math.floor(data.open_time / 1000);

        // New candle opened → schedule full refresh for indicators
        const isNewCandle = _lastCandleTime && t > _lastCandleTime;
        if (isNewCandle) {
            _lastCandleTime = t;
            _cyclesRendered = { symbol: null, interval: null };
            loadChart();
        }

        _currentPrice = parseFloat(data.close);
        _updatePriceHeader(_currentPrice, null);
        _updatePriceLabelLine(_currentPrice);
        _candleSeries.update({
            time: t,
            open: parseFloat(data.open),
            high: parseFloat(data.high),
            low: parseFloat(data.low),
            close: _currentPrice,
        });
        if (_volSeries && _activeIndicators.has('volume')) {
            _volSeries.update({
                time: t,
                value: parseFloat(data.volume),
                color: parseFloat(data.close) >= parseFloat(data.open) ? C.volUp : C.volDown,
            });
        }

        // Extend active cycle overlays to the current candle
        const high = parseFloat(data.high);
        for (const ref of _activeCycleRefs) {
            ref.area.update({ time: t, value: high * (1 + (ref.pad || 0)) });
        }

        // Live indicator update
        if (_liveData) {
            const low = parseFloat(data.low);
            const vol = parseFloat(data.volume);
            const tbv = data.taker_buy_vol != null ? parseFloat(data.taker_buy_vol) : 0;
            if (isNewCandle) {
                _liveData.closes.push(_currentPrice);
                _liveData.highs.push(high);
                _liveData.lows.push(low);
                _liveData.volumes.push(vol);
                _liveData.takerBuy.push(tbv);
            } else {
                const last = _liveData.closes.length - 1;
                _liveData.closes[last] = _currentPrice;
                _liveData.highs[last] = high;
                _liveData.lows[last] = low;
                _liveData.volumes[last] = vol;
                _liveData.takerBuy[last] = tbv;
            }
            _updateLiveIndicators(t);
        }
    }

    // Update symbol dropdown + order lines when positions change
    function _onPositionsSnapshot(data) {
        if (!data) return;
        const base = ['BTCUSDC', 'ETHUSDC', 'BNBUSDC'];
        const posSymbols = data.map(p => p.symbol);
        // Preserve existing symbols in the dropdown so closing a position doesn't remove options
        const sel = document.getElementById('chart-symbol');
        const existing = sel ? [...sel.options].map(o => o.value) : [];
        _updateSymbolSelect([...new Set([...base, ...existing, ...posSymbols])]);
        // Detect any change in active positions for current symbol → invalidate cycles cache
        const prev = _cachedPositions;
        const prevIds = prev ? prev.filter(p => p.symbol === _symbol && p.is_active).map(p => p.id).join(',') : '';
        const curIds = data.filter(p => p.symbol === _symbol && p.is_active).map(p => p.id).join(',');
        if (prevIds !== curIds) {
            _cyclesCache = { symbol: null, data: null };
            _cyclesRendered = { symbol: null, interval: null };
            if (_activeIndicators.has('cycles')) loadChart();
        }
        _cachedPositions = data;
        if (_activeIndicators.has('orders')) {
            _fetchPendingOrders().then(() => _renderOrderLines());
        }
    }

    function _onAnalysisUpdate(data) {
        if (!data) return;
        _cachedAnalysis = data;
        if (_activeIndicators.has('levels')) _renderLevelLines();
    }

    // ── Custom alert price lines ───────────────────────────
    function _renderAlertLines() {
        _alertPriceLines.forEach(l => _candleSeries.removePriceLine(l));
        _alertPriceLines = [];
        if (!_candleSeries || typeof Alerts === 'undefined') return;
        const alerts = Alerts.getAlerts();
        for (const a of alerts) {
            if (a.symbol !== _symbol) continue;
            const price = parseFloat(a.target_price);
            if (!price) continue;
            _alertPriceLines.push(_candleSeries.createPriceLine({
                price,
                color: a.direction === 'above' ? 'rgba(59, 130, 246, 0.8)' : 'rgba(251, 146, 60, 0.8)',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `🔔 ${a.direction === 'above' ? '↑' : '↓'}`,
            }));
        }
    }

    WS.on('kline_update', Utils.throttleRAF(_onKlineUpdate));
    WS.on('positions_snapshot', _onPositionsSnapshot);
    WS.on('analysis_update', _onAnalysisUpdate);
    WS.on('price_update', msg => {
        if (msg.symbol === _symbol) _updatePriceHeader(null, msg.change_24h);
    });

    // Reload chart when page resumes from background (mobile)
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible' || !_initialized || !_candleSeries) return;
        const el = document.getElementById('view-chart');
        if (el && !el.classList.contains('hidden')) {
            _subscribedStream = null; // force re-subscribe
            loadChart();
        }
    });

    function resetAutoSelect() { _userSelected = false; }

    return { init, loadChart, selectActivePosition, resetAutoSelect, renderAlertLines: _renderAlertLines };
})();
