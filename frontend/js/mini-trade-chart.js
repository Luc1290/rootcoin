const MiniTradeChart = (() => {
    const _charts = {};
    let _idCounter = 0;

    const C = {
        upCandle: '#22c55e',
        downCandle: '#ef4444',
        entry: '#3b82f6',
        sl: '#ef4444',
        tp: '#22c55e',
    };

    function create(containerId, opts = {}) {
        const el = document.getElementById(containerId);
        if (!el) return null;

        const id = `mtc_${++_idCounter}`;
        const height = opts.height || 160;

        const chart = LightweightCharts.createChart(el, {
            width: el.clientWidth,
            height,
            layout: { background: { color: 'transparent' }, textColor: '#9ca3af', fontSize: 10 },
            grid: { vertLines: { visible: false }, horzLines: { visible: false } },
            rightPriceScale: { borderColor: 'transparent', textColor: '#9ca3af', scaleMargins: { top: 0.05, bottom: 0.05 } },
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

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) chart.applyOptions({ width: w });
        });
        ro.observe(el);

        const entry = {
            id,
            chart,
            series,
            ro,
            el,
            symbol: opts.symbol || '',
            entryLine: null,
            slLine: null,
            tpLine: null,
            labelEl: null,
            timingEl: null,
            lastTs: 0,
        };

        if (opts.entryPrice) _addLine(entry, 'entryLine', opts.entryPrice, C.entry, LightweightCharts.LineStyle.Solid);
        if (opts.slPrice) _addLine(entry, 'slLine', opts.slPrice, C.sl, LightweightCharts.LineStyle.Dashed);
        if (opts.tpPrice) _addLine(entry, 'tpLine', opts.tpPrice, C.tp, LightweightCharts.LineStyle.Dashed);

        _charts[id] = entry;
        return id;
    }

    function setData(chartId, klines) {
        const entry = _charts[chartId];
        if (!entry) return;

        const data = [];
        for (const k of klines) {
            const t = typeof k.time === 'number' ? k.time : Math.floor(new Date(k.time + 'Z').getTime() / 1000);
            const v = parseFloat(k.close);
            if (!isFinite(t) || !isFinite(v) || v <= 0) continue;
            if (data.length && t <= data[data.length - 1].time) continue;
            data.push({ time: t, value: v });
        }

        if (!data.length) return;
        entry.series.setData(data);
        entry.lastTs = data[data.length - 1].time;
        entry.chart.timeScale().fitContent();
    }

    function updateLevels(chartId, levels) {
        const entry = _charts[chartId];
        if (!entry) return;

        if (levels.entryPrice) {
            _updateOrAddLine(entry, 'entryLine', parseFloat(levels.entryPrice), C.entry, LightweightCharts.LineStyle.Solid);
        }
        if (levels.slPrice) {
            _updateOrAddLine(entry, 'slLine', parseFloat(levels.slPrice), C.sl, LightweightCharts.LineStyle.Dashed);
        }
        if (levels.tpPrice) {
            _updateOrAddLine(entry, 'tpLine', parseFloat(levels.tpPrice), C.tp, LightweightCharts.LineStyle.Dashed);
        }
    }

    function appendCandle(chartId, candle) {
        const entry = _charts[chartId];
        if (!entry) return;

        const t = typeof candle.time === 'number' ? candle.time : Math.floor(new Date(candle.time + 'Z').getTime() / 1000);
        const v = parseFloat(candle.close);
        if (!isFinite(t) || !isFinite(v) || v <= 0) return;

        try {
            entry.series.update({ time: t, value: v });
            if (t > entry.lastTs) entry.lastTs = t;
        } catch (_) { /* chart not ready */ }
    }

    function addLabel(chartId, direction, strength) {
        const entry = _charts[chartId];
        if (!entry) return;

        if (entry.labelEl) entry.labelEl.remove();

        const label = document.createElement('div');
        label.className = 'mini-chart-label';

        const isLong = direction === 'LONG';
        let text = isLong ? 'LONG' : 'SHORT';
        let cls = isLong ? 'long' : 'short';

        if (strength === 'strong') {
            text += ' FORT';
            cls += ' strong';
        }

        label.textContent = text;
        label.classList.add(cls);
        entry.el.style.position = 'relative';
        entry.el.appendChild(label);
        entry.labelEl = label;
    }

    function addTiming(chartId, timing) {
        const entry = _charts[chartId];
        if (!entry) return;

        if (entry.timingEl) entry.timingEl.remove();
        if (!timing) return;

        const badge = document.createElement('div');
        badge.className = `mini-chart-timing ${timing.status}`;

        if (timing.status === 'ready') {
            badge.innerHTML = `<span class="timing-icon">&#10003;</span> ${timing.summary || 'Pret'}`;
        } else if (timing.status === 'wait') {
            badge.innerHTML = `<span class="timing-icon">&#9203;</span> ${timing.summary || 'Attendre'}`;
        } else {
            badge.innerHTML = `<span class="timing-icon">&#9888;</span> ${timing.summary || 'Prudence'}`;
        }

        entry.el.style.position = 'relative';
        entry.el.appendChild(badge);
        entry.timingEl = badge;
    }

    function destroy(chartId) {
        const entry = _charts[chartId];
        if (!entry) return;
        if (entry.labelEl) entry.labelEl.remove();
        if (entry.timingEl) entry.timingEl.remove();
        if (entry.ro) entry.ro.disconnect();
        entry.chart.remove();
        delete _charts[chartId];
    }

    function destroyExcept(keepIds) {
        const keep = new Set(keepIds);
        for (const id of Object.keys(_charts)) {
            if (!keep.has(id)) destroy(id);
        }
    }

    function destroyAll() {
        for (const id of Object.keys(_charts)) destroy(id);
    }

    function getChartIds() {
        return Object.keys(_charts);
    }

    function getEntry(chartId) {
        return _charts[chartId] || null;
    }

    // ── Helpers ──────────────────────────────────────────────

    function _addLine(entry, key, price, color, style) {
        const p = parseFloat(price);
        if (!p || !isFinite(p)) return;
        entry[key] = entry.series.createPriceLine({
            price: p,
            color,
            lineWidth: 1,
            lineStyle: style,
            axisLabelVisible: false,
        });
    }

    function _updateOrAddLine(entry, key, price, color, style) {
        if (!price || !isFinite(price)) return;
        if (entry[key]) {
            entry[key].applyOptions({ price });
        } else {
            _addLine(entry, key, price, color, style);
        }
    }

    // ── Fetch klines utility ─────────────────────────────────

    async function fetchAndRender(chartId, symbol, interval, limit) {
        interval = interval || '5m';
        limit = limit || 24;
        try {
            const resp = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=${limit}&indicators=`);
            const data = await resp.json();
            const klines = (data.klines || []).map(k => ({
                time: k.open_time,
                open: k.open,
                high: k.high,
                low: k.low,
                close: k.close,
            }));
            setData(chartId, klines);
        } catch (e) {
            console.error('MiniTradeChart: fetch failed', symbol, e);
        }
    }

    return {
        create,
        setData,
        updateLevels,
        appendCandle,
        addLabel,
        addTiming,
        destroy,
        destroyExcept,
        destroyAll,
        getChartIds,
        getEntry,
        fetchAndRender,
    };
})();
