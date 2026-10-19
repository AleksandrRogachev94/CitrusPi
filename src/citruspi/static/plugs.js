// Optional equipment monitoring. Device names are inserted as text, never HTML.
(() => {
    let chart = null;
    let requestId = 0;
    let cardRequestId = 0;
    const cards = document.getElementById('plug-cards');
    const status = document.getElementById('power-chart-status');
    const colors = ['#38bdf8', '#a78bfa', '#f472b6', '#2dd4bf'];

    async function getJSON(url) {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Request failed');
        return response.json();
    }

    function line(parent, text, className) {
        const node = document.createElement('p');
        node.textContent = text;
        if (className) node.className = className;
        parent.appendChild(node);
    }

    function format(value, digits, suffix) {
        return Number.isFinite(value) ? value.toFixed(digits) + suffix : '—';
    }

    function markStale() {
        for (const card of cards.children) {
            if (card.dataset.timestamp && Date.now() - Date.parse(card.dataset.timestamp) > card.dataset.staleMs) {
                card.querySelector('.plug-state').textContent = 'Unknown · readings stale';
                card.querySelector('.plug-watts').textContent = '— W';
                card.querySelector('.plug-relay').textContent = 'Plug: Unknown';
            }
        }
    }

    async function refreshCards() {
        const current = ++cardRequestId;
        try {
            const plugs = await getJSON('/api/plugs');
            if (current !== cardRequestId) return;
            const nodes = plugs.map(plug => {
                const card = document.createElement('article');
                card.className = 'plug-card';
                const latest = plug.latest;
                const state = !plug.monitoring ? 'Monitoring paused' : plug.stale ? 'Unknown · readings stale' : ({
                    auth_error: 'Authentication needed', unsupported: 'Firmware unsupported',
                    unreachable: 'Unknown · plug unreachable', energy_unavailable: 'Power unavailable',
                }[latest?.status] || (latest?.active === true ? 'Active' : latest?.active === false ? 'Idle' : 'Unknown'));
                card.dataset.timestamp = latest?.timestamp || '';
                card.dataset.staleMs = plug.stale_after_sec * 1000;
                line(card, plug.name, 'plug-name');
                line(card, state, 'plug-state');
                line(card, format(plug.stale || !plug.monitoring ? null : latest?.power_w, 1, ' W'), 'plug-watts');
                const relay = plug.stale || !plug.monitoring || latest?.relay_on == null ? 'Unknown' : latest.relay_on ? 'Enabled' : 'Off';
                line(card, `Plug: ${relay}`, 'plug-relay muted small');
                const s = plug.summary;
                const runtime = (Number.isFinite(s.runtime_hours) ? '~' : '') + format(s.runtime_hours, 2, ' h active');
                const energy = (Number.isFinite(s.estimated_kwh) ? '~' : '') + format(s.estimated_kwh, 3, ' kWh');
                line(card, `24h: ${runtime} · ${energy}`, 'small');
                line(card, `${format(s.coverage_pct, 1, '% coverage')} · ${format(s.duty_cycle_pct, 1, '% active while observed')}`, 'muted small');
                line(card, `Plug's today counter: ${format(latest?.energy_today_kwh, 3, ' kWh')}${plug.stale ? ' (last recorded)' : ''}`, 'muted small');
                if (latest) line(card, `Last contact attempt: ${new Date(latest.timestamp).toLocaleString()}`, 'muted small');
                return card;
            });
            cards.replaceChildren(...nodes);
        } catch (_) {
            if (current !== cardRequestId) return;
            // Clear live claims immediately when the API cannot be reached.
            cards.replaceChildren();
            line(cards, 'Equipment data unavailable. Retrying shortly.', 'muted small');
        }
    }

    window.loadPowerChart = async function(hours) {
        const current = ++requestId;
        try {
            const [plugs, temperatures, ready] = await Promise.all([
                getJSON(`/api/plugs/history?hours=${hours}`),
                getJSON(`/api/history?hours=${hours}`).catch(() => []),
                window.chartsReady,
            ]);
            if (current !== requestId) return;
            if (ready === false || typeof Chart === 'undefined') throw new Error('Chart unavailable');
            const datasets = plugs.map((plug, index) => {
                const points = [];
                for (const reading of plug.readings) {
                    const time = Date.parse(reading.timestamp);
                    points.push({ x: time, y: reading.power_w });
                }
                return {
                    label: `${plug.name} W`, data: points, yAxisID: 'power',
                    borderColor: colors[index % colors.length], stepped: true,
                    spanGaps: false, pointRadius: 0, borderWidth: 2,
                };
            });
            for (const [field, label, color] of [
                ['temp_f', 'Canopy °F', '#f59e0b'], ['root_temp_f', 'Root °F', '#84cc16'],
            ]) datasets.push({
                label, data: temperatures.map(r => ({ x: r.timestamp, y: r[field] })),
                yAxisID: 'temperature', borderColor: color, borderDash: [4, 4],
                spanGaps: false, pointRadius: 0, borderWidth: 1.5,
            });
            if (chart) chart.destroy();
            const textColor = getThemeColors().textColor;
            chart = new Chart(document.getElementById('powerChart').getContext('2d'), {
                type: 'line', data: { datasets }, options: {
                    responsive: true, maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: { legend: { labels: { color: textColor } } },
                    scales: {
                        x: { type: 'time', min: Date.now() - hours * 3600000, max: Date.now(),
                            ticks: { color: textColor, maxTicksLimit: 7, maxRotation: 0 } },
                        power: { position: 'left', min: 0, ticks: { color: textColor },
                            title: { display: true, text: 'Watts', color: textColor } },
                        temperature: { position: 'right', grid: { drawOnChartArea: false },
                            ticks: { color: textColor },
                            title: { display: true, text: '°F', color: textColor } },
                    },
                },
            });
            status.textContent = plugs.some(p => p.readings.some(r => Number.isFinite(r.power_w))) ? '' : 'No power readings yet.';
        } catch (_) {
            if (current !== requestId) return;
            if (chart) chart.destroy();
            chart = null;
            status.textContent = 'Power history unavailable. Retrying on the next chart refresh.';
        }
    };
    refreshCards();
    window.loadPowerChart(currentRangeHours);
    setInterval(refreshCards, 30000);
    setInterval(markStale, 5000);
})();
