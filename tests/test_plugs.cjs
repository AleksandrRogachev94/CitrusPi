// Run with node --test tests/test_plugs.cjs (no npm dependencies).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const now = Date.now();
const iso = seconds => new Date(now - seconds * 1000).toISOString();
const series = () => ({
    id: 'light', name: 'Grow light', role: 'light', recorded_since: iso(120),
    activity: [
        { start: iso(120), end: iso(60), active: true },
        { start: iso(60), end: iso(0), active: false },
    ],
    readings: [{ timestamp: iso(120), power_w: 100, active: true },
        { timestamp: iso(60), power_w: null, active: null },
        { timestamp: iso(0), power_w: 0, active: false }],
});
const plug = overrides => ({
    name: 'Heater <script>alert(1)</script>', monitoring: true, stale: false, stale_after_sec: 180,
    latest: { timestamp: iso(0), relay_on: true, power_w: 0, active: false, energy_today_kwh: 0.5, status: 'ok' },
    summary: { estimated_kwh: 0.5, runtime_hours: 2, duty_cycle_pct: 25, coverage_pct: 50 },
    ...overrides,
});

function harness(electricity = false) {
    function element() {
        const classes = new Set();
        return {
            children: [], dataset: {}, textContent: '', className: '', open: false, hidden: false,
            style: { setProperty(key, value) { this[key] = value; } }, events: {},
            setAttribute(key, value) { this[key] = value; },
            classList: {
                toggle(name, value) { if (value) classes.add(name); else classes.delete(name); },
                contains: name => classes.has(name),
            },
            addEventListener(event, callback) { this.events[event] = callback; },
            appendChild(child) { this.children.push(child); },
            replaceChildren(...children) { this.children = children; },
            querySelector(selector) {
                for (const child of this.children) {
                    if (child.className.split(' ').includes(selector.slice(1))) return child;
                    const nested = child.querySelector(selector);
                    if (nested) return nested;
                }
            },
            getContext() { return {}; },
        };
    }
    const nodes = Object.fromEntries(['plug-cards', 'plug-details', 'power-details',
        'plug-totals', 'plug-total-watts', 'plug-total-energy', 'equipment-range',
        'power-chart-status', 'powerChart', 'activityChart', 'activity-chart-status',
        'activity-history-note', 'activity-selection', 'activity-periods', 'equipment-lanes',
        'temperature-history', 'equipment-summary', 'runtime-history', 'power-history'].map(id => [id, element()]));
    for (const id of ['temperature-history', 'equipment-summary', 'runtime-history', 'power-history']) {
        const content = element();
        content.className = 'range-content';
        nodes[id].appendChild(content);
    }
    if (electricity) nodes['electricity-cost'] = element();
    const intervals = {};
    const charts = [];
    const requests = [];
    let respond = async url => url.startsWith('/api/plugs?') ? [plug()] : url.startsWith('/api/plugs/history') ? [series()] : [];
    const context = vm.createContext({
        document: { getElementById: id => nodes[id], createElement: element },
        fetch: async url => {
            requests.push(url);
            return { ok: true, json: () => respond(url) };
        },
        window: {
            chartsReady: Promise.resolve(true),
            chartHistoryLoad: {
                hours: 24, bounds: { start: now - 24 * 3600000, end: now },
                readings: Promise.resolve([{ timestamp: iso(0), temp_f: 60, root_temp_f: 58 }]),
            },
        },
        getThemeColors: () => ({ textColor: 'white', gridColor: 'gray' }),
        currentRangeHours: 24, Date,
        Chart: function(ctx, config) {
            this.config = config;
            this.destroy = () => { this.destroyed = true; };
            this.width = 800;
            this.chartArea = { left: 57, right: 775 };
            for (const plugin of config.plugins || []) plugin.afterLayout?.(this);
            charts.push(this);
        },
        setInterval: (fn, ms) => { intervals[ms] = fn; },
    });
    const dashboard = fs.readFileSync(require.resolve('../src/citruspi/templates/dashboard.html'), 'utf8');
    vm.runInContext(dashboard.match(/function setRangeLoading\(id, loading\) \{[\s\S]*?\n\}/)[0], context);
    vm.runInContext(fs.readFileSync(require.resolve('../src/citruspi/static/plugs.js'), 'utf8'), context);
    return {
        nodes, intervals, charts, context, requests, respond: fn => { respond = fn; },
        openPower() { nodes['power-details'].open = true; nodes['power-details'].events.toggle(); },
    };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('each equipment section loads independently and waits for shared sensor history', async () => {
    const h = harness();
    await flush();
    let releaseSummary, releaseHistory, releaseSensors;
    h.respond(url => new Promise(resolve => {
        if (url.startsWith('/api/plugs?')) releaseSummary = resolve;
        else releaseHistory = resolve;
    }));
    const sensors = new Promise(resolve => { releaseSensors = resolve; });
    const loading = h.context.window.loadPowerChart(720, undefined, sensors);
    await flush();
    for (const id of ['equipment-summary', 'runtime-history', 'temperature-history', 'power-history']) {
        assert.equal(h.nodes[id]['aria-busy'], 'true');
        assert.equal(h.nodes[id].classList.contains('is-loading'), true);
        assert.equal(h.nodes[id].children[0].inert, true);
    }
    releaseSummary([plug()]);
    await flush();
    assert.equal(h.nodes['equipment-summary']['aria-busy'], 'false');
    assert.equal(h.nodes['runtime-history']['aria-busy'], 'false');
    assert.equal(h.nodes['temperature-history']['aria-busy'], 'true');
    releaseHistory([series()]);
    await flush();
    assert.equal(h.nodes['power-history']['aria-busy'], 'true');
    releaseSensors([]);
    await loading;
    assert.equal(h.nodes['temperature-history']['aria-busy'], 'false');
    assert.equal(h.nodes['power-history']['aria-busy'], 'false');
});

test('equipment failures clear every loader and show unavailable status', async () => {
    const h = harness();
    await flush();
    h.respond(async () => { throw new Error('offline'); });
    await h.context.window.loadPowerChart(12);
    await flush();
    for (const id of ['equipment-summary', 'runtime-history', 'temperature-history', 'power-history']) {
        assert.equal(h.nodes[id]['aria-busy'], 'false');
        assert.equal(h.nodes[id].children[0].inert, false);
    }
    assert.match(h.nodes['equipment-range'].textContent, /unavailable/);
    assert.match(h.nodes['activity-chart-status'].textContent, /unavailable/);
    assert.match(h.nodes['power-chart-status'].textContent, /unavailable/);
});

test('old equipment requests cannot dismiss the new range loaders', async () => {
    const h = harness();
    await flush();
    const pending = {};
    h.respond(url => new Promise(resolve => { pending[url] = resolve; }));
    const old = h.context.window.loadPowerChart(24);
    const latest = h.context.window.loadPowerChart(720);
    await flush();
    pending['/api/plugs?hours=24']([plug()]);
    pending['/api/plugs/history?hours=24']([series()]);
    await old;
    for (const id of ['equipment-summary', 'runtime-history', 'temperature-history', 'power-history']) {
        assert.equal(h.nodes[id]['aria-busy'], 'true');
    }
    pending['/api/plugs?hours=720']([]);
    pending['/api/plugs/history?hours=720']([]);
    await latest;
    await flush();
    for (const id of ['equipment-summary', 'runtime-history', 'temperature-history', 'power-history']) {
        assert.equal(h.nodes[id]['aria-busy'], 'false');
    }
});

test('equipment reuses sensor history on startup and range changes without fetching it again', async () => {
    const h = harness();
    await flush();
    assert.equal(h.charts[0].config.data.datasets[0].data[0].y, 60);
    assert.equal(h.charts[0].config.data.datasets[1].data[0].y, 58);
    await h.context.window.loadPowerChart(12, undefined, Promise.resolve([
        { timestamp: iso(0), temp_f: 62, root_temp_f: 59 },
    ]));
    assert.equal(h.charts.at(-1).config.data.datasets[0].data[0].y, 62);
    assert.equal(h.charts.at(-1).config.data.datasets[1].data[0].y, 59);
    assert.ok(h.requests.includes('/api/plugs?hours=12'));
    assert.ok(h.requests.includes('/api/plugs/history?hours=12'));
    assert.equal(h.requests.filter(url => url.startsWith('/api/history?')).length, 0);
});

test('global range drives usage and combined costs; missing data never becomes free', async () => {
    const h = harness(true);
    await flush();
    const requests = [];
    h.respond(async url => {
        requests.push(url);
        if (url.startsWith('/api/plugs?')) return [
            plug({ summary: { estimated_cost_usd: 1.25, estimated_kwh: 3, runtime_hours: 1, coverage_pct: 50 } }),
            plug({ summary: { estimated_cost_usd: 0 } }),
            plug({ summary: { estimated_cost_usd: null } }),
        ];
        return [];
    });
    await h.context.window.loadPowerChart(168);
    await flush();
    assert.ok(requests.includes('/api/plugs?hours=168'));
    assert.ok(requests.includes('/api/plugs/history?hours=168'));
    assert.equal(h.nodes['electricity-cost'].textContent, '~$1.25 (partial)');
    assert.match(h.nodes['equipment-range'].textContent, /last 7d/);
    assert.equal(h.nodes['plug-total-energy'].textContent, '~3.000 (partial)');
    h.respond(async url => url.startsWith('/api/plugs?') ? [plug({ summary: { estimated_cost_usd: null } })] : []);
    await h.context.window.loadPowerChart(1);
    await flush();
    assert.equal(h.nodes['electricity-cost'].textContent, '—');
    assert.match(h.nodes['equipment-range'].textContent, /last 1h/);
    h.respond(async () => { throw new Error('offline'); });
    await h.intervals[30000]();
    assert.equal(h.nodes['plug-totals'].hidden, true);
    assert.equal(h.nodes['electricity-cost'].textContent, '—');
});

test('a late cost response cannot replace the newly selected range', async () => {
    const h = harness(true);
    await flush();
    let release;
    h.respond(url => url === '/api/plugs?hours=24' ? new Promise(resolve => { release = resolve; }) : []);
    await h.context.window.loadPowerChart(24);
    h.respond(async url => url.startsWith('/api/plugs?') ? [plug({ summary: { estimated_cost_usd: 0 } })] : []);
    await h.context.window.loadPowerChart(72);
    await flush();
    release([plug({ summary: { estimated_cost_usd: 999 } })]);
    await flush();
    assert.equal(h.nodes['electricity-cost'].textContent, '~$0.00');
});

test('equipment rows show live power and selected-range energy together; runtime stays in details', async () => {
    const h = harness();
    await flush();
    const card = h.nodes['plug-cards'].children[0];
    assert.equal(card.querySelector('.plug-name').textContent, 'Heater <script>alert(1)</script>');
    assert.equal(card.querySelector('.plug-state').textContent, 'Idle');
    assert.equal(card.querySelector('.plug-watts').textContent, '0');
    assert.equal(card.children.length, 3);
    assert.equal(card.querySelector('.metric-value').textContent, '0');
    assert.equal(card.children[2].children[0].textContent, '~0.500');
    assert.equal(card.children[2].children[1].textContent, '50% coverage');
    assert.equal(h.nodes['plug-total-watts'].textContent, '0');
    assert.equal(h.nodes['plug-total-energy'].textContent, '~0.500');
    const usage = h.nodes['plug-details'].children[0];
    assert.ok(usage.children.some(c => c.textContent.includes('~2 h active in selected range')));
    assert.ok(usage.children.some(c => c.textContent.includes('outlet enabled')));
});

test('stale readings lose live state without waiting for another API response', async () => {
    const h = harness();
    await flush();
    const card = h.nodes['plug-cards'].children[0];
    card.dataset.timestamp = iso(181);
    h.intervals[5000]();
    assert.equal(card.querySelector('.plug-state').textContent, 'Unknown · readings stale');
    assert.equal(card.querySelector('.plug-watts').textContent, '—');
    assert.equal(h.nodes['plug-total-watts'].textContent, '—');
    assert.equal(h.nodes['plug-total-energy'].textContent, '~0.500');
});

test('totals combine plug measurements and mark missing live values without losing history', async () => {
    const h = harness(true);
    await flush();
    h.respond(async url => url.startsWith('/api/plugs?') ? [
        plug({ latest: { timestamp: iso(0), power_w: 71.1 },
            summary: { estimated_kwh: 0.885, estimated_cost_usd: 0.16, coverage_pct: 98 } }),
        plug({ latest: { timestamp: iso(0), power_w: 0 },
            summary: { estimated_kwh: 0.277, estimated_cost_usd: 0.05, coverage_pct: 98 } }),
    ] : []);
    await h.intervals[30000]();
    assert.equal(h.nodes['plug-total-watts'].textContent, '71.1');
    assert.equal(h.nodes['plug-total-energy'].textContent, '~1.162');
    assert.equal(h.nodes['electricity-cost'].textContent, '~$0.21');
    assert.equal(h.nodes['plug-cards'].children[1].querySelector('.plug-cost').textContent, '~$0.05');
    h.nodes['plug-cards'].children[0].dataset.timestamp = iso(181);
    h.intervals[5000]();
    assert.equal(h.nodes['plug-total-watts'].textContent, '0 (partial)');
    assert.equal(h.nodes['plug-total-energy'].textContent, '~1.162');
    assert.equal(h.nodes['electricity-cost'].textContent, '~$0.21');
});

test('API failure clears current claims and usage details, and recovers on the next poll', async () => {
    const h = harness();
    await flush();
    h.respond(async () => { throw new Error('offline'); });
    await h.intervals[30000]();
    assert.match(h.nodes['plug-cards'].children[0].children[0].textContent, /unavailable/);
    assert.equal(h.nodes['plug-totals'].hidden, true);
    assert.equal(h.nodes['plug-details'].children.length, 0);
    h.respond(async () => [plug()]);
    await h.intervals[30000]();
    assert.equal(h.nodes['plug-cards'].children[0].querySelector('.plug-state').textContent, 'Idle');
});

test('paused, stale, and failed readings never imply equipment is off', async () => {
    const h = harness();
    await flush();
    for (const [data, expected] of [
        [plug({ monitoring: false }), 'Monitoring paused'],
        [plug({ stale: true }), 'Unknown · readings stale'],
        [plug({ latest: { status: 'auth_error' } }), 'Authentication needed'],
    ]) {
        h.respond(async () => [data]);
        await h.intervals[30000]();
        assert.equal(h.nodes['plug-cards'].children[0].querySelector('.plug-state').textContent, expected);
    }
});

test('power chart renders only when opened and excludes duplicate temperature axes', async () => {
    const h = harness();
    await flush();
    assert.equal(h.charts.length, 1);
    h.openPower();
    const chart = h.charts.at(-1).config;
    assert.equal(chart.data.datasets[0].data[1].y, null);
    assert.equal(chart.data.datasets[0].spanGaps, false);
    assert.equal(chart.data.datasets[0].stepped, true);
    assert.equal(chart.data.datasets[0].pointRadius, 0);
    assert.equal(chart.data.datasets[0].pointHoverRadius, 4);
    assert.equal(chart.data.datasets.length, 1);
    assert.equal(chart.options.scales.y.title, undefined);
    await h.context.window.loadPowerChart(72);
    assert.equal(h.charts.at(-1).config.options.scales.x.time.unit, 'day');
});

test('activity is visible without sensors and has no misleading current-state badges', async () => {
    const h = harness();
    await flush();
    const lane = h.nodes['equipment-lanes'].children[0];
    assert.equal(lane.querySelector('.equipment-lane-title').textContent, 'Grow light');
    const segments = lane.children[1].children;
    assert.equal(segments.length, 2); // Active and idle intervals; no duplicate marker.
    assert.equal(segments[0].className, 'lane-segment active');
    assert.equal(segments[1].className, 'lane-segment idle');
    assert.match(segments[0].title, /Active/);
    assert.equal(h.nodes['activity-chart-status'].hidden, true);
    assert.match(h.nodes['activity-history-note'].textContent, /Recorded since/);
    await h.context.window.loadPowerChart(24, undefined, Promise.resolve([]));
    assert.equal(h.charts.at(-1).config.data.datasets[0].data.length, 0);
});

test('lanes align to actual chart bounds, rather than fixed CSS margins', async () => {
    const h = harness();
    await flush();
    assert.equal(h.nodes['equipment-lanes'].style.marginLeft, '57px');
    assert.equal(h.nodes['equipment-lanes'].style.marginRight, '25px');
});

test('activity survives a sensor API or chart library failure', async () => {
    const h = harness();
    await flush();
    h.respond(async url => {
        if (url.startsWith('/api/plugs/history')) return [series()];
        throw new Error('sensors unavailable');
    });
    await h.context.window.loadPowerChart(1, undefined, Promise.reject(new Error('sensors unavailable')));
    assert.equal(h.nodes['equipment-lanes'].children.length, 1);
    assert.equal(h.nodes['activity-chart-status'].hidden, true);
    h.context.window.chartsReady = Promise.resolve(false);
    await h.context.window.loadPowerChart(1);
    assert.equal(h.nodes['equipment-lanes'].children.length, 1);
    assert.match(h.nodes['activity-chart-status'].textContent, /unavailable/);
});

test('single observation is visible without inventing an active duration', async () => {
    const h = harness();
    await flush();
    h.respond(async () => [{ ...series(), activity: [], readings: [{ timestamp: iso(30), active: true }] }]);
    await h.context.window.loadPowerChart(1);
    const track = h.nodes['equipment-lanes'].children[0].children[1];
    assert.equal(track.children.length, 1);
    assert.equal(track.children[0].className, 'lane-marker active');
    assert.equal(track.children[0].style.width, undefined);
});

test('intervals are clipped to the time range and missing states stay unknown', async () => {
    const h = harness();
    await flush();
    h.respond(async () => [{ ...series(), readings: [], activity: [
        { start: iso(100), end: iso(80), active: true },
        { start: iso(55), end: iso(50), active: null },
    ] }]);
    await h.context.window.loadPowerChart(1, { start: now - 90000, end: now });
    const segments = h.nodes['equipment-lanes'].children[0].children[1].children;
    assert.equal(segments.length, 1);
    assert.equal(segments[0].style.left, '0%');
    assert.ok(Math.abs(parseFloat(segments[0].style.width) - 100 / 9) < 0.01);
});

test('an older range response cannot replace the latest chart or lanes', async () => {
    const h = harness();
    await flush();
    let release;
    h.respond(url => url.includes('hours=24') && url.startsWith('/api/plugs/history') ? new Promise(resolve => { release = resolve; }) : []);
    const old = h.context.window.loadPowerChart(24);
    await flush();
    await h.context.window.loadPowerChart(72);
    const newest = h.charts.at(-1);
    release([series()]);
    await old;
    assert.equal(h.charts.at(-1), newest);
    assert.equal(h.nodes['equipment-lanes'].children.length, 0);
});

test('an older cards response cannot replace a newer state', async () => {
    const h = harness();
    await flush();
    let release;
    h.respond(() => new Promise(resolve => { release = resolve; }));
    const old = h.intervals[30000]();
    await flush();
    h.respond(async () => [plug({ stale: true })]);
    await h.intervals[30000]();
    release([plug()]);
    await old;
    assert.equal(h.nodes['plug-cards'].children[0].querySelector('.plug-state').textContent, 'Unknown · readings stale');
});

test('failed history requests clear old chart and lanes', async () => {
    const h = harness();
    await flush();
    const old = h.charts[0];
    h.respond(async () => { throw new Error('offline'); });
    await h.context.window.loadPowerChart(24);
    assert.equal(old.destroyed, true);
    assert.equal(h.nodes['equipment-lanes'].children.length, 0);
    assert.equal(h.nodes['activity-periods'].children.length, 0);
    assert.equal(h.nodes['activity-selection'].textContent, '');
    assert.match(h.nodes['activity-chart-status'].textContent, /unavailable/);
});

test('period buttons reveal duration on click or keyboard focus without sensors', async () => {
    const h = harness();
    await flush();
    const lane = h.nodes['equipment-lanes'].children[0];
    const active = lane.children[1].children[0];
    assert.equal(active.type, 'button');
    assert.match(lane.children[0].children[1].textContent, /~1 min active in view/);
    active.events.click();
    assert.match(h.nodes['activity-selection'].textContent, /Grow light · Active · .* · ~1 min/);
    const idle = lane.children[1].children[1];
    idle.events.focus();
    assert.match(h.nodes['activity-selection'].textContent, /Idle · .* · ~1 min/);
});

test('active period list excludes idle time and caps long histories at the latest 20', async () => {
    const h = harness();
    await flush();
    const group = h.nodes['activity-periods'].children[0];
    assert.equal(group.children[1].children.length, 1);
    assert.equal(group.children[1].children[0].children[1].textContent, '~1 min');
    h.respond(async () => [{ ...series(), activity: Array.from({ length: 25 }, (_, i) => ({
        start: iso((25 - i) * 120), end: iso((25 - i) * 120 - 60), active: true,
    })) }]);
    await h.context.window.loadPowerChart(24);
    const recent = h.nodes['activity-periods'].children[0];
    assert.equal(recent.children[1].children.length, 20);
    assert.match(recent.children[2].textContent, /Latest 20 of 25/);
});

test('usage formats hours as readable minutes and missing values stay unknown', async () => {
    const h = harness();
    await flush();
    h.respond(async () => [plug({ summary: { runtime_hours: 1.38, estimated_kwh: 0.843, coverage_pct: 64 } })]);
    await h.intervals[30000]();
    const usage = h.nodes['plug-details'].children[0];
    assert.equal(usage.children[1].textContent, '~1 h 23 min active in selected range');
    assert.equal(h.nodes['plug-cards'].children[0].children[2].children[1].textContent, '64% coverage');
    h.respond(async () => [plug({ summary: { runtime_hours: null, estimated_kwh: null, coverage_pct: 0 } })]);
    await h.intervals[30000]();
    assert.equal(h.nodes['plug-details'].children[0].children[1].textContent, '— active in selected range');
});

test('a single observation does not claim a run duration', async () => {
    const h = harness();
    await flush();
    h.respond(async () => [{ ...series(), activity: [], readings: [{ timestamp: iso(30), active: true }] }]);
    await h.context.window.loadPowerChart(1);
    const lane = h.nodes['equipment-lanes'].children[0];
    assert.equal(lane.children[0].children[1].textContent, 'No observed duration');
    lane.children[1].children[0].events.click();
    assert.match(h.nodes['activity-selection'].textContent, /single reading/);
    assert.match(h.nodes['activity-periods'].children[0].children[2].textContent, /No activity readings/);
});

test('inspected durations respect the selected window and retain subminute precision', async () => {
    const h = harness();
    await flush();
    h.respond(async () => [{ ...series(), readings: [], activity: [{ start: iso(100), end: iso(80), active: true }] }]);
    await h.context.window.loadPowerChart(1, { start: now - 90000, end: now });
    const lane = h.nodes['equipment-lanes'].children[0];
    assert.equal(lane.children[0].children[1].textContent, '<1 min active in view');
    lane.children[1].children[0].events.focus();
    assert.match(h.nodes['activity-selection'].textContent, /<1 min/);
});
