const Analysis = (() => {
    let _data = null;
    let _newsData = null;
    let _currentSymbol = null;

    async function load() {
        try {
            const [analysisResp, newsResp] = await Promise.all([
                fetch('/api/analysis'),
                fetch('/api/news'),
            ]);
            if (analysisResp.ok) {
                _data = await analysisResp.json();
                _populateSymbols();
            }
            if (newsResp.ok) {
                _newsData = await newsResp.json();
            }
            render();
        } catch (e) {
            console.error('Analysis load failed', e);
            document.getElementById('analysis-empty').classList.remove('hidden');
        }
    }

    function render() {
        if (!_data || !_data.analyses || !_data.analyses.length) {
            document.getElementById('analysis-empty').classList.remove('hidden');
            document.getElementById('analysis-bias').innerHTML = '';
            document.getElementById('analysis-levels').innerHTML = '';
            document.getElementById('analysis-macro').innerHTML = '';
            document.getElementById('analysis-alerts').innerHTML = '';
            return;
        }
        document.getElementById('analysis-empty').classList.add('hidden');

        const analysis = _currentSymbol
            ? _data.analyses.find(a => a.symbol === _currentSymbol)
            : _data.analyses[0];

        if (!analysis) return;
        _currentSymbol = analysis.symbol;

        _renderFreshness();
        _renderBias(analysis);
        _renderLevels(analysis);
        _renderMacro(_data.macro);
        _renderAlerts(analysis);
        _renderNews();
    }

    function _populateSymbols() {
        const sel = document.getElementById('analysis-symbol');
        if (!sel || !_data || !_data.analyses) return;

        const current = sel.value;
        sel.innerHTML = '';
        _data.analyses.forEach(a => {
            const opt = document.createElement('option');
            opt.value = a.symbol;
            opt.textContent = a.symbol;
            sel.appendChild(opt);
        });

        if (current && _data.analyses.some(a => a.symbol === current)) {
            sel.value = current;
            _currentSymbol = current;
        } else {
            const btc = _data.analyses.find(a => a.symbol.startsWith('BTC'));
            _currentSymbol = btc ? btc.symbol : _data.analyses[0].symbol;
            sel.value = _currentSymbol;
        }

        sel.onchange = () => {
            _currentSymbol = sel.value;
            render();
        };
    }

    function _renderFreshness() {
        const el = document.getElementById('analysis-freshness');
        if (!el || !_data) return;
        if (_data.is_stale) {
            el.innerHTML = '<span class="stale-badge">STALE</span>';
        } else if (_data.computed_at) {
            const ago = Utils.timeAgo(_data.computed_at);
            el.textContent = `Mis a jour ${ago}`;
        }
    }

    // ── Block 1: Bias ──────────────────────────────────────

    function _renderBias(analysis) {
        const el = document.getElementById('analysis-bias');
        const b = analysis.bias;
        const colorClass = b.direction === 'LONG' ? 'pnl-positive'
            : b.direction === 'SHORT' ? 'pnl-negative'
            : 'text-gray-400';
        const arrow = b.direction === 'LONG' ? '\u25B2'
            : b.direction === 'SHORT' ? '\u25BC'
            : '\u2014';
        const barColor = b.direction === 'LONG' ? '#22c55e'
            : b.direction === 'SHORT' ? '#ef4444'
            : '#6b7280';

        const justificationHtml = (b.justification || '').split('\n').map(line => {
            if (!line.trim()) return '';
            const isConclusion = line.startsWith('Conclusion');
            const cls = isConclusion ? 'text-sm font-semibold text-gray-200 mt-2' : 'text-sm text-gray-400';
            return `<div class="${cls}">${Utils.escHtml(line)}</div>`;
        }).join('');

        el.innerHTML = `
        <div class="card">
            <div class="metric-label mb-2">Biais du jour &mdash; ${analysis.symbol}</div>
            <div class="flex items-center gap-4 mb-3">
                <span class="bias-direction ${colorClass}">${arrow}</span>
                <div>
                    <div class="${colorClass} text-2xl font-bold">${b.direction}</div>
                    <div class="text-sm text-gray-400">Confiance: ${b.confidence}%</div>
                </div>
                <div class="flex-1">
                    <div class="confidence-bar">
                        <div class="confidence-fill" style="width:${b.confidence}%;background:${barColor}"></div>
                    </div>
                </div>
            </div>
            <div class="space-y-1">${justificationHtml}</div>
        </div>`;
    }

    // ── Block 2: Key levels ────────────────────────────────

    function _renderLevels(analysis) {
        const el = document.getElementById('analysis-levels');
        const levels = analysis.key_levels || [];
        const current = analysis.current_price;

        if (!levels.length) {
            el.innerHTML = '<div class="card"><div class="metric-label">Niveaux cles</div><div class="text-sm text-gray-500 mt-2">Aucune donnee</div></div>';
            return;
        }

        let currentInserted = false;
        let rows = '';

        for (const lvl of levels) {
            const price = parseFloat(lvl.price);
            const cprice = parseFloat(current);

            // Insert current price row at the right position
            if (!currentInserted && current && price < cprice) {
                rows += `
                <div class="level-current">
                    <div class="flex justify-between">
                        <span class="text-blue-400 font-bold text-xs uppercase">Prix actuel</span>
                        <span class="text-blue-400 font-bold tabular-nums">${Utils.fmtPrice(current)}</span>
                    </div>
                </div>`;
                currentInserted = true;
            }

            const abovePrice = current && parseFloat(lvl.price) >= parseFloat(current);
            const typeColor = abovePrice ? 'text-green-400' : 'text-red-400';
            const label = lvl.label || lvl.type;
            const dist = lvl.distance_pct
                ? `<span class="text-xs text-gray-500">${parseFloat(lvl.distance_pct) >= 0 ? '+' : ''}${lvl.distance_pct}%</span>`
                : '';

            rows += `
            <div class="level-row">
                <span class="${typeColor} text-xs font-semibold" style="min-width:5.5rem">${label}</span>
                <span class="text-gray-300 tabular-nums">${Utils.fmtPrice(lvl.price)}</span>
                ${dist}
            </div>`;
        }

        // If current price is below all levels
        if (!currentInserted && current) {
            rows += `
            <div class="level-current">
                <div class="flex justify-between">
                    <span class="text-blue-400 font-bold text-xs uppercase">Prix actuel</span>
                    <span class="text-blue-400 font-bold tabular-nums">${Utils.fmtPrice(current)}</span>
                </div>
            </div>`;
        }

        el.innerHTML = `
        <div class="card">
            <div class="metric-label mb-2">Niveaux cles</div>
            ${rows}
        </div>`;
    }

    // ── Block 3: Macro ─────────────────────────────────────

    function _renderMacro(macro) {
        const el = document.getElementById('analysis-macro');
        if (!macro || !macro.indicators || !Object.keys(macro.indicators).length) {
            el.innerHTML = '<div class="card"><div class="metric-label">Macro</div><div class="text-sm text-gray-500 mt-2">Aucune donnee</div></div>';
            return;
        }

        const displayOrder = ['dxy', 'vix', 'nasdaq', 'gold', 'us10y', 'spread', 'oil', 'usdjpy'];
        const names = {
            dxy: 'DXY', vix: 'VIX', nasdaq: 'Nasdaq', gold: 'Gold',
            us10y: 'US 10Y', spread: 'Spread 10-5Y',
            oil: 'Petrole', usdjpy: 'USD/JPY',
        };
        const cryptoImpact = {
            dxy: 'inverse', vix: 'inverse', nasdaq: 'direct', gold: 'inverse',
            us10y: 'inverse', spread: 'spread', oil: 'inverse', usdjpy: 'direct',
        };

        let cards = '';
        for (const key of displayOrder) {
            const ind = macro.indicators[key];
            if (!ind) continue;
            const label = names[key] || key;

            const trend = ind.trend;
            const isInverted = trend === 'inverted';
            const trendClass = isInverted ? 'macro-trend-down' : `macro-trend-${trend}`;
            const arrow = isInverted ? '\u25BC' : trend === 'up' ? '\u25B2' : trend === 'down' ? '\u25BC' : '\u2014';
            const change = parseFloat(ind.change_pct || 0);
            const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;

            // Color based on crypto impact (use change_pct sign, not trend which has a 0.3% dead zone)
            const impact = cryptoImpact[key];
            let impactColor = 'text-gray-400';
            if (impact === 'spread') {
                const spreadVal = parseFloat(ind.value || 0);
                impactColor = spreadVal < 0 ? 'pnl-negative' : spreadVal > 0.5 ? 'pnl-positive' : 'text-yellow-400';
            } else if (impact === 'inverse') {
                impactColor = change < 0 ? 'pnl-positive' : change > 0 ? 'pnl-negative' : 'text-gray-400';
            } else if (impact === 'direct') {
                impactColor = change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : 'text-gray-400';
            }

            const displayValue = _fmtMacroValue(key, ind.value);

            cards += `
            <div class="macro-card">
                <div class="text-xs text-gray-500 font-semibold mb-1">${label}</div>
                <div class="text-lg font-bold tabular-nums">${displayValue}</div>
                <div class="${trendClass} text-sm font-semibold">
                    ${arrow} <span class="tabular-nums">${changeStr}</span>
                </div>
                <div class="${impactColor} text-xs mt-0.5">crypto ${impactColor.includes('positive') ? '\u25B2' : impactColor.includes('negative') ? '\u25BC' : '\u2014'}</div>
            </div>`;
        }

        const staleHtml = macro.is_stale ? '<span class="stale-badge ml-2">STALE</span>' : '';

        el.innerHTML = `
        <div class="card">
            <div class="metric-label mb-2">Macro${staleHtml}</div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">${cards}</div>
        </div>`;
    }

    // ── Block 4: Alerts ────────────────────────────────────

    function _renderAlerts(analysis) {
        const el = document.getElementById('analysis-alerts');
        const alerts = analysis.alerts || [];

        if (!alerts.length) {
            el.innerHTML = '';
            return;
        }

        const chips = alerts.map(a => {
            const cls = `alert-chip alert-chip-${a.type}`;
            const icon = a.type === 'conflict' ? '\u26A0'
                : a.type === 'aligned' ? '\u2705'
                : a.type === 'whale' ? '\uD83D\uDC0B'
                : '\u2139';
            return `<span class="${cls}" title="${Utils.escHtml(a.message)}">${icon} ${Utils.escHtml(a.message)}</span>`;
        }).join('');

        el.innerHTML = `
        <div class="card">
            <div class="metric-label mb-2">Alertes</div>
            <div class="flex flex-wrap gap-1.5">${chips}</div>
        </div>`;
    }

    // ── Block 5: News ─────────────────────────────────────

    const CRYPTO_CATEGORIES = new Set(['Markets', 'crypto']);
    const MACRO_CATEGORIES = new Set(['macro']);

    function _classifyItem(item) {
        const cat = item.category || '';
        if (CRYPTO_CATEGORIES.has(cat)) return 'crypto';
        if (MACRO_CATEGORIES.has(cat)) return 'macro';
        // Google feeds: use feed name
        if (item.feed === 'google_crypto') return 'crypto';
        if (item.feed === 'google_macro') return 'macro';
        // CoinDesk non-Markets (Opinion, Policy, Business, Finance, Tech...)
        return 'general';
    }

    function _renderNewsItem(item) {
        const title = Utils.escHtml(item.title_fr || item.title || '');
        const ago = item.published_at ? Utils.timeAgo(item.published_at) : '';
        const sourceLabel = Utils.escHtml(item.source || '');
        const href = Utils.safeHref(item.link);

        return `
        <a href="${href}" target="_blank" rel="noopener" class="news-item">
            <div class="text-sm text-gray-200 font-medium leading-snug">${title}</div>
            <div class="flex items-center gap-2 mt-1">
                <span class="text-xs text-gray-600">${sourceLabel}</span>
                <span class="text-xs text-gray-600">${ago}</span>
            </div>
        </a>`;
    }

    function _renderNewsColumn(title, items, catClass) {
        const html = items.map(_renderNewsItem).join('');
        const count = items.length;
        return `
        <div class="card news-column">
            <div class="flex items-center gap-2 mb-3">
                <span class="news-category ${catClass}">${title}</span>
                <span class="text-xs text-gray-600">${count}</span>
            </div>
            <div class="news-list">${html || '<div class="text-xs text-gray-600 py-2">Aucune news</div>'}</div>
        </div>`;
    }

    function _renderNews() {
        const el = document.getElementById('analysis-news');
        if (!_newsData || !_newsData.items || !_newsData.items.length) {
            el.innerHTML = '';
            return;
        }

        const staleHtml = _newsData.is_stale ? '<span class="stale-badge ml-2">STALE</span>' : '';
        const freshnessHtml = _newsData.fetched_at
            ? `<span class="text-xs text-gray-500">${Utils.timeAgo(_newsData.fetched_at)}</span>`
            : '';

        const crypto = [];
        const macro = [];
        const general = [];

        for (const item of _newsData.items) {
            const col = _classifyItem(item);
            if (col === 'crypto') crypto.push(item);
            else if (col === 'macro') macro.push(item);
            else general.push(item);
        }

        el.innerHTML = `
        <div class="flex items-center gap-2 mb-3">
            <div class="metric-label">News${staleHtml}</div>
            ${freshnessHtml}
        </div>
        <div class="news-grid">
            ${_renderNewsColumn('Crypto', crypto, 'news-cat-crypto')}
            ${_renderNewsColumn('Macro', macro, 'news-cat-macro')}
            ${_renderNewsColumn('General', general, 'news-cat-other')}
        </div>`;
    }

    // ── Helpers ─────────────────────────────────────────────

    function _fmtMacroValue(key, val) {
        const n = parseFloat(val);
        if (isNaN(n)) return val;
        if (key === 'us10y') return n.toFixed(2) + '%';
        if (key === 'spread') return (n >= 0 ? '+' : '') + n.toFixed(3) + '%';
        if (n >= 1000) return n.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        return n.toFixed(2);
    }

    // WS updates
    WS.on('analysis_update', (data) => {
        _data = data;
        render();
    });

    WS.on('news_update', (data) => {
        _newsData = data;
        _renderNews();
    });

    const _throttledRenderLevels = Utils.throttle((analysis) => _renderLevels(analysis), 300);

    WS.on('price_update', (data) => {
        if (!_data || !_data.analyses || !_currentSymbol) return;
        if (data.symbol !== _currentSymbol) return;
        const analysis = _data.analyses.find(a => a.symbol === _currentSymbol);
        if (!analysis) return;
        analysis.current_price = data.price;
        const cp = parseFloat(data.price);
        if (cp && analysis.key_levels) {
            for (const lvl of analysis.key_levels) {
                const p = parseFloat(lvl.price);
                lvl.distance_pct = (((p - cp) / cp) * 100).toFixed(2);
            }
        }
        _throttledRenderLevels(analysis);
    });

    return { load };
})();
