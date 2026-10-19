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

function harness() {
    function element() {
        return {
            children: [], dataset: {}, textContent: '', className: '', open: false, hidden: false,
            style: { setProperty(key, value) { this[key] = value; } }, events: {},
            setAttribute(key, value) { this[key] = value; },
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
        'power-chart-status', 'powerChart', 'activityChart', 'activity-chart-status',
        'activity-history-note', 'equipment-lanes'].map(id => [id, element()]));
    const intervals = {};
    const charts = [];
    let respond = async url => url === '/api/plugs' ? [plug()] : url.startsWith('/api/plugs/history') ? [series()] : [];
    const context = vm.createContext({
        document: { getElementById: id => nodes[id], createElement: element },
        fetch: async url => ({ ok: true, json: () => respond(url) }),
        window: { chartsReady: Promise.resolve(true) },
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
    vm.runInContext(fs.readFileSync(require.resolve('../src/citruspi/static/plugs.js'), 'utf8'), context);
    return {
        nodes, intervals, charts, context, respond: fn => { respond = fn; },
        openPower() { nodes['power-details'].open = true; nodes['power-details'].events.toggle(); },
    };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('compact cards distinguish enabled relay from idle; usage details are separate', async () => {
    const h = harness();
    await flush();
    const card = h.nodes['plug-cards'].children[0];
    assert.equal(card.children[0].textContent, 'Heater <script>alert(1)</script>');
    assert.equal(card.querySelector('.plug-state').textContent, 'Idle');
    assert.equal(card.querySelector('.plug-watts').textContent, '0.0 W');
    assert.equal(card.children.length, 4);
    assert.equal(card.children[3].textContent, 'Today: 0.500 kWh');
    const usage = h.nodes['plug-details'].children[0];
    assert.ok(usage.children.some(c => c.textContent.includes('50.0% coverage')));
    assert.ok(usage.children.some(c => c.textContent.includes('plug enabled')));
});

test('stale readings lose live state without waiting for another API response', async () => {
    const h = harness();
    await flush();
    const card = h.nodes['plug-cards'].children[0];
    card.dataset.timestamp = iso(181);
    h.intervals[5000]();
    assert.equal(card.querySelector('.plug-state').textContent, 'Unknown · readings stale');
    assert.equal(card.querySelector('.plug-watts').textContent, '— W');
});

test('API failure clears current claims and usage details, and recovers on the next poll', async () => {
    const h = harness();
    await flush();
    h.respond(async () => { throw new Error('offline'); });
    await h.intervals[30000]();
    assert.match(h.nodes['plug-cards'].children[0].textContent, /unavailable/);
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
    assert.equal(chart.data.datasets.length, 1);
    assert.equal(chart.options.scales.y.title.text, 'Watts');
    await h.context.window.loadPowerChart(72);
    assert.equal(h.charts.at(-1).config.options.scales.x.time.unit, 'day');
});

test('activity is visible without sensors and has no misleading current-state badges', async () => {
    const h = harness();
    await flush();
    const lane = h.nodes['equipment-lanes'].children[0];
    assert.equal(lane.children[0].textContent, 'Grow light');
    const segments = lane.children[1].children;
    assert.equal(segments.length, 3); // Active interval, idle interval, last observation.
    assert.equal(segments[0].className, 'lane-segment active');
    assert.equal(segments[1].className, 'lane-segment idle');
    assert.match(segments[0].title, /Active/);
    assert.equal(h.nodes['activity-chart-status'].hidden, true);
    assert.match(h.nodes['activity-history-note'].textContent, /History starts/);
    assert.equal(h.charts[0].config.data.datasets[0].data.length, 0);
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
    await h.context.window.loadPowerChart(1);
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
    assert.match(h.nodes['activity-chart-status'].textContent, /unavailable/);
});
