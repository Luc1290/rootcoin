const KlineChart = (() => {
    let _symbol = 'BTCUSDC';
    let _interval = '1h';
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
    let _activeIndicators = new Set(['ma', 'volume', 'obv', 'rsi', 'macd', 'buy_sell', 'cycles']);
    let _loading = false;
    let _initialized = false;
    let _syncing = false;
    let _subscribedStream = null;
    let _crosshairSyncing = false;
    let _chartRegistry = [];
    let _seriesDataMap = {};

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
        bb: 'rgba(147, 51, 234, 0.5)',
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
            const resp = await fetch('/api/klines/symbols');
            const symbols = await resp.json();
            _updateSymbolSelect(symbols);
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

        new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _mainChart.applyOptions({ width: w });
        }).observe(el);
    }

    function _ensureSubChart(chartRef, seriesRef, elId, height, seriesFactory) {
        if (chartRef.chart) return;
        const el = document.getElementById(elId);
        if (!el) return;

        chartRef.chart = LightweightCharts.createChart(el, _chartOptions(height, false));
        chartRef.chart.applyOptions({ width: el.clientWidth });

        seriesRef.series = seriesFactory(chartRef.chart);

        _syncTimeScales(_mainChart, chartRef.chart);

        new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) chartRef.chart.applyOptions({ width: w });
        }).observe(el);
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

        new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _volChart.applyOptions({ width: w });
        }).observe(el);
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

        new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _rsiChart.applyOptions({ width: w });
        }).observe(el);
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

        new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _obvChart.applyOptions({ width: w });
        }).observe(el);
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

        new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _macdChart.applyOptions({ width: w });
        }).observe(el);
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

        new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _bsChart.applyOptions({ width: w });
        }).observe(el);
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

    function _clearCycles() {
        _cycleSeries.forEach(s => _mainChart.removeSeries(s));
        _cycleSeries = [];
    }

    async function _loadCycles(candles) {
        _clearCycles();
        try {
            const resp = await fetch(`/api/cycles?symbol=${_symbol}&limit=50`);
            const cycles = await resp.json();
            if (!cycles.length || !candles.length) return;

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

                // Area that follows candle shape but extends above
                const cycleCandles = candles.filter(cd => cd.time >= openTs && cd.time <= closeTs);
                if (!cycleCandles.length) return;

                // Offset = 50% of the price range so the area sits above candles
                const allHighs = candles.map(cd => cd.high);
                const allLows = candles.map(cd => cd.low);
                const range = Math.max(...allHighs) - Math.min(...allLows);
                const offset = range * 0.5;

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
                });
                area.setData(areaData);
                _cycleSeries.push(area);

                // Entry price line
                if (c.entry_price) {
                    const entryPrice = parseFloat(c.entry_price);
                    const pnlTxt = c.is_active
                        ? (c.pnl_pct ? parseFloat(c.pnl_pct).toFixed(1) + '%' : c.side)
                        : (c.realized_pnl_pct ? parseFloat(c.realized_pnl_pct).toFixed(1) + '%' : '');
                    const line = _mainChart.addLineSeries({
                        color: color + '0.6)',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        lastValueVisible: false,
                        priceLineVisible: false,
                        title: pnlTxt,
                    });
                    line.setData([
                        { time: areaData[0].time, value: entryPrice },
                        { time: areaData[areaData.length - 1].time, value: entryPrice },
                    ]);
                    _cycleSeries.push(line);
                }
            });
        } catch (e) {
            console.error('KlineChart: cycles failed', e);
        }
    }

    // Live candle update from WS
    function _onKlineUpdate(data) {
        if (!_candleSeries || data.symbol !== _symbol || data.interval !== _interval) return;
        _candleSeries.update({
            time: Math.floor(data.open_time / 1000),
            open: parseFloat(data.open),
            high: parseFloat(data.high),
            low: parseFloat(data.low),
            close: parseFloat(data.close),
        });
        if (_volSeries && _activeIndicators.has('volume')) {
            _volSeries.update({
                time: Math.floor(data.open_time / 1000),
                value: parseFloat(data.volume),
                color: parseFloat(data.close) >= parseFloat(data.open) ? C.volUp : C.volDown,
            });
        }
    }

    // Update symbol dropdown when positions change
    function _onPositionsSnapshot(data) {
        if (!data || !data.length) return;
        const base = ['BTCUSDC', 'ETHUSDC'];
        const posSymbols = data.map(p => p.symbol);
        _updateSymbolSelect([...new Set([...base, ...posSymbols])]);
    }

    WS.on('kline_update', _onKlineUpdate);
    WS.on('positions_snapshot', _onPositionsSnapshot);

    return { init, loadChart };
})();
