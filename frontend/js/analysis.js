const Analysis = (() => {
    let _data = null;
    let _newsData = null;
    let _currentSymbol = null;
    let _trackHistory = [];
    let _trackStats = {};
    let _llmAnalysis = null;
    let _llmLoading = false;
    let _llmHistory = [];
    let _llmStats = {};

    async function load() {
        try {
            const [analysisResp, newsResp, oppResp, histResp, statsResp] = await Promise.all([
                fetch('/api/analysis'),
                fetch('/api/news'),
                fetch('/api/opportunities'),
                fetch('/api/opportunities/history?limit=50'),
                fetch('/api/opportunities/stats'),
            ]);
            if (analysisResp.ok) {
                _data = await analysisResp.json();
                _populateSymbols();
            }
            if (newsResp.ok) {
                _newsData = await newsResp.json();
            }
            if (oppResp.ok) {
                const oppData = await oppResp.json();
                Opportunities.update(oppData.opportunities || []);
            }
            if (histResp.ok) _trackHistory = (await histResp.json()).history || [];
            if (statsResp.ok) _trackStats = await statsResp.json();
            render();
            if (_currentSymbol) _loadCachedLlm(_currentSymbol);
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
            document.getElementById('analysis-whale').innerHTML = '';
            return;
        }
        document.getElementById('analysis-empty').classList.add('hidden');

        const analysis = _currentSymbol
            ? _data.analyses.find(a => a.symbol === _currentSymbol)
            : _data.analyses[0];

        if (!analysis) return;
        _currentSymbol = analysis.symbol;

        _renderFreshness();
        _renderLlm();
        _renderLlmTrackRecord();
        _renderOpportunities();
        _renderTrackRecord();
        _renderLevels(analysis);
        _renderMacro(_data.macro);
        _renderAlerts(analysis);
        _renderWhales();
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
            _loadCachedLlm(_currentSymbol);
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

    async function _loadCachedLlm(symbol) {
        if (!symbol) return;
        try {
            const [lastResp, histResp, statsResp] = await Promise.all([
                fetch(`/api/llm/last?symbol=${encodeURIComponent(symbol)}`),
                fetch(`/api/llm/history?symbol=${encodeURIComponent(symbol)}&limit=20`),
                fetch(`/api/llm/stats?symbol=${encodeURIComponent(symbol)}`),
            ]);
            if (lastResp.ok) {
                const data = await lastResp.json();
                if (data && data.direction) {
                    _llmAnalysis = data;
                }
            }
            if (histResp.ok) _llmHistory = (await histResp.json()).history || [];
            if (statsResp.ok) _llmStats = await statsResp.json();
            _renderLlm();
            _renderLlmTrackRecord();
        } catch (e) { /* silent */ }
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

        const layersHtml = _buildLayerExplanations(b);

        let freshnessHtml = '';
        if (_data && _data.computed_at) {
            const ago = Utils.timeAgo(_data.computed_at);
            const computedMs = new Date(_data.computed_at).getTime();
            const nextMs = computedMs + 60000;
            const nowMs = Date.now();
            const secsLeft = Math.max(0, Math.round((nextMs - nowMs) / 1000));
            const nextStr = secsLeft > 0 ? `${secsLeft}s` : 'imminent';
            freshnessHtml = `<div class="text-xs text-gray-500 mt-2">Mis a jour ${ago} · prochain refresh ~${nextStr}</div>`;
        }

        el.innerHTML = `
        <div class="card">
            <div class="metric-label mb-2">Biais &mdash; ${analysis.symbol}</div>
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
            <div class="space-y-1">${layersHtml}</div>
            ${freshnessHtml}
        </div>`;
    }

    function _buildLayerExplanations(bias) {
        const ls = bias.layer_scores;
        if (!ls) return '';

        const layers = [
            { key: 'primary_5m', label: '5min', score: ls.primary_5m, max: 30,
              desc: s => s > 20 ? 'Tendance + momentum alignes' : s > 10 ? 'Signal moderé, momentum partiel' : s > 0 ? 'Signal faible, peu de momentum' : 'Aucun signal' },
            { key: 'confirmation_15m', label: '15min', score: ls.confirmation_15m, max: 25,
              desc: s => s > 16 ? 'Confirme le 5min' : s > 7 ? 'Confirmation partielle' : s > 0 ? 'Neutre, pas de confirmation' : 'Oppose le 5min' },
            { key: 'context_1h', label: '1h', score: ls.context_1h, max: 15,
              desc: s => s > 10 ? 'Tendance horaire alignee' : s > 3 ? 'Tendance horaire neutre' : 'Tendance horaire opposee' },
            { key: 'warning_4h', label: '4h', score: ls.warning_4h, max: 5,
              desc: s => s >= 4 ? 'Contexte 4h favorable' : s >= 2 ? 'Contexte 4h neutre' : 'Contexte 4h defavorable' },
            { key: 'flow', label: 'Flux', score: ls.flow, max: 20,
              desc: s => s > 13 ? 'Pression achat/vente + orderbook + whales' : s > 7 ? 'Flux moderé en faveur' : s > 0 ? 'Flux faible' : 'Aucun flux favorable' },
            { key: 'macro', label: 'Macro', score: ls.macro, max: 5,
              desc: s => s > 2 ? 'Environnement macro favorable' : s < -2 ? 'Environnement macro defavorable' : 'Macro neutre' },
        ];

        return layers.map(l => {
            const pct = l.max > 0 ? Math.round(l.score / l.max * 100) : 0;
            const clampPct = Math.max(0, Math.min(pct, 100));
            const color = clampPct > 60 ? '#22c55e' : clampPct > 30 ? '#eab308' : '#ef4444';
            const explanation = l.desc(l.score);

            return `<div class="flex items-center gap-2 text-xs">
                <span class="text-gray-500 font-semibold" style="min-width:36px">${l.label}</span>
                <div class="flex-shrink-0" style="width:60px;height:4px;background:#292524;border-radius:2px;overflow:hidden">
                    <div style="width:${clampPct}%;height:100%;background:${color};border-radius:2px"></div>
                </div>
                <span class="tabular-nums text-gray-500" style="min-width:28px">${l.score}/${l.max}</span>
                <span class="text-gray-400">${explanation}</span>
            </div>`;
        }).join('');
    }

    // ── LLM Analysis ─────────────────────────────────────

    function _renderConfidenceFactors(factors) {
        const pour = (factors.pour || []).map(f => `<li class="text-green-400">\u2713 ${Utils.escHtml(f)}</li>`).join('');
        const contre = (factors.contre || []).map(f => `<li class="text-red-400">\u2717 ${Utils.escHtml(f)}</li>`).join('');
        if (!pour && !contre) return '';
        return `<div class="mb-3 p-2 rounded bg-stone-800/50 text-xs">
            <div class="font-semibold text-gray-400 mb-1">Facteurs de confiance</div>
            <ul class="space-y-0.5 list-none pl-0">${pour}${contre}</ul>
        </div>`;
    }

    function _renderLlm() {
        const el = document.getElementById('analysis-llm');
        if (!el) return;

        const btnDisabled = _llmLoading ? 'disabled' : '';
        const btnText = _llmLoading ? '<span class="llm-spinner"></span> Analyse en cours...' : 'Analyse IA';

        if (!_llmAnalysis || _llmAnalysis.symbol !== _currentSymbol) {
            el.innerHTML = `
            <div class="card llm-card">
                <div class="flex items-center justify-between">
                    <div class="metric-label">Second avis IA</div>
                    <div class="flex items-center gap-2">
                        <button class="llm-btn-preview" onclick="Analysis.previewPrompt()">Voir donnees</button>
                        <button id="llm-analyze-btn" class="llm-btn" ${btnDisabled} onclick="Analysis.requestLlm()">${btnText}</button>
                    </div>
                </div>
                <div class="text-xs text-gray-500 mt-2">Appuie pour obtenir une analyse Claude Opus</div>
                <div id="llm-preview" class="hidden"></div>
            </div>`;
            return;
        }

        const a = _llmAnalysis;
        if (a.error && !a.direction) {
            el.innerHTML = `
            <div class="card llm-card llm-card-error">
                <div class="flex items-center justify-between mb-2">
                    <div class="metric-label">Second avis IA</div>
                    <button id="llm-analyze-btn" class="llm-btn" ${btnDisabled} onclick="Analysis.requestLlm()">${btnText}</button>
                </div>
                <div class="text-sm text-red-400">Erreur: ${Utils.escHtml(a.error)}</div>
            </div>`;
            return;
        }

        const isLong = a.direction === 'LONG';
        const dirClass = isLong ? 'pnl-positive' : 'pnl-negative';
        const dirBorder = isLong ? '#22c55e' : '#ef4444';
        const arrow = isLong ? '\u25B2' : '\u25BC';

        const confNum = typeof a.confidence === 'number' ? a.confidence : (a.confidence === 'elevee' ? 85 : a.confidence === 'moderee' ? 60 : 35);
        const confColor = confNum >= 80 ? '#22c55e' : confNum >= 65 ? '#84cc16' : confNum >= 50 ? '#eab308' : confNum >= 35 ? '#f97316' : '#ef4444';
        const confLabel = confNum >= 80 ? 'Elevee' : confNum >= 65 ? 'Correcte' : confNum >= 50 ? 'Moderee' : confNum >= 35 ? 'Faible' : 'Tres faible';

        const tokensInfo = a.input_tokens ? `<span class="text-xs text-gray-600">${a.input_tokens + a.output_tokens} tokens</span>` : '';
        const timeInfo = a.analyzed_at ? `<span class="text-xs text-gray-500">${Utils.timeAgo(a.analyzed_at)}</span>` : '';

        el.innerHTML = `
        <div class="card llm-card" style="border-left:3px solid ${dirBorder}">
            <div class="flex items-center justify-between mb-3">
                <div class="metric-label">Second avis IA</div>
                <div class="flex items-center gap-2">
                    ${timeInfo}
                    ${tokensInfo}
                    <button id="llm-analyze-btn" class="llm-btn llm-btn-sm" ${btnDisabled} onclick="Analysis.requestLlm()">${_llmLoading ? '...' : 'Relancer'}</button>
                </div>
            </div>
            <div class="flex items-center gap-3 mb-3">
                <span class="llm-direction ${dirClass}">${arrow} ${a.direction}</span>
                <span class="llm-confidence" style="color:${confColor}">Confiance: ${confNum}% <span class="text-xs opacity-70">(${confLabel})</span></span>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                <div class="llm-level"><span class="text-gray-500 text-xs">Entry</span><span class="tabular-nums font-bold">${Utils.fmtPrice(a.entry)}</span></div>
                <div class="llm-level"><span class="text-gray-500 text-xs">Stop Loss</span><span class="tabular-nums font-bold pnl-negative">${Utils.fmtPrice(a.stop_loss)}</span></div>
                <div class="llm-level"><span class="text-gray-500 text-xs">TP1</span><span class="tabular-nums font-bold pnl-positive">${Utils.fmtPrice(a.tp1)}</span></div>
                <div class="llm-level"><span class="text-gray-500 text-xs">TP2</span><span class="tabular-nums font-bold pnl-positive">${Utils.fmtPrice(a.tp2)}</span></div>
            </div>
            <div class="flex items-center gap-3 mb-3 text-xs">
                <span class="text-gray-400">R:R <span class="font-bold" style="color:#c9956b">${a.risk_reward ? a.risk_reward.toFixed(1) : '?'}</span></span>
                <div class="flex-1 h-1.5 bg-stone-700 rounded-full overflow-hidden ml-2" style="max-width:120px">
                    <div class="h-full rounded-full" style="width:${confNum}%;background:${confColor}"></div>
                </div>
            </div>
            ${a.market_read ? `<div class="llm-market-read"><span class="text-xs text-gray-500 font-semibold">Lecture du marche :</span> ${Utils.escHtml(a.market_read)}</div>` : ''}
            ${a.position_advice && a.position_advice !== 'Pas de position ouverte.' ? `<div class="llm-position-advice"><span class="text-xs text-gray-500 font-semibold">Ta position :</span> ${Utils.escHtml(a.position_advice)}</div>` : ''}
            ${a.confidence_factors ? _renderConfidenceFactors(a.confidence_factors) : ''}
            <div class="llm-explanation"><span class="text-xs text-gray-500 font-semibold">Analyse :</span> ${Utils.escHtml(a.explanation || '')}</div>
            ${a.key_signal ? `<div class="llm-key-signal"><span class="text-xs text-gray-500 font-semibold">Signal cle :</span> ${Utils.escHtml(a.key_signal)}</div>` : ''}
            ${a.invalidation ? `<div class="llm-invalidation"><span class="text-xs text-gray-500 font-semibold">Invalidation :</span> ${Utils.escHtml(a.invalidation)}</div>` : ''}
            ${a.prompt_sent ? `<details class="mt-3"><summary class="text-xs text-gray-500 cursor-pointer hover:text-gray-300">Voir donnees envoyees</summary><pre class="llm-prompt-preview">${Utils.escHtml(a.prompt_sent)}</pre></details>` : ''}
        </div>`;
    }

    async function _previewPrompt() {
        if (!_currentSymbol) return;
        const el = document.getElementById('llm-preview');
        if (!el) return;
        if (!el.classList.contains('hidden')) { el.classList.add('hidden'); return; }
        el.innerHTML = '<div class="text-xs text-gray-500 py-2">Chargement...</div>';
        el.classList.remove('hidden');
        try {
            const resp = await fetch(`/api/llm/preview/${_currentSymbol}`);
            if (resp.ok) {
                const data = await resp.json();
                el.innerHTML = `<pre class="llm-prompt-preview">${Utils.escHtml(data.prompt)}</pre>`;
            } else {
                el.innerHTML = '<div class="text-xs text-red-400 py-2">Erreur chargement</div>';
            }
        } catch (e) {
            el.innerHTML = `<div class="text-xs text-red-400 py-2">${e.message}</div>`;
        }
    }

    async function _requestLlm() {
        if (_llmLoading || !_currentSymbol) return;
        _llmLoading = true;
        _renderLlm();
        try {
            const resp = await fetch('/api/llm/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: _currentSymbol }),
            });
            if (resp.ok) {
                _llmAnalysis = await resp.json();
            } else {
                const err = await resp.json().catch(() => ({ detail: 'Erreur inconnue' }));
                _llmAnalysis = { error: err.detail || 'Erreur serveur', symbol: _currentSymbol };
            }
        } catch (e) {
            _llmAnalysis = { error: e.message, symbol: _currentSymbol };
        }
        _llmLoading = false;
        _renderLlm();
        _loadCachedLlm(_currentSymbol);
    }

    function _renderLlmTrackRecord() {
        const el = document.getElementById('analysis-llm-track');
        if (!el) return;

        const history = _llmHistory.filter(h => h.outcome && h.outcome !== 'superseded');
        const stats = _llmStats;
        if (!history.length && !stats.total) { el.innerHTML = ''; return; }

        const wr = stats.win_rate || 0;
        const total = stats.total || 0;
        const wins = stats.wins || 0;
        const losses = stats.losses || 0;
        const avgWin = stats.avg_win_pct || 0;
        const avgLoss = stats.avg_loss_pct || 0;
        const totalPnl = stats.total_pnl_pct || 0;
        const confW = stats.avg_confidence_win || 0;
        const confL = stats.avg_confidence_loss || 0;

        const wrColor = wr >= 55 ? 'pnl-positive' : wr >= 45 ? 'text-yellow-400' : 'pnl-negative';
        const pnlClass = totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';

        const rows = history.map(h => {
            const sym = h.symbol.replace('USDC', '').replace('USDT', '');
            const isWin = h.outcome === 'tp1_hit' || h.outcome === 'tp2_hit';
            const isLoss = h.outcome === 'sl_hit';
            const statusLabel = h.outcome === 'tp1_hit' ? 'TP1' : h.outcome === 'tp2_hit' ? 'TP2' : h.outcome === 'sl_hit' ? 'SL' : 'Exp';
            const statusClass = isWin ? 'tp_hit' : isLoss ? 'sl_hit' : 'expired';
            const pnl = h.outcome_pnl_pct != null ? parseFloat(h.outcome_pnl_pct) : null;
            const pnlStr = pnl != null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%` : '--';
            const pnlCls = pnl != null ? (pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : 'text-gray-500';
            const dirIcon = h.direction === 'LONG' ? '&#x2191;' : '&#x2193;';
            const dirCls = h.direction === 'LONG' ? 'pnl-positive' : 'pnl-negative';
            const ago = h.analyzed_at ? Utils.timeAgoShort(h.analyzed_at) : '';

            return `<div class="track-record-row">
                <div class="flex items-center gap-1" style="min-width:0">
                    <span class="text-xs font-bold">${sym}</span>
                    <span class="text-xs ${dirCls}">${dirIcon}</span>
                    <span class="track-record-status ${statusClass}">${statusLabel}</span>
                    <span class="text-xs text-gray-500 tabular-nums" style="font-size:9px">conf ${h.confidence}%</span>
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                    <span class="text-xs font-bold tabular-nums ${pnlCls}">${pnlStr}</span>
                    <span class="text-xs text-gray-500">${ago}</span>
                </div>
            </div>`;
        }).join('');

        let calibration = '';
        if (confW && confL && total >= 5) {
            if (confL > confW + 5) {
                calibration = `<div class="text-xs text-orange-400 mt-1">Overconfidence : confiance plus haute sur les pertes (${confL}%) que sur les gains (${confW}%)</div>`;
            } else if (confW > confL + 10) {
                calibration = `<div class="text-xs text-green-400 mt-1">Bonne calibration : confiance plus haute sur les gains</div>`;
            }
        }

        el.innerHTML = `<div class="card" style="border-left:3px solid #c9956b">
            <div class="flex items-center justify-between mb-1">
                <div class="metric-label">Track Record IA</div>
                <span class="text-xs text-gray-500">${_currentSymbol || ''}</span>
            </div>
            <div class="flex items-center gap-3 text-xs mb-2">
                <span class="text-gray-400">${total} analyses</span>
                <span class="text-gray-400">${wins}W / ${losses}L</span>
                <span class="font-bold ${wrColor}">${wr}%</span>
                <span class="font-bold ${pnlClass}">${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(1)}%</span>
            </div>
            <div class="flex items-center gap-3 text-xs mb-2 text-gray-500">
                <span>Moy W <span class="pnl-positive">+${avgWin.toFixed(2)}%</span></span>
                <span>Moy L <span class="pnl-negative">${avgLoss.toFixed(2)}%</span></span>
                <span>Conf W ${confW}% / L ${confL}%</span>
            </div>
            ${calibration}
            <div style="max-height:150px;overflow-y:auto">${rows}</div>
        </div>`;
    }

    // ── Opportunities ──────────────────────────────────────

    function _renderOpportunities() {
        const el = document.getElementById('analysis-opportunities');
        if (el) Opportunities.renderCompact(el);
    }

    // ── Track Record ─────────────────────────────────────

    const REF_SIZE = 40000;

    function _renderTrackRecord() {
        const el = document.getElementById('analysis-track-record');
        if (!el) return;

        const history = _trackHistory;
        const stats = _trackStats;
        if (!history.length && !stats.total) { el.innerHTML = ''; return; }

        const winRate = stats.win_rate || 0;
        const total = stats.total || 0;
        const tpHit = stats.tp_hit || 0;
        const slHit = stats.sl_hit || 0;
        const totalPnl = stats.total_pnl_pct || 0;
        const totalPnlClass = totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const totalUsd = totalPnl / 100 * REF_SIZE;
        const totalUsdStr = `${totalUsd >= 0 ? '+' : '-'}$${Math.abs(totalUsd).toFixed(0)}`;
        const avgWin = stats.avg_win_pct || 0;
        const avgLoss = stats.avg_loss_pct || 0;

        const rows = history.map(r => {
            const sym = r.symbol.replace('USDC', '');
            const dirIcon = r.direction === 'LONG' ? '&#x2191;' : '&#x2193;';
            const dirClass = r.direction === 'LONG' ? 'pnl-positive' : 'pnl-negative';
            const statusLabel = r.status === 'tp_hit' ? 'TP' : r.status === 'sl_hit' ? 'SL' : r.status === 'expired' ? 'Exp' : r.status === 'taken' ? 'Ouvert' : r.status;
            const pnl = r.outcome_pnl_pct ? parseFloat(r.outcome_pnl_pct) : null;
            const pnlStr = pnl !== null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%` : '--';
            const pnlClass = pnl !== null ? (pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : 'text-gray-500';
            const pnlUsd = pnl !== null ? pnl / 100 * REF_SIZE : null;
            const pnlUsdStr = pnlUsd !== null ? `${pnlUsd >= 0 ? '+' : '-'}$${Math.abs(pnlUsd).toFixed(0)}` : '';
            const rr = r.rr ? parseFloat(r.rr) : null;
            const rrStr = rr !== null ? `${rr.toFixed(1)}` : '';
            const rrColor = rr !== null ? (rr >= 2 ? '#c9956b' : rr >= 1.5 ? '#a78b6d' : '#6b7280') : '';
            const ago = r.detected_at ? Utils.timeAgoShort(r.detected_at) : '';

            let durationStr = '';
            if (r.detected_at && r.resolved_at) {
                const ms = new Date(r.resolved_at) - new Date(r.detected_at);
                const mins = Math.floor(ms / 60000);
                if (mins < 60) durationStr = `${mins}m`;
                else if (mins < 1440) durationStr = `${Math.floor(mins / 60)}h${mins % 60 ? (mins % 60) + 'm' : ''}`;
                else durationStr = `${Math.floor(mins / 1440)}j`;
            }

            return `<div class="track-record-row">
                <div class="flex items-center gap-1" style="min-width:0">
                    <span class="text-xs font-bold">${sym}</span>
                    <span class="text-xs ${dirClass}">${dirIcon}</span>
                    <span class="track-record-status ${r.status}">${statusLabel}</span>
                    ${rrStr ? `<span class="text-xs tabular-nums font-semibold" style="font-size:9px;color:${rrColor}">${rrStr}R</span>` : ''}
                    ${durationStr ? `<span class="text-xs text-gray-500" style="font-size:9px">${durationStr}</span>` : ''}
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                    <span class="text-xs font-bold tabular-nums ${pnlClass}">${pnlStr}</span>
                    ${pnlUsdStr ? `<span class="text-xs tabular-nums ${pnlClass}" style="font-size:9px;opacity:0.8">${pnlUsdStr}</span>` : ''}
                    <span class="text-xs text-gray-500">${ago}</span>
                </div>
            </div>`;
        }).join('');

        el.innerHTML = `<div class="card" style="border-left:3px solid #a78b6d">
            <div class="flex items-center justify-between mb-1">
                <div class="metric-label">Track Record</div>
                <span class="text-xs text-gray-500">/ $${(REF_SIZE/1000).toFixed(0)}k</span>
            </div>
            <div class="flex items-center gap-3 text-xs mb-2">
                <span class="text-gray-400">${total} sig</span>
                <span class="text-gray-400">${tpHit}W / ${slHit}L</span>
                <span class="font-bold ${winRate >= 50 ? 'pnl-positive' : 'pnl-negative'}">${winRate}%</span>
                <span class="font-bold ${totalPnlClass}">${totalUsdStr}</span>
                <span class="text-gray-500" style="font-size:9px">moy W <span class="pnl-positive">+${avgWin.toFixed(2)}%</span> L <span class="pnl-negative">${avgLoss.toFixed(2)}%</span></span>
            </div>
            <div style="max-height:150px;overflow-y:auto">${rows}</div>
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

        const displayOrder = ['dxy', 'vix', 'nasdaq', 'sp500', 'gold', 'us10y', 'spread', 'oil', 'usdjpy', 'mstr', 'ibit', 'googl', 'nvda', 'cac40', 'dax', 'eurusd'];
        const names = {
            dxy: 'DXY', vix: 'VIX', nasdaq: 'Nasdaq', sp500: 'S&P 500', gold: 'Gold',
            us10y: 'US 10Y', spread: 'Spread 10-5Y',
            oil: 'Petrole', usdjpy: 'USD/JPY',
            mstr: 'MicroStrategy', ibit: 'BTC ETF (IBIT)',
            googl: 'Google', nvda: 'Nvidia',
            cac40: 'CAC 40', dax: 'DAX', eurusd: 'EUR/USD',
        };
        const cryptoImpact = {
            dxy: 'inverse', vix: 'inverse', nasdaq: 'direct', sp500: 'direct', gold: 'inverse',
            us10y: 'inverse', spread: 'spread', oil: 'inverse', usdjpy: 'direct',
            mstr: 'direct', ibit: 'direct', googl: 'direct', nvda: 'direct',
            cac40: 'direct', dax: 'direct', eurusd: 'inverse',
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
        const alerts = (analysis.alerts || []).filter(a => a.type !== 'whale');

        if (!alerts.length) {
            el.innerHTML = '';
            return;
        }

        const chips = alerts.map(a => {
            const cls = `alert-chip alert-chip-${a.type}`;
            const icon = a.type === 'conflict' ? '\u26A0'
                : a.type === 'aligned' ? '\u2705'
                : '\u2139';
            return `<span class="${cls}" title="${Utils.escHtml(a.message)}">${icon} ${Utils.escHtml(a.message)}</span>`;
        }).join('');

        el.innerHTML = `
        <div class="card">
            <div class="metric-label mb-2">Alertes</div>
            <div class="flex flex-wrap gap-1.5">${chips}</div>
        </div>`;
    }

    // ── Block 4b: Whales ──────────────────────────────────

    let _whaleFilter = 'ALL';

    function _renderWhales() {
        const el = document.getElementById('analysis-whale');
        const allWhales = _data && _data.whale_alerts ? _data.whale_alerts : [];
        const whales = allWhales.slice().reverse();

        if (!whales.length) {
            el.innerHTML = '';
            return;
        }

        const symbols = [...new Set(whales.map(w => w.symbol.replace('USDC', '').replace('USDT', '')))].sort();
        const options = symbols.map(s => `<option value="${s}"${_whaleFilter === s ? ' selected' : ''}>${s}</option>`).join('');

        const filtered = _whaleFilter === 'ALL' ? whales
            : whales.filter(w => w.symbol.replace('USDC', '').replace('USDT', '') === _whaleFilter);

        const rows = filtered.map(w => {
            const sym = w.symbol.replace('USDC', '').replace('USDT', '');
            const qty = Utils.fmtQuoteQty(w.quote_qty);
            const price = Utils.fmtPriceCompact(w.price);
            const ago = Utils.timeAgoShort(w.timestamp);
            const isBuy = w.side === 'BUY';
            const sideClass = isBuy ? 'side-long' : 'side-short';
            const label = isBuy ? 'Achat massif' : 'Vente massive';
            return `<div class="flex items-center gap-1.5 py-1.5">
                    <span class="text-xs">\uD83D\uDC0B</span>
                    <span class="cockpit-side ${sideClass}">${label}</span>
                    <span class="text-xs text-gray-300"><b>${qty}</b> de ${sym} \u00e0 ${price}</span>
                    <span class="text-xs text-gray-500">&middot; ${ago}</span>
            </div>`;
        }).join('');

        el.innerHTML = `
        <div class="card">
            <div class="flex items-center justify-between mb-2">
                <span class="metric-label">Whale alerts</span>
                <select id="analysis-whale-filter" class="bg-gray-800 text-xs text-gray-300 border border-gray-600 rounded px-1 py-0.5">
                    <option value="ALL"${_whaleFilter === 'ALL' ? ' selected' : ''}>Tous</option>
                    ${options}
                </select>
            </div>
            <div style="max-height:280px;overflow-y:auto">
                ${rows}
            </div>
        </div>`;

        const sel = document.getElementById('analysis-whale-filter');
        if (sel) sel.onchange = () => { _whaleFilter = sel.value; _renderWhales(); };
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
        if (data.opportunities) Opportunities.update(data.opportunities);
        if (document.getElementById('view-analysis').classList.contains('hidden')) return;
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

    return { load, requestLlm: _requestLlm, previewPrompt: _previewPrompt };
})();
