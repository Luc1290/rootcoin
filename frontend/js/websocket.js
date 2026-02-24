const WS = (() => {
    let ws = null;
    let backoff = 1;
    let handlers = {};
    let reconnectTimer = null;

    const statusDot = () => document.getElementById('ws-status');

    function connect() {
        if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
            return;
        }
        clearTimeout(reconnectTimer);
        reconnectTimer = null;

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
            if (!reconnectTimer) {
                reconnectTimer = setTimeout(() => {
                    reconnectTimer = null;
                    backoff = Math.min(backoff * 2, 60);
                    connect();
                }, backoff * 1000);
            }
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

    // Reconnect + refresh on resume from background (iOS / mobile)
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            backoff = 1;
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
            // Kill zombie WS — mobile browsers don't always fire onclose
            if (ws) {
                ws.onclose = null;
                ws.close();
                ws = null;
            }
            connect();
            // Reload active tab data — WS data is stale after background
            Positions.load();
            if (typeof Cockpit !== 'undefined') Cockpit.load();
        }
    });

    connect();

    return { on };
})();
