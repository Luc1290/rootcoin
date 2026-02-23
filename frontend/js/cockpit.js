const Cockpit = (() => {
    let _positions = [];
    let _analysis = null;
    let _lastFills = [];
    let _portfolioTotal = null;
    const MAX_FILLS = 3;

    async function load() {
        try {
            const [balResp, anaResp] = await Promise.all([
                fetch('/api/balances'),
                fetch('/api/analysis'),
            ]);
            if (balResp.ok) {
                const balances = await balResp.json();
                _portfolioTotal = _calcPortfolioTotal(balances);
            }
            if (anaResp.ok) {
                _analysis = await anaResp.json();
            }
            render();
        } catch (e) {
            console.error('Cockpit load failed', e);
        }
    }

    function render() {
        _renderPortfolio();
        _renderPositions();
        _renderBias();
        _renderLastFill();
        _renderWhale();
    }

    function _renderPortfolio() {
        const el = document.getElementById('cockpit-portfolio');
        const totalPnl = _positions.reduce((s, p) => s + (parseFloat(p.pnl_usd) || 0), 0);
        const pnlClass = totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const pnlSign = totalPnl >= 0 ? '+' : '';
        const portfolioStr = _portfolioTotal !== null ? '$' + _portfolioTotal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--';

        el.innerHTML = `
        <div class="cockpit-card">
            <div class="flex items-center justify-between">
                <span class="text-sm text-gray-400">Portfolio</span>
                <span class="text-lg font-bold tabular-nums">${portfolioStr}</span>
            </div>
            <div class="flex items-center justify-between mt-1">
                <span class="text-sm text-gray-400">PnL ouvert</span>
                <span class="text-base font-bold tabular-nums ${pnlClass}">${pnlSign}$${Math.abs(totalPnl).toFixed(2)}</span>
            </div>
        </div>`;
    }

    function _renderPositions() {
        const el = document.getElementById('cockpit-positions');
        if (!_positions.length) {
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Aucune position ouverte</span></div>';
            return;
        }

        const rows = _positions.map(p => {
            const pnl = parseFloat(p.pnl_pct) || 0;
            const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
            const pnlSign = pnl >= 0 ? '+' : '';
            const sideClass = p.side === 'LONG' ? 'side-long' : 'side-short';
            const symbol = p.symbol.replace('USDC', '');
            const price = _fmtPrice(p.current_price);
            const pnlUsd = parseFloat(p.pnl_usd) || 0;
            const pnlUsdSign = pnlUsd >= 0 ? '+' : '';
            return `<div class="cockpit-position" onclick="App.switchTab('positions')">
                <div class="flex items-center gap-2 flex-1 min-w-0">
                    <span class="font-bold text-sm">${symbol}</span>
                    <span class="cockpit-side ${sideClass}">${p.side}</span>
                </div>
                <div class="flex items-center gap-3">
                    <span class="text-xs text-gray-400 tabular-nums">${price}</span>
                    <span class="text-sm font-bold tabular-nums ${pnlClass}">${pnlSign}${pnl.toFixed(1)}%</span>
                    <span class="text-xs tabular-nums ${pnlClass}">${pnlUsdSign}$${Math.abs(pnlUsd).toFixed(0)}</span>
                </div>
            </div>`;
        }).join('');

        el.innerHTML = `<div class="cockpit-card">${rows}</div>`;
    }

    function _renderBias() {
        const el = document.getElementById('cockpit-bias');
        if (!_analysis || !_analysis.analyses || !_analysis.analyses.length) {
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Analyse en attente...</span></div>';
            return;
        }

        const rows = _analysis.analyses.map(a => {
            if (!a.bias) return '';
            const dir = a.bias.direction;
            const conf = a.bias.confidence || 0;
            const symbol = a.symbol.replace('USDC', '');
            let barColor = '#6b7280';
            let dirClass = '';
            if (dir === 'LONG') { barColor = '#22c55e'; dirClass = 'pnl-positive'; }
            else if (dir === 'SHORT') { barColor = '#ef4444'; dirClass = 'pnl-negative'; }
            return `<div class="cockpit-bias-row" onclick="App.switchTab('analysis')">
                <div class="flex items-center justify-between mb-1">
                    <span class="text-xs font-bold">${symbol}</span>
                    <span class="text-xs font-semibold ${dirClass}">${dir} ${conf}%</span>
                </div>
                <div class="cockpit-bias-track"><div class="cockpit-bias-fill" style="width:${conf}%;background:${barColor}"></div></div>
            </div>`;
        }).join('');

        el.innerHTML = rows ? `<div class="cockpit-card">${rows}</div>` : '';
    }

    function _renderLastFill() {
        const el = document.getElementById('cockpit-lastfill');
        if (!_lastFills.length) {
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Aucun fill recent</span></div>';
            return;
        }

        const rows = _lastFills.slice(0, MAX_FILLS).map(f => {
            const symbol = f.symbol.replace('USDC', '');
            const sideClass = f.side === 'BUY' ? 'side-long' : 'side-short';
            const price = _fmtPrice(f.price);
            return `<div class="flex items-center justify-between py-1.5">
                <div class="flex items-center gap-2">
                    <span class="text-xs font-bold">${symbol}</span>
                    <span class="cockpit-side ${sideClass}">${f.side}</span>
                    <span class="text-xs text-gray-400">${f.filled_qty}</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-xs tabular-nums text-gray-300">${price}</span>
                    <span class="text-xs ${f.status === 'FILLED' ? 'pnl-positive' : 'text-gray-500'}">${f.status}</span>
                </div>
            </div>`;
        }).join('');

        el.innerHTML = `<div class="cockpit-card"><div class="text-xs text-gray-500 mb-1">Last fills</div>${rows}</div>`;
    }

    function _renderWhale() {
        const el = document.getElementById('cockpit-whale');
        const alerts = _analysis && _analysis.whale_alerts ? _analysis.whale_alerts : [];
        if (!alerts.length) {
            el.innerHTML = '<div class="cockpit-card"><span class="text-xs text-gray-500">Aucune alerte whale</span></div>';
            return;
        }

        const latest = alerts[alerts.length - 1];
        const symbol = latest.symbol.replace('USDC', '');
        const sideClass = latest.side === 'BUY' ? 'side-long' : 'side-short';
        const qty = _fmtQuoteQty(latest.quote_qty);
        const ago = _timeAgo(latest.timestamp);

        el.innerHTML = `
        <div class="cockpit-card cockpit-whale-card">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <span class="text-xs">&#x1F40B;</span>
                    <span class="text-xs font-bold">${symbol}</span>
                    <span class="cockpit-side ${sideClass}">${latest.side}</span>
                    <span class="text-xs font-semibold text-gray-200">${qty}</span>
                </div>
                <span class="text-xs text-gray-500">${ago}</span>
            </div>
        </div>`;
    }

    // ── Helpers ──

    function _calcPortfolioTotal(balances) {
        const stables = new Set(['USDC', 'USDT', 'BUSD', 'FDUSD', 'DAI', 'TUSD']);
        let total = 0;
        const grouped = {};
        for (const b of balances) {
            const a = b.asset;
            if (!grouped[a]) grouped[a] = { net: 0, usd: 0, hasUsd: false };
            grouped[a].net += parseFloat(b.net) || 0;
            if (b.usd_value) {
                grouped[a].usd += parseFloat(b.usd_value) || 0;
                grouped[a].hasUsd = true;
            }
        }
        for (const [asset, g] of Object.entries(grouped)) {
            if (g.hasUsd) total += g.usd;
            else if (stables.has(asset)) total += g.net;
        }
        return total;
    }

    function _fmtPrice(p) {
        const n = parseFloat(p);
        if (!n) return '--';
        if (n >= 1000) return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 });
        if (n >= 1) return '$' + n.toFixed(2);
        return '$' + n.toPrecision(4);
    }

    function _fmtQuoteQty(q) {
        const n = parseFloat(q);
        if (!n) return '--';
        if (n >= 1000000) return '$' + (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return '$' + (n / 1000).toFixed(0) + 'K';
        return '$' + n.toFixed(0);
    }

    function _timeAgo(isoStr) {
        if (!isoStr) return '';
        const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
        if (diff < 60) return diff + 's';
        if (diff < 3600) return Math.floor(diff / 60) + 'min';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h';
        return Math.floor(diff / 86400) + 'd';
    }

    // ── WS real-time updates ──

    WS.on('positions_snapshot', (data) => {
        _positions = data || [];
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderPortfolio();
        _renderPositions();
    });

    WS.on('analysis_update', (data) => {
        _analysis = data;
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderBias();
        _renderWhale();
    });

    WS.on('order_update', (data) => {
        if (!data || !data.symbol) return;
        _lastFills.unshift(data);
        if (_lastFills.length > 10) _lastFills.length = 10;
        if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
        _renderLastFill();
    });

    WS.on('balance_update', () => {
        fetch('/api/balances').then(r => r.json()).then(balances => {
            _portfolioTotal = _calcPortfolioTotal(balances);
            if (document.getElementById('view-cockpit').classList.contains('hidden')) return;
            _renderPortfolio();
        }).catch(() => {});
    });

    return { load };
})();
