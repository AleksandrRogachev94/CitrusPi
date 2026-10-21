// Read-only equipment status and history. User-configured names stay text, never HTML.
(() => {
    const cards = document.getElementById('plug-cards');
    const electricityCost = document.getElementById('electricity-cost');
    const details = document.getElementById('power-details');
    const lanes = document.getElementById('equipment-lanes');
    const activityStatus = document.getElementById('activity-chart-status');
    const powerStatus = document.getElementById('power-chart-status');
    const roleColors = { heater: '#f97316', light: '#a78bfa', other: '#38bdf8' };
    let temperatureChart = null;
    let powerChart = null;
    let history = null;
    let historyRequest = 0;
    let cardRequest = 0;
    let summaryHours = null;

    async function getJSON(url) {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Request failed');
        return response.json();
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function format(value, digits, suffix) {
        return Number.isFinite(value) ? value.toFixed(digits) + suffix : '—';
    }

    function money(value) {
        return Number.isFinite(value) ? `~$${value.toFixed(2)}` : '—';
    }

    function totalCost(plugs) {
        const values = plugs.map(p => p.summary.estimated_cost_usd).filter(Number.isFinite);
        return values.length ? money(values.reduce((sum, value) => sum + value, 0)) : '—';
    }

    function duration(ms) {
        if (!Number.isFinite(ms)) return '—';
        if (ms <= 0) return '0 min';
        const minutes = Math.round(ms / 60000);
        if (minutes < 1) return '<1 min';
        const hours = Math.floor(minutes / 60);
        return '~' + [hours ? `${hours} h` : '', minutes % 60 ? `${minutes % 60} min` : ''].filter(Boolean).join(' ');
    }

    function timestamp(ms) {
        return new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }

    function timeRange(start, end) {
        const finish = new Date(start).toDateString() === new Date(end).toDateString()
            ? new Date(end).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
            : timestamp(end);
        return `${timestamp(start)} – ${finish}`;
    }

    function periodLabel(name, active, start, end) {
        return `${name} · ${active ? 'Active' : 'Idle'} · ${timeRange(start, end)} · ${duration(end - start)}`;
    }

    function markStale() {
        for (const card of cards.children) {
            if (card.dataset.timestamp && Date.now() - Date.parse(card.dataset.timestamp) > Number(card.dataset.staleMs)) {
                card.querySelector('.plug-state').textContent = 'Unknown · readings stale';
                card.querySelector('.plug-watts').textContent = '— W';
            }
        }
    }

    async function refreshCards(hours = currentRangeHours) {
        const current = ++cardRequest;
        if (summaryHours !== hours) {
            summaryHours = hours;
            document.getElementById('plug-details').replaceChildren();
            if (electricityCost) electricityCost.textContent = 'Loading electricity estimate…';
        }
        try {
            const plugs = await getJSON(`/api/plugs?hours=${hours}`);
            if (current !== cardRequest) return;
            const usage = [];
            const nodes = plugs.map(plug => {
                const latest = plug.latest;
                const known = plug.monitoring && !plug.stale;
                const state = !plug.monitoring ? 'Monitoring paused' : plug.stale ? 'Unknown · readings stale' : ({
                    auth_error: 'Authentication needed', unsupported: 'Firmware unsupported',
                    unreachable: 'Unknown · plug unreachable', energy_unavailable: 'Power unavailable',
                }[latest?.status] || (latest?.active === true ? 'Active' : latest?.active === false ? 'Idle' : 'Unknown'));
                const card = element('article', 'plug-card');
                card.dataset.timestamp = latest?.timestamp || '';
                card.dataset.staleMs = plug.stale_after_sec * 1000;
                const heading = element('div', 'plug-heading');
                heading.appendChild(element('p', 'plug-name', plug.name));
                heading.appendChild(element('p', 'plug-state', state));
                card.appendChild(heading);
                const watts = known ? latest?.power_w : null;
                card.appendChild(element('p', 'plug-watts', format(watts, watts === 0 ? 0 : 1, ' W')));
                card.appendChild(element('p', 'muted small', `Today: ${format(latest?.energy_today_kwh, 3, ' kWh')}${!known ? ' (last recorded)' : ''}`));

                const report = element('article', 'plug-usage');
                report.appendChild(element('p', 'plug-name', plug.name));
                const summary = plug.summary;
                const runtime = summary.runtime_hours == null ? '—' : duration(summary.runtime_hours * 3600000);
                const energy = summary.estimated_kwh == null ? '—' : `~${format(summary.estimated_kwh, 3, ' kWh')}`;
                const range = hours <= 24 ? `${hours}h` : `${hours / 24}d`;
                report.appendChild(element('p', 'small', `${range} · ${runtime} active · ${energy}`));
                if (electricityCost) {
                    report.appendChild(element('p', 'small', `Estimated cost: ${money(summary.estimated_cost_usd)}`));
                }
                report.appendChild(element('p', 'muted small', `${format(summary.coverage_pct, 0, '% data coverage')}`));
                // The timestamp makes this a historical observation, not a second live claim.
                if (latest) {
                    const relay = latest.relay_on == null ? 'unknown' : latest.relay_on ? 'enabled' : 'off';
                    report.appendChild(element('p', 'muted small', `${timestamp(Date.parse(latest.timestamp))} · outlet ${relay}`));
                }
                usage.push(report);
                return card;
            });
            cards.replaceChildren(...nodes);
            document.getElementById('plug-details').replaceChildren(...usage);
            if (electricityCost) {
                const range = hours <= 24 ? `${hours}h` : `${hours / 24}d`;
                electricityCost.textContent = `Estimated electricity total (${range}): ${totalCost(plugs)}`;
                if (plugs.some(p => p.summary.estimated_cost_usd == null)) {
                    electricityCost.textContent += ' · incomplete plug data';
                }
            }
        } catch (_) {
            if (current !== cardRequest) return;
            cards.replaceChildren(element('p', 'muted small', 'Equipment data unavailable. Retrying shortly.'));
            document.getElementById('plug-details').replaceChildren();
            if (electricityCost) electricityCost.textContent = 'Electricity estimate unavailable.';
        }
    }

    function renderLanes(plugs, bounds) {
        const span = bounds.end - bounds.start;
        const selection = document.getElementById('activity-selection');
        selection.textContent = 'Select a period for timing and duration.';
        const periods = document.getElementById('activity-periods');
        periods.replaceChildren();
        lanes.replaceChildren(...plugs.map(plug => {
            const row = element('div', 'equipment-lane-row');
            row.style.setProperty('--equipment-color', roleColors[plug.role] || roleColors.other);
            const intervals = plug.activity.map(interval => ({
                start: Math.max(bounds.start, Date.parse(interval.start)),
                end: Math.min(bounds.end, Date.parse(interval.end)), active: interval.active,
            })).filter(interval => Number.isFinite(interval.start) && Number.isFinite(interval.end)
                && interval.end > interval.start && interval.active != null);
            const active = intervals.filter(interval => interval.active);
            const activeTime = active.reduce((total, interval) => total + interval.end - interval.start, 0);
            const heading = element('div', 'equipment-lane-heading');
            heading.appendChild(element('p', 'equipment-lane-title', plug.name));
            heading.appendChild(element('p', 'muted small', intervals.length ? `${duration(activeTime)} active in view` : 'No observed duration'));
            row.appendChild(heading);
            const track = element('div', 'equipment-lane-track');
            for (const { start, end, active } of intervals) {
                const segment = element('button', `lane-segment ${active ? 'active' : 'idle'}`);
                segment.type = 'button';
                segment.style.left = `${(start - bounds.start) / span * 100}%`;
                segment.style.width = `${(end - start) / span * 100}%`;
                const label = periodLabel(plug.name, active, start, end);
                segment.title = label;
                segment.setAttribute('aria-label', label);
                segment.addEventListener('click', () => { selection.textContent = label; });
                segment.addEventListener('focus', () => { selection.textContent = label; });
                track.appendChild(segment);
            }
            // A single observation has no duration, but must still be visible.
            const last = plug.readings.at(-1);
            const time = Date.parse(last?.timestamp);
            if (last?.active != null && time >= bounds.start && time <= bounds.end
                && !intervals.some(interval => time >= interval.start && time <= interval.end)) {
                const marker = element('button', `lane-marker ${last.active ? 'active' : 'idle'}`);
                marker.type = 'button';
                marker.style.left = `${(time - bounds.start) / span * 100}%`;
                marker.title = `${plug.name}: ${last.active ? 'Active' : 'Idle'} at ${new Date(time).toLocaleString()}`;
                marker.setAttribute('aria-label', marker.title);
                marker.addEventListener('click', () => { selection.textContent = `${marker.title} · single reading`; });
                marker.addEventListener('focus', () => { selection.textContent = `${marker.title} · single reading`; });
                track.appendChild(marker);
            }
            row.appendChild(track);

            const group = element('div', 'activity-period-group');
            group.appendChild(element('p', 'plug-name', plug.name));
            const list = element('ol', 'activity-period-list');
            for (const { start, end } of active.slice(-20).reverse()) {
                const item = element('li', '');
                item.appendChild(element('span', '', timeRange(start, end)));
                item.appendChild(element('strong', '', duration(end - start)));
                list.appendChild(item);
            }
            group.appendChild(list);
            group.appendChild(element('p', 'muted small', active.length
                ? `${active.length > 20 ? `Latest 20 of ${active.length}` : active.length} active period${active.length === 1 ? '' : 's'} in view`
                : intervals.length ? 'No active periods observed in this range.' : 'No activity readings in this range.'));
            periods.appendChild(group);
            return row;
        }));
        const starts = plugs.map(p => Date.parse(p.recorded_since)).filter(Number.isFinite);
        const first = Math.min(...starts);
        const note = document.getElementById('activity-history-note');
        note.textContent = first > bounds.start + span * 0.05 && first <= bounds.end
            ? `Recorded since ${timestamp(first)}.`
            : starts.length ? '' : 'Waiting for the first plug reading.';
    }

    function options(hours, bounds, theme) {
        return {
            responsive: true, maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'nearest', axis: 'x' },
            plugins: { legend: { labels: { color: theme.textColor, usePointStyle: true, boxWidth: 12, boxHeight: 8, padding: 16 } } },
            scales: {
                x: {
                    type: 'time', min: bounds.start, max: bounds.end,
                    time: { unit: hours <= 1 ? 'minute' : hours > 24 ? 'day' : 'hour',
                        displayFormats: { minute: 'h:mm a', hour: 'ha', day: 'MMM d' },
                        tooltipFormat: 'MMM d, h:mm a' },
                    grid: { display: false },
                    ticks: { color: theme.textColor, maxTicksLimit: 7, maxRotation: 0 },
                },
                y: { ticks: { color: theme.textColor, padding: 2, font: { size: 10 } },
                    border: { display: false }, grid: { color: theme.gridColor, tickLength: 0 } },
            },
        };
    }

    function renderPower() {
        if (!history || !details.open) return;
        const { plugs, hours, bounds, theme } = history;
        if (powerChart) powerChart.destroy();
        const config = options(hours, bounds, theme);
        config.scales.y.min = 0;
        powerChart = new Chart(document.getElementById('powerChart').getContext('2d'), {
            type: 'line', options: config,
            data: { datasets: plugs.map(plug => ({
                label: `${plug.name} W`, borderColor: roleColors[plug.role] || roleColors.other,
                data: plug.readings.map(r => ({ x: r.timestamp, y: r.power_w })),
                stepped: true, spanGaps: false,
                pointRadius: plug.readings.length === 1 ? 3 : 0, pointHoverRadius: 4, borderWidth: 2,
            })) },
        });
        powerStatus.textContent = plugs.some(p => p.readings.some(r => Number.isFinite(r.power_w))) ? '' : 'No power readings yet.';
    }

    details.addEventListener('toggle', renderPower);
    window.loadPowerChart = async function(hours, bounds = { start: Date.now() - hours * 3600000, end: Date.now() }) {
        refreshCards(hours);
        const current = ++historyRequest;
        let plugs;
        try {
            const temperaturesPromise = getJSON(`/api/history?hours=${hours}`).catch(() => []);
            plugs = await getJSON(`/api/plugs/history?hours=${hours}`);
            if (current !== historyRequest) return;
            renderLanes(plugs, bounds);
            const [temperatures, ready] = await Promise.all([temperaturesPromise, window.chartsReady]);
            if (current !== historyRequest) return;
            if (ready === false || typeof Chart === 'undefined') throw new Error('Chart unavailable');
            const theme = getThemeColors();
            history = { plugs, hours, bounds, theme };
            const config = options(hours, bounds, theme);
            config.scales.y.suggestedMin = 45;
            config.scales.y.suggestedMax = 75;
            if (temperatureChart) temperatureChart.destroy();
            temperatureChart = new Chart(document.getElementById('activityChart').getContext('2d'), {
                type: 'line', options: config,
                data: { datasets: [
                    ['temp_f', 'Canopy °F', '#f59e0b'], ['root_temp_f', 'Root °F', '#84cc16'],
                ].map(([field, label, color]) => ({
                    label, borderColor: color,
                    data: temperatures.map(r => ({ x: r.timestamp, y: r[field] })),
                    spanGaps: false, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2,
                })) },
                plugins: [{
                    id: 'alignEquipmentLanes',
                    afterLayout(chart) {
                        lanes.style.marginLeft = `${chart.chartArea.left}px`;
                        lanes.style.marginRight = `${chart.width - chart.chartArea.right}px`;
                    },
                }],
            });
            activityStatus.hidden = true;
            renderPower();
        } catch (_) {
            if (current !== historyRequest) return;
            history = null;
            if (temperatureChart) temperatureChart.destroy();
            if (powerChart) powerChart.destroy();
            temperatureChart = powerChart = null;
            if (!plugs) {
                lanes.replaceChildren();
                document.getElementById('activity-periods').replaceChildren();
                document.getElementById('activity-selection').textContent = '';
            }
            activityStatus.textContent = 'History chart unavailable. Retrying on the next refresh.';
            activityStatus.hidden = false;
            powerStatus.textContent = 'Power history unavailable.';
        }
    };

    window.loadPowerChart(currentRangeHours);
    setInterval(() => refreshCards(currentRangeHours), 30000);
    setInterval(markStale, 5000);
})();
