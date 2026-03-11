const MiniTradeChart = (() => {
    const _charts = {};
    let _idCounter = 0;

    const C = {
        upCandle: '#22c55e',
        downCandle: '#ef4444',
        entry: '#3b82f6',
        sl: '#ef4444',
        tp: '#22c55e',
        retest: '#f59e0b',
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
            rightPriceScale: { borderColor: 'transparent', textColor: '#9ca3af', scaleMargins: { top: 0.1, bottom: 0.1 }, autoScale: true },
            timeScale: { visible: true, fixLeftEdge: true, fixRightEdge: true, borderColor: 'transparent', timeVisible: true, secondsVisible: false },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            handleScroll: false,
            handleScale: false,
        });

        // Store price levels so autoscale includes Entry/SL/TP lines
        const priceLevels = {
            entry: opts.entryPrice ? parseFloat(opts.entryPrice) : 0,
            sl: opts.slPrice ? parseFloat(opts.slPrice) : 0,
            tp: opts.tpPrice ? parseFloat(opts.tpPrice) : 0,
            retest: opts.retestPrice ? parseFloat(opts.retestPrice) : 0,
        };

        const series = chart.addAreaSeries({
            lineColor: '#d1d5db',
            topColor: 'rgba(209,213,219,0.12)',
            bottomColor: 'rgba(209,213,219,0)',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            autoscaleInfoProvider: (original) => {
                const res = original();
                if (!res || !res.priceRange) return res;
                const prices = [priceLevels.entry, priceLevels.sl, priceLevels.tp, priceLevels.retest]
                    .filter(p => p > 0 && isFinite(p));
                for (const p of prices) {
                    res.priceRange.minValue = Math.min(res.priceRange.minValue, p);
                    res.priceRange.maxValue = Math.max(res.priceRange.maxValue, p);
                }
                return res;
            },
        });

        const ro = new ResizeObserver(entries => {
            const w = entries[0].contentRect.width;
            if (w > 0) chart.applyOptions({ width: w });
        });
        ro.observe(el);

        const showLabels = opts.showLineLabels !== false;

        const entry = {
            id,
            chart,
            series,
            ro,
            el,
            symbol: opts.symbol || '',
            showLineLabels: showLabels,
            entryLabel: opts.entryLabel || 'Entry',
            entryLine: null,
            slLine: null,
            tpLine: null,
            retestLine: null,
            retestLabel: opts.retestLabel || 'Retest',
            labelEl: null,
            timingEl: null,
            lastTs: 0,
            pendingMarker: null,
            priceLevels,
        };

        if (opts.entryPrice) _addLine(entry, 'entryLine', opts.entryPrice, C.entry, LightweightCharts.LineStyle.Solid);
        if (opts.slPrice) _addLine(entry, 'slLine', opts.slPrice, C.sl, LightweightCharts.LineStyle.Dashed);
        if (opts.tpPrice) _addLine(entry, 'tpLine', opts.tpPrice, C.tp, LightweightCharts.LineStyle.Dashed);
        if (opts.retestPrice) _addLine(entry, 'retestLine', opts.retestPrice, C.retest, LightweightCharts.LineStyle.Dotted);

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

        // Apply pending marker (arrow at specific timestamp)
        if (entry.pendingMarker) {
            _applyMarker(entry, data);
        }

        entry.chart.timeScale().fitContent();

        // Recolor entry line + marker if PnL already known
        _updatePnlVisuals(entry);

        // Reposition line labels now that data + scale exist
        requestAnimationFrame(() => {
            if (entry.entryLine) _positionLineLabel(entry, 'entryLine', entry.entryLine.options().price);
            if (entry.slLine) _positionLineLabel(entry, 'slLine', entry.slLine.options().price);
            if (entry.tpLine) _positionLineLabel(entry, 'tpLine', entry.tpLine.options().price);
            if (entry.retestLine) _positionLineLabel(entry, 'retestLine', entry.retestLine.options().price);
        });
    }

    function updateLevels(chartId, levels) {
        const entry = _charts[chartId];
        if (!entry) return;

        if (levels.entryPrice) {
            entry.priceLevels.entry = parseFloat(levels.entryPrice);
            _updateOrAddLine(entry, 'entryLine', parseFloat(levels.entryPrice), C.entry, LightweightCharts.LineStyle.Solid);
        }
        if (levels.slPrice) {
            entry.priceLevels.sl = parseFloat(levels.slPrice);
            _updateOrAddLine(entry, 'slLine', parseFloat(levels.slPrice), C.sl, LightweightCharts.LineStyle.Dashed);
        }
        if (levels.tpPrice) {
            entry.priceLevels.tp = parseFloat(levels.tpPrice);
            _updateOrAddLine(entry, 'tpLine', parseFloat(levels.tpPrice), C.tp, LightweightCharts.LineStyle.Dashed);
        }
    }

    function appendCandle(chartId, candle) {
        const entry = _charts[chartId];
        if (!entry) return;

        let t = typeof candle.time === 'number' ? candle.time : Math.floor(new Date(candle.time + 'Z').getTime() / 1000);
        if (t > 1e10) t = Math.floor(t / 1000); // ms → s
        const v = parseFloat(candle.close);
        if (!isFinite(t) || !isFinite(v) || v <= 0) return;

        try {
            entry.series.update({ time: t, value: v });
            if (t > entry.lastTs) entry.lastTs = t;
        } catch (_) { /* chart not ready */ }
    }

    function _updatePnlVisuals(entry) {
        if (entry._pnlWinning == null) return;
        const winning = entry._pnlWinning;
        const color = winning ? 'rgba(34, 197, 94, 0.7)' : 'rgba(239, 68, 68, 0.7)';

        if (entry.entryLine) {
            entry.entryLine.applyOptions({ color });
        }

        if (entry.pendingMarker) {
            const markerColor = winning ? '#22c55e' : '#ef4444';
            const markers = entry.series.markers ? entry.series.markers() : [];
            if (markers.length) {
                entry.series.setMarkers([{
                    ...markers[0],
                    color: markerColor,
                }]);
            }
        }
    }

    function updatePnl(chartId, pnlUsd) {
        const entry = _charts[chartId];
        if (!entry) return;
        const winning = pnlUsd >= 0;
        if (entry._pnlWinning === winning) return;
        entry._pnlWinning = winning;
        _updatePnlVisuals(entry);
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

    function addMarker(chartId, timestamp, direction, price, size) {
        const entry = _charts[chartId];
        if (!entry) return;
        let ts = timestamp;
        if (typeof ts === 'string') {
            const hastz = ts.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(ts);
            ts = Math.floor(new Date(hastz ? ts : ts + 'Z').getTime() / 1000);
        }
        else if (ts > 1e12) ts = Math.floor(ts / 1000);
        const p = price ? parseFloat(price) : null;
        entry.pendingMarker = { time: ts, direction: direction || 'LONG', price: p, size: size };
        entry.side = direction || 'LONG';
    }

    function _applyMarker(entry, data) {
        const m = entry.pendingMarker;
        if (!m || !data.length) return;

        // Clean up previous hidden marker series if any
        if (entry.markerSeries) {
            entry.chart.removeSeries(entry.markerSeries);
            entry.markerSeries = null;
        }

        // Find the closest data point to the marker timestamp
        let closest = data[0];
        let bestDiff = Math.abs(data[0].time - m.time);
        for (const d of data) {
            const diff = Math.abs(d.time - m.time);
            if (diff < bestDiff) { bestDiff = diff; closest = d; }
        }

        const isLong = m.direction === 'LONG';
        entry.series.setMarkers([{
            time: closest.time,
            position: isLong ? 'belowBar' : 'aboveBar',
            color: isLong ? '#22c55e' : '#ef4444',
            shape: isLong ? 'arrowUp' : 'arrowDown',
            size: m.size != null ? m.size : 1,
        }]);
    }

    function destroy(chartId) {
        const entry = _charts[chartId];
        if (!entry) return;
        if (entry.labelEl) entry.labelEl.remove();
        if (entry.timingEl) entry.timingEl.remove();
        if (entry.entryLine_label) entry.entryLine_label.remove();
        if (entry.slLine_label) entry.slLine_label.remove();
        if (entry.tpLine_label) entry.tpLine_label.remove();
        if (entry.retestLine_label) entry.retestLine_label.remove();
        if (entry.markerSeries) entry.chart.removeSeries(entry.markerSeries);
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

    const _lineLabels = {
        entryLine: 'Entry',
        slLine: 'SL',
        tpLine: 'TP',
        retestLine: null, // set dynamically via _retestLabel
    };

    function _addLine(entry, key, price, color, style) {
        const p = parseFloat(price);
        if (!p || !isFinite(p)) return;
        entry[key] = entry.series.createPriceLine({
            price: p,
            color,
            lineWidth: 1,
            lineStyle: style,
            axisLabelVisible: key !== 'retestLine',
            title: '',
        });
        if (entry.showLineLabels && key !== 'retestLine') _addLineLabel(entry, key, p, color);
    }

    function _addLineLabel(entry, key, price, color) {
        const labelKey = key + '_label';
        if (entry[labelKey]) entry[labelKey].remove();

        const el = document.createElement('div');
        el.className = 'mini-chart-line-label';
        el.style.color = color;
        const label = key === 'retestLine' ? (entry.retestLabel || 'Retest')
            : key === 'entryLine' ? (entry.entryLabel || 'Entry')
            : (_lineLabels[key] || '');
        const priceStr = Utils && Utils.fmtPriceCompact ? Utils.fmtPriceCompact(price) : price.toString();
        el.textContent = `${label} ${priceStr}`;
        entry.el.style.position = 'relative';
        entry.el.appendChild(el);
        entry[labelKey] = el;

        // Position after chart renders
        requestAnimationFrame(() => _positionLineLabel(entry, key, price));
    }

    function _positionLineLabel(entry, key, price) {
        const labelEl = entry[key + '_label'];
        if (!labelEl) return;
        try {
            const y = entry.series.priceToCoordinate(price);
            if (y !== null && isFinite(y)) {
                // Place label above the line (offset -14px so text sits on top, not crossed)
                labelEl.style.top = (y - 14) + 'px';
            } else {
                labelEl.style.top = key === 'tpLine' ? '4px' : key === 'slLine' ? 'calc(100% - 16px)' : '50%';
            }
        } catch {
            labelEl.style.top = '50%';
        }
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
        updatePnl,
        addLabel,
        addTiming,
        addMarker,
        destroy,
        destroyExcept,
        destroyAll,
        getChartIds,
        getEntry,
        fetchAndRender,
    };
})();
