const KlineChart = (() => {
    let _symbol = 'BTCUSDC';
    let _interval = '15m';
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
    let _activeCycleRefs = []; // {area, line, offset, entryPrice}
    let _activeIndicators = new Set(['ma', 'volume', 'obv', 'rsi', 'macd', 'buy_sell', 'cycles']);
    let _loading = false;
    let _initialized = false;
    let _syncing = false;
    let _subscribedStream = null;
    let _crosshairSyncing = false;
    let _chartRegistry = [];
    let _seriesDataMap = {};
    let _currentPrice = null;
    const _observers = [];
    let _orderPriceLines = [];
    let _levelPriceLines = [];
    let _cachedPositions = null;
    let _cachedAnalysis = null;
    let _lastCandleTime = null;
    let _liveData = null; // cached kline arrays for live indicator updates

    function _observeResize(el, chart) {
        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) chart.applyOptions({ width: w });
        });
        ro.observe(el);
        _observers.push(ro);
    }

    const C = {
        bg: 'transparent',
        text: '#9ca3af',
        grid: 'rgba(55, 65, 81, 0.3)',
        border: '#374151',
        upCandle: '#22c55e',
        downCandle: '#ef4444',
        volUp: 'rgba(34, 197, 94, 0.4)',
        volDown: 'rgba(239, 68, 68, 0.4)',
        ma7: '#f59e0b',
        ma25: '#3b82f6',
        ma99: '#a855f7',
        bb: 'rgba(167, 139, 250, 0.7)',
        rsi: '#f59e0b',
        obv: '#06b6d4',
        macdLine: '#3b82f6',
        macdSignal: '#f59e0b',
        macdHistUp: 'rgba(34,197,94,0.5)',
        macdHistDown: 'rgba(239,68,68,0.5)',
        bsBuy: 'rgba(34,197,94,0.6)',
        bsSell: 'rgba(239,68,68,0.6)',
        buy: '#22c55e',
        sell: '#ef4444',
    };

    function init() {
        if (_initialized) return;
        _initialized = true;

        document.querySelectorAll('.chart-interval-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                _interval = btn.dataset.interval;
                document.querySelectorAll('.chart-interval-btn').forEach(b =>
                    b.classList.toggle('active', b === btn));
                loadChart();
            });
        });

        const sel = document.getElementById('chart-symbol');
        if (sel) sel.addEventListener('change', () => {
            _symbol = sel.value;
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

    function _updateSymbolSelect(symbols) {
        const sel = document.getElementById('chart-symbol');
        if (!sel) return;
        const current = sel.value;
        const unique = [...new Set(symbols)];
        sel.innerHTML = unique.map(s =>
            `<option value="${s}" ${s === current ? 'selected' : ''}>${s}</option>`
        ).join('');
    }

    function _chartOptions(height, showTimeScale) {
        return {
            width: 0,
            height: height,
            layout: { background: { color: C.bg }, textColor: C.text, fontSize: 11 },
            grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
            rightPriceScale: { borderColor: C.border, minimumWidth: 70 },
            timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false, visible: showTimeScale },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        };
    }

    function _createMainChart() {
        const el = document.getElementById('kline-chart-main');
        if (!el || _mainChart) return;

        _mainChart = LightweightCharts.createChart(el, _chartOptions(500, true));
        _mainChart.applyOptions({ width: el.clientWidth });

        _candleSeries = _mainChart.addCandlestickSeries({
            upColor: C.upCandle, downColor: C.downCandle,
            borderVisible: false,
            wickUpColor: C.upCandle, wickDownColor: C.downCandle,
        });

        // Floating % label next to crosshair
        const pctLabel = document.createElement('div');
        pctLabel.style.cssText = 'position:absolute;right:0;padding:2px 6px;font-size:12px;font-weight:600;pointer-events:none;z-index:10;display:none;white-space:nowrap;background:rgba(0,0,0,0.75);border-radius:3px;';
        el.style.position = 'relative';
        el.appendChild(pctLabel);

        _mainChart.subscribeCrosshairMove(param => {
            if (!param.point || !_currentPrice) { pctLabel.style.display = 'none'; return; }
            const price = _candleSeries.coordinateToPrice(param.point.y);
            if (price == null) { pctLabel.style.display = 'none'; return; }
            const pct = ((price - _currentPrice) / _currentPrice * 100);
            const sign = pct >= 0 ? '+' : '';
            const color = pct >= 0 ? C.upCandle : C.downCandle;
            pctLabel.textContent = `${sign}${pct.toFixed(2)}%`;
            pctLabel.style.color = color;
            pctLabel.style.top = (param.point.y + 12) + 'px';
            pctLabel.style.display = 'block';
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

        _volChart = LightweightCharts.createChart(el, _chartOptions(100, false));
        _volChart.applyOptions({ width: el.clientWidth });

        _volSeries = _volChart.addHistogramSeries({
            priceFormat: { type: 'volume' },
        });

        _syncTimeScales(_mainChart, _volChart);
        _registerChart(_volChart, _volSeries, 'volume');
        _observeResize(el, _volChart);
    }

    function _ensureRsiChart() {
        if (_rsiChart) return;
        const el = document.getElementById('kline-chart-rsi');
        if (!el) return;

        _rsiChart = LightweightCharts.createChart(el, _chartOptions(120, false));
        _rsiChart.applyOptions({ width: el.clientWidth });

        _rsiSeries = _rsiChart.addLineSeries({
            color: C.rsi, lineWidth: 1.5,
            lastValueVisible: true, priceLineVisible: false,
        });
        _rsiSeries.createPriceLine({ price: 70, color: 'rgba(239,68,68,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true });
        _rsiSeries.createPriceLine({ price: 30, color: 'rgba(34,197,94,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true });

        _syncTimeScales(_mainChart, _rsiChart);
        _registerChart(_rsiChart, _rsiSeries, 'rsi');
        _observeResize(el, _rsiChart);
    }

    function _ensureObvChart() {
        if (_obvChart) return;
        const el = document.getElementById('kline-chart-obv');
        if (!el) return;

        _obvChart = LightweightCharts.createChart(el, _chartOptions(120, false));
        _obvChart.applyOptions({ width: el.clientWidth });

        _obvSeries = _obvChart.addLineSeries({
            color: C.obv, lineWidth: 1.5,
            lastValueVisible: true, priceLineVisible: false,
        });

        _syncTimeScales(_mainChart, _obvChart);
        _registerChart(_obvChart, _obvSeries, 'obv');
        _observeResize(el, _obvChart);
    }

    function _ensureMacdChart() {
        if (_macdChart) return;
        const el = document.getElementById('kline-chart-macd');
        if (!el) return;

        _macdChart = LightweightCharts.createChart(el, _chartOptions(140, false));
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

        _bsChart = LightweightCharts.createChart(el, _chartOptions(100, false));
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
    }

    function _toTs(isoStr) {
        return Math.floor(new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z').getTime() / 1000);
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
        _subscribeWS();

        try {
            const indList = [..._activeIndicators].filter(i => i !== 'trades' && i !== 'cycles').join(',');
            const resp = await fetch(`/api/klines/${_symbol}?interval=${_interval}&indicators=${indList}&limit=500`);
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

            // Trade markers
            if (_activeIndicators.has('trades')) {
                await _loadTradeMarkers(klines);
            } else {
                _candleSeries.setMarkers([]);
            }

            // Cycles overlay
            if (_activeIndicators.has('cycles')) {
                await _loadCycles(candles);
            } else {
                _clearCycles();
            }

            // Order + Level overlay lines
            _renderOrderLines();
            _renderLevelLines();

            // Show last ~150 candles with 1/4 empty space on the right
            const total = candles.length;
            const visible = Math.min(total, 150);
            const rightPad = Math.round(visible / 3);
            _mainChart.timeScale().setVisibleLogicalRange({
                from: total - visible,
                to: total - 1 + rightPad,
            });
        } catch (e) {
            console.error('KlineChart: load failed', e);
        } finally {
            _loading = false;
        }
    }

    async function _loadTradeMarkers(klines) {
        try {
            const start = klines[0].open_time;
            const end = klines[klines.length - 1].close_time;
            const resp = await fetch(`/api/klines/${_symbol}/trades?start_time=${start}&end_time=${end}`);
            const trades = await resp.json();
            const markers = trades.map(t => ({
                time: _toTs(t.executed_at),
                position: t.side === 'BUY' ? 'belowBar' : 'aboveBar',
                color: t.side === 'BUY' ? C.buy : C.sell,
                shape: t.side === 'BUY' ? 'arrowUp' : 'arrowDown',
                text: t.side[0] + ' ' + parseFloat(t.quantity),
            }));
            markers.sort((a, b) => a.time - b.time);
            _candleSeries.setMarkers(markers);
        } catch (e) {
            console.error('KlineChart: markers failed', e);
        }
    }

    let _entryPriceLines = [];

    function _clearCycles() {
        _cycleSeries.forEach(s => _mainChart.removeSeries(s));
        _cycleSeries = [];
        _entryPriceLines.forEach(l => _candleSeries.removePriceLine(l));
        _entryPriceLines = [];
        _activeCycleRefs = [];
        _cyclesRendered = { symbol: null, interval: null };
    }

    async function _loadCycles(candles) {
        // Same symbol + interval: series already correct, skip entirely
        if (_cyclesRendered.symbol === _symbol && _cyclesRendered.interval === _interval) {
            return;
        }

        _cycleSeries.forEach(s => _mainChart.removeSeries(s));
        _cycleSeries = [];
        _entryPriceLines.forEach(l => _candleSeries.removePriceLine(l));
        _entryPriceLines = [];
        _activeCycleRefs = [];

        try {
            // Refetch if symbol changed or cache has active cycles (may have closed)
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
            if (!cycles.length || !candles.length) {
                _cyclesRendered = { symbol: _symbol, interval: _interval };
                return;
            }

            const now = Math.floor(Date.now() / 1000);

            cycles.forEach(c => {
                if (!c.opened_at) return;
                const openTs = _toTs(c.opened_at);
                const closeTs = c.is_active ? now : (c.closed_at ? _toTs(c.closed_at) : now);

                let color;
                if (c.is_active) {
                    color = 'rgba(99,179,255,';   // bright blue
                } else if (c.realized_pnl && parseFloat(c.realized_pnl) > 0) {
                    color = 'rgba(34,197,94,';    // green
                } else {
                    color = 'rgba(239,68,68,';    // red
                }

                const cycleCandles = candles.filter(cd => cd.time >= openTs && cd.time <= closeTs);
                if (!cycleCandles.length) return;

                const allHighs = candles.map(cd => cd.high);
                const allLows = candles.map(cd => cd.low);
                const range = Math.max(...allHighs) - Math.min(...allLows);
                const offset = range * 0.15; 

                const areaData = cycleCandles.map(cd => ({
                    time: cd.time,
                    value: cd.close + offset,
                }));

                const opHigh = c.is_active ? '0.25)' : '0.18)';
                const opLow = c.is_active ? '0.06)' : '0.03)';
                const opLine = c.is_active ? '0.5)' : '0.3)';
                const area = _mainChart.addAreaSeries({
                    topColor: color + opHigh,
                    bottomColor: color + opLow,
                    lineColor: color + opLine,
                    lineWidth: c.is_active ? 2 : 1,
                    lastValueVisible: false,
                    priceLineVisible: false,
                    crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null,
                });
                area.setData(areaData);
                _cycleSeries.push(area);

                // Entry price line (active cycles only)
                let entryPrice = null;
                if (c.entry_price && c.is_active) {
                    entryPrice = parseFloat(c.entry_price);
                    _entryPriceLines.push(_candleSeries.createPriceLine({
                        price: entryPrice,
                        color: color + '0.6)',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: false,
                        title: 'Entry ' + Utils.fmtPrice(entryPrice),
                    }));
                }

                if (c.is_active) {
                    _activeCycleRefs.push({ area, offset, entryPrice });
                }
            });
            _cyclesRendered = { symbol: _symbol, interval: _interval };
        } catch (e) {
            console.error('KlineChart: cycles failed', e);
        }
    }

    // ── Order lines (SL/TP) ────────────────────────────────
    function _clearOrderLines() {
        _orderPriceLines.forEach(l => _candleSeries.removePriceLine(l));
        _orderPriceLines = [];
    }

    function _renderOrderLines() {
        _clearOrderLines();
        if (!_candleSeries || !_cachedPositions || !_activeIndicators.has('orders')) return;
        const pos = _cachedPositions.find(p => p.symbol === _symbol);
        if (!pos) return;
        if (pos.sl_price) {
            _orderPriceLines.push(_candleSeries.createPriceLine({
                price: parseFloat(pos.sl_price),
                color: '#ef4444',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'SL',
            }));
        }
        if (pos.tp_price) {
            _orderPriceLines.push(_candleSeries.createPriceLine({
                price: parseFloat(pos.tp_price),
                color: '#22c55e',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'TP',
            }));
        }
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
            const color = lvl.type === 'PP'
                ? 'rgba(59,130,246,0.5)'
                : above ? 'rgba(34,197,94,0.5)' : 'rgba(239,68,68,0.5)';
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
        for (const ref of _activeCycleRefs) {
            ref.area.update({ time: t, value: _currentPrice + ref.offset });
        }

        // Live indicator update
        if (_liveData) {
            const high = parseFloat(data.high);
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
        _updateSymbolSelect([...new Set([...base, ...posSymbols])]);
        // Detect position closed for current symbol → invalidate cycles cache
        const prev = _cachedPositions;
        if (prev) {
            const hadSymbol = prev.some(p => p.symbol === _symbol && p.is_active);
            const hasSymbol = data.some(p => p.symbol === _symbol && p.is_active);
            if (hadSymbol && !hasSymbol) {
                _cyclesCache = { symbol: null, data: null };
                _cyclesRendered = { symbol: null, interval: null };
                if (_activeIndicators.has('cycles')) loadChart();
            }
        }
        _cachedPositions = data;
        if (_activeIndicators.has('orders')) _renderOrderLines();
    }

    function _onAnalysisUpdate(data) {
        if (!data) return;
        _cachedAnalysis = data;
        if (_activeIndicators.has('levels')) _renderLevelLines();
    }

    WS.on('kline_update', Utils.throttleRAF(_onKlineUpdate));
    WS.on('positions_snapshot', _onPositionsSnapshot);
    WS.on('analysis_update', _onAnalysisUpdate);

    // Reload chart when page resumes from background (mobile)
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible' || !_initialized || !_candleSeries) return;
        const el = document.getElementById('view-chart');
        if (el && !el.classList.contains('hidden')) {
            _subscribedStream = null; // force re-subscribe
            loadChart();
        }
    });

    return { init, loadChart };
})();
