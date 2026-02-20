const Trades = (() => {
    const tbody = () => document.getElementById('trades-tbody');
    const filter = () => document.getElementById('trades-filter');

    function render(trades) {
        const tb = tbody();
        if (!trades.length) {
            tb.innerHTML = '<tr><td colspan="6" class="text-center text-gray-500 py-8">Aucun trade</td></tr>';
            return;
        }
        tb.innerHTML = trades.map(t => {
            const sideClass = t.side === 'BUY' ? 'side-long' : 'side-short';
            const price = parseFloat(t.price) || 0;
            const qty = parseFloat(t.quantity) || 0;
            const total = parseFloat(t.quote_qty) || (price * qty);
            const date = new Date(t.executed_at);
            const dateStr = date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })
                + ' ' + date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
            return `
            <tr class="border-b border-gray-800">
                <td class="py-2 text-gray-400">${dateStr}</td>
                <td class="py-2">${t.symbol}</td>
                <td class="py-2 ${sideClass} font-semibold">${t.side}</td>
                <td class="py-2 text-right">${price.toFixed(2)}</td>
                <td class="py-2 text-right">${qty}</td>
                <td class="py-2 text-right">$${total.toFixed(2)}</td>
            </tr>`;
        }).join('');
    }

    async function load(symbol) {
        try {
            const url = symbol ? `/api/trades?symbol=${symbol}` : '/api/trades';
            const resp = await fetch(url);
            const data = await resp.json();
            render(data);
        } catch (e) {
            console.error('Failed to load trades', e);
        }
    }

    // Filter change
    document.addEventListener('DOMContentLoaded', () => {
        const f = filter();
        if (f) f.addEventListener('change', () => load(f.value));
    });

    return { load, render };
})();
