const Charts = (() => {
    // Cache: positionId -> { chart, series, symbol, lastTimestamp }
    const _posCharts = {};
    let _portfolioChart = null;
    let _portfolioSeries = null;
    let _cockpitChart = null;
    let _cockpitSeries = null;

    // --- Mini position charts ---

    function createMiniChart(containerId, positionId, symbol, entryInfo) {
        const el = document.getElementById(containerId);
        if (!el || _posCharts[positionId]) return;

        const chart = LightweightCharts.createChart(el, {
            width: el.clientWidth,
            height: 120,
            layout: { background: { color: 'transparent' }, textColor: '#9ca3af', fontSize: 10 },
            grid: { vertLines: { visible: false }, horzLines: { visible: false } },
            rightPriceScale: { visible: false },
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

        // Entry price horizontal line (color updated dynamically)
        let priceLine = null;
        if (entryInfo && entryInfo.entryPrice > 0) {
            priceLine = series.createPriceLine({
                price: entryInfo.entryPrice,
                color: 'rgba(148, 163, 184, 0.4)',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: false,
            });
        }

        _posCharts[positionId] = { chart, series, symbol, lastTs: 0, entryInfo: entryInfo || null, priceLine };
        _loadPriceHistory(positionId, symbol);

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) chart.applyOptions({ width: w });
        });
        ro.observe(el);
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
        const winning = _isWinning(entry, currentPrice);
        if (entry.priceLine) {
            entry.priceLine.applyOptions({ color: _pnlColor(winning, 0.5) });
        }
        if (entry._markerTime != null) {
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
            const resp = await fetch(`/api/prices/${symbol}?hours=24&order=asc&limit=1440`);
            const data = await resp.json();
            if (!data.length) return;

            const raw = data.map(p => ({
                time: Math.floor(new Date(p.recorded_at + 'Z').getTime() / 1000),
                value: parseFloat(p.price),
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

    const APPEND_INTERVAL = 60; // match price_record_interval (1 point/min)

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
            rightPriceScale: { borderColor: '#374151' },
            timeScale: {
                borderColor: '#374151',
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

        loadPortfolioData(24);
    }

    async function loadPortfolioData(hours) {
        try {
            const resp = await fetch(`/api/portfolio/history?hours=${hours}`);
            const data = await resp.json();
            if (!_portfolioSeries) return;

            if (!data.length) {
                _portfolioSeries.setData([]);
                return;
            }

            const seen = new Set();
            const points = [];
            for (const d of data) {
                const ts = d.snapshot_at;
                const t = Math.floor(new Date(ts + (ts.endsWith('Z') || ts.includes('+') ? '' : 'Z')).getTime() / 1000);
                const v = parseFloat(d.total_usd);
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
            rightPriceScale: { visible: false },
            timeScale: { visible: false, fixLeftEdge: true, fixRightEdge: true },
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
            crosshairMarkerVisible: false,
        });

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) _cockpitChart.applyOptions({ width: w });
        });
        ro.observe(el);
    }

    async function loadCockpitData() {
        try {
            const resp = await fetch('/api/portfolio/history?hours=24');
            const data = await resp.json();
            if (!_cockpitSeries) return;

            if (!data.length) {
                _cockpitSeries.setData([]);
                return;
            }

            const seen = new Set();
            const points = [];
            for (const d of data) {
                const ts = d.snapshot_at;
                const t = Math.floor(new Date(ts + (ts.endsWith('Z') || ts.includes('+') ? '' : 'Z')).getTime() / 1000);
                const v = parseFloat(d.total_usd);
                if (!seen.has(t) && !isNaN(t) && isFinite(v)) {
                    seen.add(t);
                    points.push({ time: t, value: v });
                }
            }
            points.sort((a, b) => a.time - b.time);

            if (points.length >= 2) {
                const up = points[points.length - 1].value >= points[0].value;
                _cockpitSeries.applyOptions({
                    lineColor: up ? '#22c55e' : '#ef4444',
                    topColor: up ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
                });
            }

            _cockpitSeries.setData(points);
            _cockpitChart.timeScale().fitContent();
        } catch (e) {
            console.error('Chart: failed to load cockpit portfolio data', e);
        }
    }

    // Feed real-time price updates to mini charts (RAF-throttled)
    const _pendingPrices = {};
    const _flushPrices = Utils.throttleRAF(() => {
        for (const [symbol, price] of Object.entries(_pendingPrices)) {
            appendPrice(symbol, price);
        }
    });
    WS.on('price_update', data => {
        _pendingPrices[data.symbol] = data.price;
        _flushPrices();
    });

    return { createMiniChart, appendPrice, cleanup, createPortfolioChart, loadPortfolioData, createCockpitChart, loadCockpitData };
})();
