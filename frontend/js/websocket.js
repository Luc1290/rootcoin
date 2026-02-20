const WS = (() => {
    let ws = null;
    let backoff = 1;
    let handlers = {};

    const statusDot = () => document.getElementById('ws-status');

    function connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${proto}//${location.host}/ws`);

        ws.onopen = () => {
            backoff = 1;
            const dot = statusDot();
            if (dot) {
                dot.classList.remove('bg-red-500', 'bg-yellow-500');
                dot.classList.add('bg-green-500');
                dot.title = 'Connected';
            }
        };

        ws.onclose = () => {
            const dot = statusDot();
            if (dot) {
                dot.classList.remove('bg-green-500');
                dot.classList.add('bg-yellow-500');
                dot.title = 'Reconnecting...';
            }
            setTimeout(() => {
                backoff = Math.min(backoff * 2, 60);
                connect();
            }, backoff * 1000);
        };

        ws.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                const type = msg.type;
                if (handlers[type]) {
                    handlers[type].forEach(fn => fn(msg.data));
                }
            } catch (err) {
                console.error('WS parse error', err);
            }
        };
    }

    function on(type, fn) {
        if (!handlers[type]) handlers[type] = [];
        handlers[type].push(fn);
    }

    connect();

    return { on };
})();
