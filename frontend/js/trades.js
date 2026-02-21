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
            <tr>
                <td class="text-gray-400 tabular-nums">${dateStr}</td>
                <td class="font-medium">${t.symbol}</td>
                <td class="${sideClass} font-semibold">${t.side}</td>
                <td class="text-right tabular-nums">${price.toFixed(2)}</td>
                <td class="text-right tabular-nums">${qty}</td>
                <td class="text-right tabular-nums font-medium">$${total.toFixed(2)}</td>
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

    document.addEventListener('DOMContentLoaded', () => {
        const f = filter();
        if (f) f.addEventListener('change', () => load(f.value));
    });

    return { load, render };
})();
