// Run with node --test tests/test_plugs.cjs (no npm dependencies).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

function harness(extraNodes = []) {
    function element() {
        return {
            children: [], dataset: {}, textContent: '', className: '',
            appendChild(child) { this.children.push(child); },
            replaceChildren(...children) { this.children = children; },
            querySelector(selector) { return this.children.find(c => c.className.split(' ').includes(selector.slice(1))); },
            getContext() { return {}; },
        };
    }
    const nodes = Object.fromEntries(['plug-cards', 'power-chart-status', 'powerChart', ...extraNodes].map(id => [id, element()]));
    const intervals = {};
    const charts = [];
    let respond = async url => url === '/api/plugs' ? [plug()] : url.startsWith('/api/plugs/history') ? [{
        name: 'Heater', readings: [{ timestamp: '2026-10-19T00:00:00Z', power_w: 100 },
            { timestamp: '2026-10-19T00:01:00Z', power_w: null },
            { timestamp: '2026-10-19T00:02:00Z', power_w: 100 }],
    }] : [];
    const context = vm.createContext({
        document: { getElementById: id => nodes[id], createElement: element },
        fetch: async url => ({ ok: true, json: () => respond(url) }),
        window: { chartsReady: Promise.resolve(true) },
        getThemeColors: () => ({ textColor: 'white' }),
        currentRangeHours: 24, Date,
        Chart: function(ctx, config) { this.config = config; this.destroy = () => { this.destroyed = true; }; charts.push(this); },
        setInterval: (fn, ms) => { intervals[ms] = fn; },
    });
    vm.runInContext(fs.readFileSync(require.resolve('../src/citruspi/static/plugs.js'), 'utf8'), context);
    return { nodes, intervals, charts, context, respond: fn => { respond = fn; } };
}

function plug(overrides = {}) {
    return {
        name: 'Heater <script>alert(1)</script>', monitoring: true, stale: false, stale_after_sec: 180,
        latest: { timestamp: new Date().toISOString(), relay_on: true, power_w: 0, active: false, energy_today_kwh: 0.5, status: 'ok' },
        summary: { estimated_kwh: 0.5, runtime_hours: 2, duty_cycle_pct: 25, coverage_pct: 50 },
        ...overrides,
    };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

test('cards distinguish relay enabled from idle and insert names as text', async () => {
    const h = harness();
    await flush();
    const card = h.nodes['plug-cards'].children[0];
    assert.equal(card.children[0].textContent, 'Heater <script>alert(1)</script>');
    assert.equal(card.querySelector('.plug-state').textContent, 'Idle');
    assert.equal(card.querySelector('.plug-watts').textContent, '0.0 W');
    assert.ok(card.children.some(c => c.textContent === 'Plug: Enabled'));
    assert.ok(card.children.some(c => c.textContent.includes('50.0% coverage')));
});

test('stale readings lose live state without waiting for another API response', async () => {
    const h = harness();
    await flush();
    const card = h.nodes['plug-cards'].children[0];
    card.dataset.timestamp = new Date(Date.now() - 181000).toISOString();
    h.intervals[5000]();
    assert.equal(card.querySelector('.plug-state').textContent, 'Unknown · readings stale');
    assert.equal(card.querySelector('.plug-watts').textContent, '— W');
    assert.equal(card.querySelector('.plug-relay').textContent, 'Plug: Unknown');
});

test('API failure clears live claims and a subsequent poll recovers', async () => {
    const h = harness();
    await flush();
    h.respond(async () => { throw new Error('offline'); });
    await h.intervals[30000]();
    assert.match(h.nodes['plug-cards'].children[0].textContent, /unavailable/);
    h.respond(async () => [plug()]);
    await h.intervals[30000]();
    assert.equal(h.nodes['plug-cards'].children[0].querySelector('.plug-state').textContent, 'Idle');
});

test('mock monitoring and failed power readings never imply equipment is off', async () => {
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

test('outage markers remain null in power datasets and temperature uses a separate axis', async () => {
    const h = harness();
    await flush();
    const chart = h.charts[0].config;
    assert.equal(chart.data.datasets[0].data[1].y, null);
    assert.equal(chart.data.datasets[0].spanGaps, false);
    assert.equal(chart.data.datasets[0].stepped, true);
    assert.equal(chart.data.datasets[1].yAxisID, 'temperature');
});

test('an older range response cannot replace the latest power chart', async () => {
    const h = harness();
    await flush();
    let release;
    h.respond(url => url.includes('hours=24') && url.startsWith('/api/plugs/history') ? new Promise(resolve => { release = resolve; }) : []);
    const old = h.context.window.loadPowerChart(24);
    await flush();
    await h.context.window.loadPowerChart(72);
    const newest = h.charts.at(-1);
    release([{ name: 'Old', readings: [] }]);
    await old;
    assert.equal(h.charts.at(-1), newest);
    assert.equal(newest.destroyed, undefined);
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

test('chart failures clear the old chart and show a recoverable error', async () => {
    const h = harness();
    await flush();
    const old = h.charts[0];
    h.respond(async () => { throw new Error('offline'); });
    await h.context.window.loadPowerChart(24);
    assert.equal(old.destroyed, true);
    assert.match(h.nodes['power-chart-status'].textContent, /unavailable/);
});

test('activityChart renders temperature datasets and activity tracks plugin', async () => {
    const h = harness(['activityChart', 'activity-chart-status', 'activity-history-note']);
    h.respond(url => url.startsWith('/api/plugs/history') ? [{
        name: 'Grow light', role: 'light', activity: [
            { start: '2026-10-19T00:00:00Z', end: '2026-10-19T00:10:00Z', active: true },
        ],
        recorded_since: '2026-10-19T00:00:00Z', readings: [],
    }] : url === '/api/plugs' ? [plug()] : [{
        timestamp: '2026-10-19T00:00:00Z', temp_f: 68, root_temp_f: 65,
    }]);
    await flush();
    await h.context.window.loadPowerChart(24);
    const activityChart = h.charts.find(c => c.config.plugins?.some(p => p.id === 'activityTracksPlugin'));
    assert.ok(activityChart);
    assert.equal(activityChart.config.data.datasets[0].label, 'Canopy °F');
    assert.equal(activityChart.config.data.datasets[1].label, 'Root °F');
    assert.equal(activityChart.config.data.datasets[0].data[0].y, 68);
    assert.equal(activityChart.config.data.datasets[1].data[0].y, 65);
});
