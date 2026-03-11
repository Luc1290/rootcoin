const Charts = (() => {
    // Cache: positionId -> { chart, series, symbol, lastTimestamp }
    const _posCharts = {};
    let _portfolioChart = null;
    let _portfolioSeries = null;
    let _cockpitChart = null;
    let _cockpitSeries = null;
    let _marketChart = null;
    let _marketSeries = null;
    let _marketSymbol = null;
    let _marketLastTs = 0;

    // --- Mini position charts ---

    function createMiniChart(containerId, positionId, symbol, entryInfo) {
        const el = document.getElementById(containerId);
        if (!el || _posCharts[positionId]) return;

        // Defer if container not yet laid out (e.g. parent just became visible)
        if (!el.clientWidth) {
            requestAnimationFrame(() => createMiniChart(containerId, positionId, symbol, entryInfo));
            return;
        }

        const chart = LightweightCharts.createChart(el, {
            width: el.clientWidth,
            height: 120,
            layout: { background: { color: 'transparent' }, textColor: '#9ca3af', fontSize: 10 },
            grid: { vertLines: { visible: false }, horzLines: { visible: false } },
            rightPriceScale: { visible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
            timeScale: { visible: false, fixLeftEdge: true, fixRightEdge: true },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            handleScroll: false,
            handleScale: false,
        });

        const series = chart.addAreaSeries({
            lineColor: '#3b82f6',
            topColor: 'rgba(59,130,246,0.15)',
            bottomColor: 'rgba(59,130,246,0)',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });

        // Entry price horizontal line (blue)
        let priceLine = null;
        if (entryInfo && entryInfo.entryPrice > 0) {
            priceLine = series.createPriceLine({
                price: entryInfo.entryPrice,
                color: 'rgba(59, 130, 246, 0.5)',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: false,
            });
        }

        // SL price line (red)
        let slLine = null;
        if (entryInfo && entryInfo.slPrice > 0) {
            slLine = series.createPriceLine({
                price: entryInfo.slPrice,
                color: 'rgba(239, 68, 68, 0.5)',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: false,
            });
        }

        // TP price line (green)
        let tpLine = null;
        if (entryInfo && entryInfo.tpPrice > 0) {
            tpLine = series.createPriceLine({
                price: entryInfo.tpPrice,
                color: 'rgba(34, 197, 94, 0.5)',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: false,
            });
        }

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) chart.applyOptions({ width: w });
        });
        ro.observe(el);

        _posCharts[positionId] = { chart, series, symbol, lastTs: 0, entryInfo: entryInfo || null, priceLine, slLine, tpLine, ro };
        _loadPriceHistory(positionId, symbol);
    }

    function updateOrderLines(positionId, slPrice, tpPrice) {
        const entry = _posCharts[positionId];
        if (!entry) return;
        const sl = parseFloat(slPrice) || 0;
        const tp = parseFloat(tpPrice) || 0;

        // Update or create SL line
        if (sl > 0) {
            if (entry.slLine) {
                entry.slLine.applyOptions({ price: sl });
            } else {
                entry.slLine = entry.series.createPriceLine({
                    price: sl,
                    color: 'rgba(239, 68, 68, 0.5)',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: false,
                });
            }
        } else if (entry.slLine) {
            entry.series.removePriceLine(entry.slLine);
            entry.slLine = null;
        }

        // Update or create TP line
        if (tp > 0) {
            if (entry.tpLine) {
                entry.tpLine.applyOptions({ price: tp });
            } else {
                entry.tpLine = entry.series.createPriceLine({
                    price: tp,
                    color: 'rgba(34, 197, 94, 0.5)',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: false,
                });
            }
        } else if (entry.tpLine) {
            entry.series.removePriceLine(entry.tpLine);
            entry.tpLine = null;
        }

        // Force immediate rescale to include/exclude SL/TP
        entry.chart.timeScale().fitContent();
    }

    function _isWinning(entry, currentPrice) {
        if (!entry.entryInfo || !entry.entryInfo.entryPrice) return null;
        const ep = entry.entryInfo.entryPrice;
        if (entry.entryInfo.side === 'LONG') return currentPrice >= ep;
        return currentPrice <= ep;
    }

    function _pnlColor(winning, alpha) {
        if (winning === null) return `rgba(148, 163, 184, ${alpha})`;
        return winning
            ? `rgba(52, 211, 153, ${alpha})`
            : `rgba(248, 113, 113, ${alpha})`;
    }

    function _updateEntryVisuals(entry, currentPrice) {
        if (entry.priceLine) {
            entry.priceLine.applyOptions({ color: 'rgba(59, 130, 246, 0.5)' });
        }
        if (entry._markerTime != null) {
            const winning = _isWinning(entry, currentPrice);
            entry.series.setMarkers([{
                time: entry._markerTime,
                position: 'belowBar',
                color: _pnlColor(winning, 1),
                shape: 'arrowUp',
                text: 'Entry',
            }]);
        }
    }

    async function _loadPriceHistory(positionId, symbol) {
        try {
            // Dynamic interval: 1m for <24h, 5m for longer positions
            let interval = '1m', limit = 1440;
            const info = _posCharts[positionId] && _posCharts[positionId].entryInfo;
            if (info && info.openedAt) {
                const ageMin = (Date.now() - new Date(info.openedAt + 'Z').getTime()) / 60000;
                if (ageMin > 1380) { // >23h → switch to 5m
                    interval = '5m';
                    limit = Math.max(60, Math.min(1440, Math.ceil(ageMin / 5 * 1.2) + 12));
                } else {
                    limit = Math.max(60, Math.min(1440, Math.ceil(ageMin * 1.2) + 30));
                }
            }
            const resp = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=${limit}&indicators=`);
            const data = await resp.json();
            const klines = data.klines || [];
            if (!klines.length) return;

            const raw = klines.map(k => ({
                time: Math.floor(new Date(k.open_time + 'Z').getTime() / 1000),
                value: parseFloat(k.close),
            })).filter(p => isFinite(p.time) && isFinite(p.value) && p.value > 0);
            // LightweightCharts requires strictly increasing timestamps
            const points = [];
            for (const p of raw) {
                if (!points.length || p.time > points[points.length - 1].time) {
                    points.push(p);
                }
            }
            if (!points.length) return;
            const entry = _posCharts[positionId];
            if (entry) {
                entry.series.setData(points);
                entry.lastTs = points[points.length - 1].time;
                const lastPrice = points[points.length - 1].value;

                // Marker at entry point
                if (entry.entryInfo && entry.entryInfo.openedAt) {
                    const entryTs = Math.floor(new Date(entry.entryInfo.openedAt + 'Z').getTime() / 1000);
                    let closest = points[0];
                    for (const pt of points) {
                        if (Math.abs(pt.time - entryTs) < Math.abs(closest.time - entryTs)) {
                            closest = pt;
                        }
                    }
                    entry._markerTime = closest.time;
                }

                _updateEntryVisuals(entry, lastPrice);
                entry.chart.timeScale().fitContent();
            }
        } catch (e) {
            console.error('Chart: failed to load history for', symbol, e);
        }
    }

    const APPEND_INTERVAL = 15; // 1 point every 15s for smoother charts

    function appendPrice(symbol, priceStr) {
        const now = Math.floor(Date.now() / 1000);
        const value = parseFloat(priceStr);
        if (!value || !isFinite(value)) return;

        for (const entry of Object.values(_posCharts)) {
            if (entry.symbol !== symbol || !entry.lastTs) continue;
            try {
                if (now - entry.lastTs >= APPEND_INTERVAL) {
                    entry.series.update({ time: now, value });
                    entry.lastTs = now;
                    entry.chart.timeScale().fitContent();
                } else {
                    entry.series.update({ time: entry.lastTs, value });
                }
                _updateEntryVisuals(entry, value);
            } catch (_) { /* chart not ready */ }
        }
    }

    function cleanup(activeIds) {
        const active = new Set(activeIds.map(Number));
        for (const id of Object.keys(_posCharts)) {
            if (!active.has(Number(id))) {
                if (_posCharts[id].ro) _posCharts[id].ro.disconnect();
                _posCharts[id].chart.remove();
                delete _posCharts[id];
            }
        }
    }

    // --- Portfolio chart ---

    function createPortfolioChart(containerId) {
        const el = document.getElementById(containerId);
        if (!el || _portfolioChart) return;

        _portfolioChart = LightweightCharts.createChart(el, {
            width: el.clientWidth,
            height: 200,
            layout: { background: { color: 'transparent' }, textColor: '#9ca3af', fontSize: 11 },
            grid: {
                vertLines: { color: 'rgba(55,65,81,0.3)' },
                horzLines: { color: 'rgba(55,65,81,0.3)' },
            },
            rightPriceScale: { borderColor: '#3d3836' },
            timeScale: {
                borderColor: '#3d3836',
                timeVisible: true,
                secondsVisible: false,
                fixLeftEdge: true,
                fixRightEdge: true,
            },
            crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
        });

        _portfolioSeries = _portfolioChart.addAreaSeries({
            lineColor: '#22c55e',
            topColor: 'rgba(34,197,94,0.2)',
            bottomColor: 'rgba(34,197,94,0)',
            lineWidth: 2,
            priceLineVisible: true,
            lastValueVisible: true,
        });

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _portfolioChart.applyOptions({ width: w });
        });
        ro.observe(el);

        loadPortfolioData(720);
    }

    async function loadPortfolioData(hours) {
        try {
            const resp = await fetch(`/api/journal/equity?hours=${hours}`);
            const data = await resp.json();
            if (!_portfolioSeries) return;

            if (!data.points || !data.points.length) {
                _portfolioSeries.setData([]);
                return;
            }

            const seen = new Set();
            const points = [];
            for (const p of data.points) {
                const ts = p.snapshot_at;
                const t = Math.floor(new Date(ts + (ts.endsWith('Z') || ts.includes('+') ? '' : 'Z')).getTime() / 1000);
                const v = parseFloat(p.total_usd);
                if (!seen.has(t) && !isNaN(t) && isFinite(v)) {
                    seen.add(t);
                    points.push({ time: t, value: v });
                }
            }
            points.sort((a, b) => a.time - b.time);
            _portfolioSeries.setData(points);
            _portfolioChart.timeScale().fitContent();
        } catch (e) {
            console.error('Chart: failed to load portfolio history', e);
        }
    }

    // --- Cockpit sparkline chart ---

    function createCockpitChart(containerId) {
        const el = document.getElementById(containerId);
        if (!el || _cockpitChart) return;

        _cockpitChart = LightweightCharts.createChart(el, {
            width: el.clientWidth,
            height: 80,
            layout: { background: { color: 'transparent' }, textColor: '#9ca3af', fontSize: 10 },
            grid: { vertLines: { visible: false }, horzLines: { visible: false } },
            rightPriceScale: { borderColor: 'transparent', textColor: '#9ca3af', minimumWidth: 50 },
            timeScale: { visible: false, fixLeftEdge: true, fixRightEdge: true },
            crosshair: {
                horzLine: { color: 'rgba(255,255,255,0.15)', style: LightweightCharts.LineStyle.Dotted, labelBackgroundColor: '#3d3836' },
                vertLine: { visible: false },
            },
            handleScroll: false,
            handleScale: false,
        });

        _cockpitSeries = _cockpitChart.addAreaSeries({
            lineColor: '#22c55e',
            topColor: 'rgba(34,197,94,0.12)',
            bottomColor: 'rgba(34,197,94,0)',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: true,
            priceFormat: { type: 'custom', formatter: v => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 }) },
        });

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _cockpitChart.applyOptions({ width: w });
        });
        ro.observe(el);
    }

    async function loadCockpitData() {
        try {
            const resp = await fetch('/api/journal/equity?hours=48');
            const data = await resp.json();
            if (!_cockpitSeries) return;

            if (!data.points || !data.points.length) {
                _cockpitSeries.setData([]);
                return;
            }

            const seen = new Set();
            const points = [];
            for (const p of data.points) {
                const ts = p.snapshot_at;
                const t = Math.floor(new Date(ts + (ts.endsWith('Z') || ts.includes('+') ? '' : 'Z')).getTime() / 1000);
                const v = parseFloat(p.total_usd);
                if (!seen.has(t) && !isNaN(t) && isFinite(v)) {
                    seen.add(t);
                    points.push({ time: t, value: v });
                }
            }
            points.sort((a, b) => a.time - b.time);

            _cockpitSeries.setData(points);
            _cockpitChart.timeScale().fitContent();
        } catch (e) {
            console.error('Chart: failed to load cockpit portfolio data', e);
        }
    }

    // --- Cockpit market chart (BTC / active position symbol) ---

    function createCockpitMarketChart(containerId) {
        const el = document.getElementById(containerId);
        if (!el) return;
        if (_marketChart) {
            _marketChart.remove();
            _marketChart = null;
            _marketSeries = null;
        }

        _marketChart = LightweightCharts.createChart(el, {
            width: el.clientWidth,
            height: 120,
            layout: { background: { color: 'transparent' }, textColor: '#9ca3af', fontSize: 10 },
            grid: { vertLines: { visible: false }, horzLines: { visible: false } },
            rightPriceScale: { borderColor: 'transparent', textColor: '#9ca3af' },
            timeScale: { visible: false, fixLeftEdge: true, fixRightEdge: true },
            handleScroll: false,
            handleScale: false,
        });

        _marketSeries = _marketChart.addAreaSeries({
            lineColor: '#f59e0b',
            topColor: 'rgba(245,158,11,0.12)',
            bottomColor: 'rgba(245,158,11,0)',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _marketChart.applyOptions({ width: w });
        });
        ro.observe(el);
    }

    async function loadCockpitMarketData(symbol) {
        _marketSymbol = symbol;
        _marketLastTs = 0;
        if (!_marketSeries) return null;

        try {
            const resp = await fetch(`/api/klines/${symbol}?interval=1m&limit=1440`);
            const data = await resp.json();
            const klines = data.klines || [];

            const points = [];
            for (const k of klines) {
                const t = Math.floor(new Date(k.open_time + 'Z').getTime() / 1000);
                const v = parseFloat(k.close);
                if (isFinite(t) && isFinite(v) && v > 0) {
                    if (!points.length || t > points[points.length - 1].time) {
                        points.push({ time: t, value: v });
                    }
                }
            }

            if (points.length >= 2) {
                const up = points[points.length - 1].value >= points[0].value;
                _marketSeries.applyOptions({
                    lineColor: up ? '#22c55e' : '#ef4444',
                    topColor: up ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
                });
            }

            _marketSeries.setData(points);
            if (points.length) _marketLastTs = points[points.length - 1].time;
            _marketChart.timeScale().fitContent();

            if (points.length >= 2) {
                const first = points[0].value;
                const last = points[points.length - 1].value;
                return { price: last, change: ((last - first) / first) * 100 };
            }
            return points.length ? { price: points[points.length - 1].value, change: 0 } : null;
        } catch (e) {
            console.error('Chart: failed to load market data for', symbol, e);
            return null;
        }
    }

    function _updateMarketChartPrice(symbol, priceStr) {
        if (!_marketChart || !_marketSeries || _marketSymbol !== symbol) return;
        const now = Math.floor(Date.now() / 1000);
        const value = parseFloat(priceStr);
        if (!value || !isFinite(value)) return;
        try {
            if (now - _marketLastTs >= 60) {
                _marketSeries.update({ time: now, value });
                _marketLastTs = now;
            } else if (_marketLastTs) {
                _marketSeries.update({ time: _marketLastTs, value });
            }
        } catch (_) { /* chart not ready */ }
    }

    function getCockpitMarketSymbol() {
        return _marketSymbol;
    }

    // Feed real-time price updates to mini charts (RAF-throttled)
    const _pendingPrices = {};
    const _flushPrices = Utils.throttleRAF(() => {
        for (const [symbol, price] of Object.entries(_pendingPrices)) {
            appendPrice(symbol, price);
            _updateMarketChartPrice(symbol, price);
        }
    });
    WS.on('price_update', data => {
        _pendingPrices[data.symbol] = data.price;
        _flushPrices();
    });

    function updateCockpitColor(pnl, hasPositions) {
        if (!_cockpitSeries) return;
        const color = !hasPositions ? '#9ca3af' : pnl >= 0 ? '#22c55e' : '#ef4444';
        const fill = !hasPositions ? 'rgba(156,163,175,0.08)' : pnl >= 0 ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)';
        _cockpitSeries.applyOptions({ lineColor: color, topColor: fill });
    }

    return { createMiniChart, updateOrderLines, appendPrice, cleanup, createPortfolioChart, loadPortfolioData, createCockpitChart, loadCockpitData, updateCockpitColor, createCockpitMarketChart, loadCockpitMarketData, getCockpitMarketSymbol };
})();
