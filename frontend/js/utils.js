const Utils = (() => {
    function timeAgo(isoStr) {
        if (!isoStr) return '';
        const diffS = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
        if (diffS < 60) return 'il y a ' + diffS + 's';
        if (diffS < 3600) return 'il y a ' + Math.floor(diffS / 60) + ' min';
        if (diffS < 86400) return 'il y a ' + Math.floor(diffS / 3600) + 'h';
        return 'il y a ' + Math.floor(diffS / 86400) + 'j';
    }

    function timeAgoShort(isoStr) {
        if (!isoStr) return '';
        const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
        if (diff < 60) return diff + 's';
        if (diff < 3600) return Math.floor(diff / 60) + 'min';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h';
        return Math.floor(diff / 86400) + 'd';
    }

    function fmtPrice(val) {
        const n = parseFloat(val);
        if (isNaN(n)) return val;
        if (n >= 1000) return n.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        if (n >= 1) return n.toFixed(4);
        return n.toFixed(6);
    }

    function throttleRAF(fn) {
        let queued = false;
        let lastArgs = null;
        return function (...args) {
            lastArgs = args;
            if (queued) return;
            queued = true;
            requestAnimationFrame(() => {
                queued = false;
                fn.apply(this, lastArgs);
            });
        };
    }

    function throttle(fn, ms) {
        let timer = null;
        let lastArgs = null;
        return function (...args) {
            lastArgs = args;
            if (timer) return;
            timer = setTimeout(() => {
                timer = null;
                fn.apply(this, lastArgs);
            }, ms);
        };
    }

    function fmtPriceCompact(val) {
        const n = parseFloat(val);
        if (!n) return '--';
        if (n >= 1000) return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 });
        if (n >= 1) return '$' + n.toFixed(2);
        return '$' + n.toPrecision(4);
    }

    function escHtml(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function safeHref(url) {
        if (!url) return '#';
        try {
            const u = new URL(url);
            if (u.protocol === 'https:' || u.protocol === 'http:') return url;
        } catch {}
        return '#';
    }

    return { timeAgo, timeAgoShort, fmtPrice, fmtPriceCompact, throttleRAF, throttle, escHtml, safeHref };
})();

const BalanceStore = (() => {
    let _balances = [];
    let _total = null;
    const _listeners = [];
    const _stables = new Set(['USDC', 'USDT', 'BUSD', 'FDUSD', 'DAI', 'TUSD']);
    const _priceMap = {};
    let _priceTimer = null;

    function _calcTotal() {
        const grouped = {};
        for (const b of _balances) {
            const a = b.asset;
            if (!grouped[a]) grouped[a] = { net: 0, usd: 0, hasUsd: false };
            grouped[a].net += parseFloat(b.net) || 0;
            if (b.usd_value) {
                grouped[a].usd += parseFloat(b.usd_value) || 0;
                grouped[a].hasUsd = true;
            }
        }
        let total = 0;
        for (const [asset, g] of Object.entries(grouped)) {
            if (g.hasUsd) total += g.usd;
            else if (_stables.has(asset)) total += g.net;
        }
        return total;
    }

    function _notify() {
        for (const fn of _listeners) fn();
    }

    async function load() {
        try {
            const resp = await fetch('/api/balances');
            if (resp.ok) {
                _balances = await resp.json();
                _total = _calcTotal();
                _notify();
            }
        } catch (e) {
            console.error('BalanceStore fetch failed', e);
        }
    }

    function get() { return _balances; }
    function getTotal() { return _total; }
    function onChange(fn) { _listeners.push(fn); }

    WS.on('balance_update', (data) => {
        if (!_balances.length || !data.length) return;
        for (const upd of data) {
            const existing = _balances.find(b => b.asset === upd.asset && b.wallet_type === 'SPOT');
            if (existing) {
                existing.free = upd.free;
                existing.locked = upd.locked;
                existing.net = String(parseFloat(upd.free) + parseFloat(upd.locked));
                if (_stables.has(upd.asset)) existing.usd_value = existing.net;
            } else if (parseFloat(upd.free) + parseFloat(upd.locked) > 0) {
                const net = String(parseFloat(upd.free) + parseFloat(upd.locked));
                _balances.push({
                    asset: upd.asset, free: upd.free, locked: upd.locked,
                    borrowed: '0', interest: '0', net,
                    wallet_type: 'SPOT',
                    usd_value: _stables.has(upd.asset) ? net : null,
                    snapshot_at: new Date().toISOString(),
                });
            }
        }
        _balances = _balances.filter(b => parseFloat(b.net) !== 0);
        _total = _calcTotal();
        _notify();
    });

    WS.on('price_update', (data) => {
        if (!data.symbol || !data.price) return;
        const price = parseFloat(data.price);
        if (!price) return;
        for (const s of _stables) {
            if (data.symbol.endsWith(s)) {
                _priceMap[data.symbol.slice(0, -s.length)] = price;
                break;
            }
        }
        if (!_balances.length || _priceTimer) return;
        _priceTimer = setTimeout(() => {
            _priceTimer = null;
            for (const b of _balances) {
                if (!_stables.has(b.asset) && _priceMap[b.asset]) {
                    b.usd_value = String(parseFloat(b.net) * _priceMap[b.asset]);
                }
            }
            _total = _calcTotal();
            _notify();
        }, 2000);
    });

    return { load, get, getTotal, onChange };
})();
