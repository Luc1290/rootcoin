const Balances = (() => {
    const tbody = () => document.getElementById('balances-tbody');
    const totalEl = () => document.getElementById('portfolio-total');
    let chartInitialized = false;

    function render(balances) {
        const tb = tbody();
        if (!balances.length) {
            tb.innerHTML = '<tr><td colspan="5" class="text-center text-gray-500 py-8">Aucune balance</td></tr>';
            totalEl().textContent = '$0.00';
            return;
        }

        let total = 0;
        tb.innerHTML = balances.map(b => {
            const free = parseFloat(b.free) || 0;
            const locked = parseFloat(b.locked) || 0;
            const net = parseFloat(b.net) || 0;
            // Estimate USD: stablecoins = 1:1, others need price
            if (['USDC', 'USDT', 'BUSD', 'FDUSD', 'DAI'].includes(b.asset)) {
                total += net;
            }
            const wallet = b.wallet_type.replace('_', ' ');
            return `
            <tr class="border-b border-gray-800">
                <td class="py-2 font-medium">${b.asset}</td>
                <td class="py-2 text-gray-400 text-xs">${wallet}</td>
                <td class="py-2 text-right">${formatNum(free)}</td>
                <td class="py-2 text-right">${formatNum(locked)}</td>
                <td class="py-2 text-right font-medium">${formatNum(net)}</td>
            </tr>`;
        }).join('');

        totalEl().textContent = `$${total.toFixed(2)}`;

        if (!chartInitialized) {
            chartInitialized = true;
            Charts.createPortfolioChart('portfolio-chart');
        }
    }

    function setChartRange(hours) {
        // Update active button
        document.querySelectorAll('.chart-range-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.hours === String(hours));
        });
        Charts.loadPortfolioData(hours);
    }

    function formatNum(n) {
        if (n === 0) return '0';
        if (n >= 1) return n.toFixed(4);
        return n.toFixed(8);
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

    // Real-time balance updates trigger a reload
    WS.on('balance_update', () => load());

    return { load, render, setChartRange };
})();
