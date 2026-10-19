// Optional equipment monitoring. Device names are inserted as text, never HTML.
(() => {
    let chart = null;
    let activityChartInstance = null;
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

    function formatDuration(ms) {
        const mins = Math.max(1, Math.round(ms / 60000));
        if (mins < 60) return `${mins}m`;
        const hours = (mins / 60).toFixed(1);
        return `${hours.endsWith('.0') ? hours.slice(0, -2) : hours}h`;
    }

    function drawRoundRect(ctx, x, y, w, h, r) {
        if (ctx.roundRect) {
            ctx.beginPath();
            ctx.roundRect(x, y, w, h, r);
        } else {
            ctx.beginPath();
            ctx.rect(x, y, w, h);
        }
    }

    function markStale() {
        if (!cards) return;
        for (const card of cards.children) {
            if (card.dataset.timestamp && Date.now() - Date.parse(card.dataset.timestamp) > card.dataset.staleMs) {
                card.querySelector('.plug-state').textContent = 'Unknown · readings stale';
                card.querySelector('.plug-watts').textContent = '— W';
                card.querySelector('.plug-relay').textContent = 'Plug: Unknown';
            }
        }
    }

    async function refreshCards() {
        if (!cards) return;
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
            cards.replaceChildren();
            line(cards, 'Equipment data unavailable. Retrying shortly.', 'muted small');
        }
    }

    function setupActivityCanvasHover(canvas, plugs) {
        if (!canvas || canvas.dataset.hoverAttached) return;
        canvas.dataset.hoverAttached = 'true';
        canvas.addEventListener('mousemove', e => {
            if (!activityChartInstance || !activityChartInstance.chartArea) return;
            const chartArea = activityChartInstance.chartArea;
            const xScale = activityChartInstance.scales.x;
            if (!xScale) return;
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            const numPlugs = plugs.length;
            const trackHeight = 15;
            const trackGap = 6;
            const totalTracksHeight = numPlugs * trackHeight + (numPlugs - 1) * trackGap;
            const dividerY = chartArea.bottom - totalTracksHeight - 10;

            const noteEl = document.getElementById('activity-history-note');
            let found = null;

            if (x >= chartArea.left && x <= chartArea.right && y >= dividerY + 8 && y <= chartArea.bottom) {
                const hoverTime = xScale.getValueForPixel(x);
                plugs.forEach((plug, i) => {
                    const trackY = dividerY + 10 + i * (trackHeight + trackGap);
                    if (y >= trackY && y <= trackY + trackHeight) {
                        const intervals = plug.activity || [];
                        for (const inv of intervals) {
                            const t1 = Date.parse(inv.start);
                            const t2 = Date.parse(inv.end);
                            if (hoverTime >= t1 && hoverTime <= t2) {
                                const dur = formatDuration(t2 - t1);
                                const state = inv.active ? 'Active' : 'Idle';
                                const d1 = new Date(t1).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                                const d2 = new Date(t2).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                                found = `${plug.name}: ${state} (${d1} – ${d2}, ${dur})`;
                                break;
                            }
                        }
                    }
                });
            }

            if (found) {
                canvas.style.cursor = 'pointer';
                if (noteEl) noteEl.textContent = found;
            } else {
                canvas.style.cursor = 'default';
                if (noteEl && noteEl.dataset.defaultText !== undefined) {
                    noteEl.textContent = noteEl.dataset.defaultText;
                }
            }
        });
        canvas.addEventListener('mouseleave', () => {
            const noteEl = document.getElementById('activity-history-note');
            if (noteEl && noteEl.dataset.defaultText !== undefined) {
                noteEl.textContent = noteEl.dataset.defaultText;
            }
            canvas.style.cursor = 'default';
        });
    }

    window.loadPowerChart = async function(hours, bounds) {
        const current = ++requestId;
        const now = bounds?.end || Date.now();
        const minTime = bounds?.start || (now - hours * 3600000);

        try {
            const [plugs, temperatures, ready] = await Promise.all([
                getJSON(`/api/plugs/history?hours=${hours}`),
                getJSON(`/api/history?hours=${hours}`).catch(() => []),
                window.chartsReady,
            ]);
            if (current !== requestId) return;
            if (ready === false || typeof Chart === 'undefined') throw new Error('Chart unavailable');

            const theme = typeof getThemeColors === 'function' ? getThemeColors() : {
                textColor: '#94a3b8', gridColor: 'rgba(255, 255, 255, 0.05)',
            };
            const isDark = document.documentElement ? document.documentElement.getAttribute('data-theme') !== 'light' : true;

            // ── 1. Detail Watts Chart (inside #power-details) ─────────────
            const powerCanvas = document.getElementById('powerChart');
            if (powerCanvas) {
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
                chart = new Chart(powerCanvas.getContext('2d'), {
                    type: 'line', data: { datasets }, options: {
                        responsive: true, maintainAspectRatio: false,
                        interaction: { intersect: false, mode: 'index' },
                        plugins: { legend: { labels: { color: theme.textColor } } },
                        scales: {
                            x: { type: 'time', min: minTime, max: now,
                                ticks: { color: theme.textColor, maxTicksLimit: 7, maxRotation: 0 } },
                            power: { position: 'left', min: 0, ticks: { color: theme.textColor },
                                title: { display: true, text: 'Watts', color: theme.textColor } },
                            temperature: { position: 'right', grid: { drawOnChartArea: false },
                                ticks: { color: theme.textColor },
                                title: { display: true, text: '°F', color: theme.textColor } },
                        },
                    },
                });
                if (status) {
                    status.textContent = plugs.some(p => p.readings.some(r => Number.isFinite(r.power_w))) ? '' : 'No power readings yet.';
                }
            }

            // ── 2. Primary Timeline & Temperature Chart (#activityChart) ──
            const activityCanvas = document.getElementById('activityChart');
            if (activityCanvas) {
                if (activityChartInstance) activityChartInstance.destroy();
                activityChartInstance = null;

                const activityStatus = document.getElementById('activity-chart-status');
                const noteEl = document.getElementById('activity-history-note');

                // Explain startup / early history window
                const earliestRecorded = plugs
                    .map(p => p.recorded_since ? Date.parse(p.recorded_since) : NaN)
                    .filter(Number.isFinite);
                if (earliestRecorded.length > 0 && noteEl) {
                    const firstRecordedMs = Math.min(...earliestRecorded);
                    if (firstRecordedMs > minTime) {
                        const firstDt = new Date(firstRecordedMs);
                        const durStr = formatDuration(now - firstRecordedMs);
                        const rangeLabel = hours === 1 ? '1h' : hours < 72 ? `${hours}h` : `${Math.round(hours / 24)}d`;
                        const msg = `Monitoring started at ${firstDt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (${durStr} recorded in this ${rangeLabel} view).`;
                        noteEl.textContent = msg;
                        noteEl.dataset.defaultText = msg;
                    } else {
                        noteEl.textContent = '';
                        noteEl.dataset.defaultText = '';
                    }
                } else if (noteEl) {
                    noteEl.textContent = '';
                    noteEl.dataset.defaultText = '';
                }

                if (activityStatus) {
                    activityStatus.hidden = true;
                    activityStatus.textContent = '';
                }

                const hasSensorMeasurements = temperatures.some(r =>
                    Number.isFinite(r.temp_f) || Number.isFinite(r.root_temp_f));

                const activityPlugin = {
                    id: 'activityTracksPlugin',
                    afterDraw(c) {
                        const ctx = c.ctx;
                        const chartArea = c.chartArea;
                        const xScale = c.scales.x;
                        if (!chartArea || !xScale) return;

                        const numPlugs = plugs.length;
                        if (numPlugs === 0) return;

                        const trackHeight = 15;
                        const trackGap = 6;
                        const totalTracksHeight = numPlugs * trackHeight + (numPlugs - 1) * trackGap;
                        const dividerY = chartArea.bottom - totalTracksHeight - 10;

                        // Offline watermark if temperature sensors have no data
                        if (!hasSensorMeasurements) {
                            ctx.save();
                            ctx.fillStyle = theme.textColor;
                            ctx.font = '500 11px system-ui, -apple-system, sans-serif';
                            ctx.textAlign = 'center';
                            ctx.textBaseline = 'middle';
                            const tempCenterY = (chartArea.top + dividerY) / 2;
                            ctx.fillText('Canopy & root sensors offline · Equipment activity tracked below', (chartArea.left + chartArea.right) / 2, tempCenterY);
                            ctx.restore();
                        }

                        // Subtle divider line
                        ctx.save();
                        ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)';
                        ctx.lineWidth = 1;
                        ctx.beginPath();
                        ctx.moveTo(chartArea.left, dividerY);
                        ctx.lineTo(chartArea.right, dividerY);
                        ctx.stroke();
                        ctx.restore();

                        // Draw each equipment track
                        plugs.forEach((plug, i) => {
                            const trackY = dividerY + 10 + i * (trackHeight + trackGap);
                            const trackWidth = chartArea.right - chartArea.left;

                            const isLight = plug.role === 'light' || plug.name.toLowerCase().includes('light');
                            const isHeater = plug.role === 'heater' || plug.name.toLowerCase().includes('heater');
                            const activeColor = isLight ? '#f59e0b' : isHeater ? '#f97316' : '#38bdf8';
                            const icon = isLight ? '💡' : isHeater ? '🔥' : '⚡';

                            // Base track background
                            ctx.save();
                            ctx.fillStyle = isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.04)';
                            drawRoundRect(ctx, chartArea.left, trackY, trackWidth, trackHeight, 3);
                            ctx.fill();

                            // Active / Idle intervals
                            const intervals = plug.activity || [];
                            intervals.forEach(inv => {
                                const t1 = Date.parse(inv.start);
                                const t2 = Date.parse(inv.end);
                                if (isNaN(t1) || isNaN(t2) || t2 <= minTime || t1 >= now) return;

                                const x1 = Math.max(chartArea.left, xScale.getPixelForValue(t1));
                                const x2 = Math.min(chartArea.right, xScale.getPixelForValue(t2));
                                const w = Math.max(2, x2 - x1);

                                ctx.fillStyle = inv.active ? activeColor : (isDark ? '#334155' : '#cbd5e1');
                                drawRoundRect(ctx, x1, trackY, w, trackHeight, Math.min(3, w / 2));
                                ctx.fill();
                            });

                            // Device label on left side
                            ctx.fillStyle = theme.textColor;
                            ctx.font = '600 10px system-ui, -apple-system, sans-serif';
                            ctx.textAlign = 'right';
                            ctx.textBaseline = 'middle';
                            ctx.fillText(`${icon} ${plug.name}`, chartArea.left - 8, trackY + trackHeight / 2);
                            ctx.restore();
                        });
                    },
                };

                const timeConfig = {
                    unit: hours <= 1 ? 'minute' : hours > 24 ? 'day' : 'hour',
                    displayFormats: {
                        minute: 'h:mm a',
                        hour: 'ha',
                        day: 'MMM d',
                        week: 'MMM d',
                    },
                    tooltipFormat: 'MMM d, h:mm a',
                };

                activityChartInstance = new Chart(activityCanvas.getContext('2d'), {
                    type: 'line',
                    data: {
                        datasets: [
                            {
                                label: 'Canopy °F',
                                data: temperatures.map(r => ({ x: r.timestamp, y: r.temp_f })),
                                borderColor: '#f59e0b',
                                backgroundColor: 'rgba(245, 158, 11, 0.08)',
                                fill: true,
                                tension: 0.25,
                                yAxisID: 'y',
                                pointRadius: 0,
                                pointHoverRadius: 4,
                                borderWidth: 2,
                                spanGaps: false,
                            },
                            {
                                label: 'Root °F',
                                data: temperatures.map(r => ({ x: r.timestamp, y: r.root_temp_f })),
                                borderColor: '#84cc16',
                                borderDash: [4, 4],
                                fill: false,
                                tension: 0.25,
                                yAxisID: 'y',
                                pointRadius: 0,
                                pointHoverRadius: 4,
                                borderWidth: 2,
                                spanGaps: false,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { intersect: false, mode: 'index' },
                        layout: { padding: { left: 45, right: 10 } },
                        plugins: {
                            legend: {
                                position: 'top',
                                labels: {
                                    color: theme.textColor,
                                    usePointStyle: true,
                                    boxWidth: 8,
                                    padding: 12,
                                    font: { size: 11, weight: '500' },
                                },
                            },
                        },
                        scales: {
                            x: {
                                type: 'time',
                                min: minTime,
                                max: now,
                                time: timeConfig,
                                grid: { display: false },
                                ticks: { color: theme.textColor, maxTicksLimit: 7, maxRotation: 0, font: { size: 10 } },
                            },
                            y: {
                                position: 'left',
                                title: { display: true, text: '°F', color: theme.textColor, font: { size: 10 } },
                                suggestedMin: 45,
                                suggestedMax: 75,
                                grid: { color: theme.gridColor },
                                ticks: {
                                    color: theme.textColor,
                                    font: { size: 10 },
                                    callback: function(val) {
                                        if (this.chart && this.chart.chartArea) {
                                            const pixel = this.getPixelForValue(val);
                                            const numPlugs = plugs.length || 0;
                                            const dividerY = this.chart.chartArea.bottom - (numPlugs * 21 + 10);
                                            if (pixel >= dividerY - 4) return null;
                                        }
                                        return val;
                                    },
                                },
                                afterDataLimits(scale) {
                                    const numPlugs = plugs.length || 0;
                                    if (numPlugs === 0) return;
                                    const span = (scale.max - scale.min) || 20;
                                    scale.min = scale.max - span / 0.65;
                                },
                            },
                        },
                    },
                    plugins: [activityPlugin],
                });

                setupActivityCanvasHover(activityCanvas, plugs);
            }
        } catch (_) {
            if (current !== requestId) return;
            if (chart) chart.destroy();
            chart = null;
            if (activityChartInstance) activityChartInstance.destroy();
            activityChartInstance = null;
            if (status) status.textContent = 'Power history unavailable. Retrying on the next chart refresh.';
            const activityStatus = document.getElementById('activity-chart-status');
            if (activityStatus) {
                activityStatus.textContent = 'Equipment history unavailable. Retrying shortly.';
                activityStatus.hidden = false;
            }
        }
    };

    refreshCards();
    if (typeof currentRangeHours !== 'undefined') {
        window.loadPowerChart(currentRangeHours);
    } else {
        window.loadPowerChart(24);
    }
    setInterval(refreshCards, 30000);
    setInterval(markStale, 5000);
})();
