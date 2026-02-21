const Balances = (() => {
    const tbody = () => document.getElementById('balances-tbody');
    const totalEl = () => document.getElementById('portfolio-total');
    let chartInitialized = false;
    const stables = ['USDC', 'USDT', 'BUSD', 'FDUSD', 'DAI'];

    function aggregate(balances) {
        const map = {};
        for (const b of balances) {
            if (!map[b.asset]) {
                map[b.asset] = { asset: b.asset, free: 0, locked: 0, borrowed: 0, interest: 0, net: 0, usdValue: 0, hasUsd: false, wallets: new Set() };
            }
            const a = map[b.asset];
            a.free += parseFloat(b.free) || 0;
            a.locked += parseFloat(b.locked) || 0;
            a.borrowed += parseFloat(b.borrowed) || 0;
            a.interest += parseFloat(b.interest) || 0;
            a.net += parseFloat(b.net) || 0;
            if (b.usd_value) {
                a.usdValue += parseFloat(b.usd_value);
                a.hasUsd = true;
            }
            a.wallets.add(b.wallet_type);
        }
        return Object.values(map).sort((a, b) => {
            const aUsd = a.hasUsd ? a.usdValue : (stables.includes(a.asset) ? a.net : 0);
            const bUsd = b.hasUsd ? b.usdValue : (stables.includes(b.asset) ? b.net : 0);
            return Math.abs(bUsd) - Math.abs(aUsd);
        });
    }

    function render(balances) {
        const tb = tbody();
        if (!balances.length) {
            tb.innerHTML = '<tr><td colspan="4" class="text-center text-gray-500 py-8">Aucune balance</td></tr>';
            totalEl().textContent = '$0.00';
            return;
        }

        const rows = aggregate(balances);
        let total = 0;
        rows.forEach(a => {
            const usdVal = a.hasUsd ? a.usdValue : (stables.includes(a.asset) ? a.net : null);
            if (usdVal !== null) total += usdVal;
        });

        tb.innerHTML = rows.map((a, i) => {
            const usdVal = a.hasUsd ? a.usdValue : (stables.includes(a.asset) ? a.net : null);
            const walletLabel = [...a.wallets].map(w => w === 'SPOT' ? 'S' : w === 'CROSS_MARGIN' ? 'C' : 'I').join('+');
            const borrowedHtml = a.borrowed > 0
                ? `<div class="text-yellow-500 text-xs">empr. ${formatQty(a.borrowed)}</div>`
                : '';
            const usdClass = usdVal !== null && usdVal < 0 ? 'text-red-400' : '';
            const usdText = usdVal !== null ? formatUsd(usdVal) : '-';
            const totalCell = i === 0
                ? `<td class="text-center font-bold text-base align-middle border-l border-gray-800/50 pl-5 pr-2" rowspan="${rows.length}">${formatUsd(total)}</td>`
                : '';

            return `
            <tr>
                <td class="font-medium">${a.asset} <span class="text-gray-600 text-xs font-normal">${walletLabel}</span></td>
                <td class="text-center tabular-nums">${formatQty(a.net)}${borrowedHtml}</td>
                <td class="text-center font-medium tabular-nums ${usdClass}">${usdText}</td>
                ${totalCell}
            </tr>`;
        }).join('');

        totalEl().textContent = formatUsd(total);

        if (!chartInitialized) {
            chartInitialized = true;
            Charts.createPortfolioChart('portfolio-chart');
        }
    }

    function setChartRange(hours) {
        document.querySelectorAll('.chart-range-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.hours === String(hours));
        });
        Charts.loadPortfolioData(hours);
    }

    function formatQty(n) {
        if (n === 0) return '0';
        if (Math.abs(n) >= 1) return n.toFixed(4);
        return n.toFixed(8);
    }

    function formatUsd(n) {
        const sign = n < 0 ? '-' : '';
        return `${sign}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    async function load() {
        try {
            const resp = await fetch('/api/balances');
            const data = await resp.json();
            render(data);
        } catch (e) {
            console.error('Failed to load balances', e);
        }
    }

    WS.on('balance_update', () => load());

    return { load, render, setChartRange };
})();
