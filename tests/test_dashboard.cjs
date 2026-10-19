// Run with: node --test tests/test_dashboard.cjs (no npm dependencies).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function page(name, { status = 'Telemetry Feed', timestamp, storedView = 'grid', reports = true } = {}) {
    const listeners = {};
    const nodes = {};
    const timers = new Map();
    let timerId = 0;
    function element(id) {
        const classes = new Set();
        return nodes[id] = {
            id, dataset: {}, style: {}, hidden: false, textContent: '',
            removeAttribute(name) { delete this[name]; },
            setAttribute(name, value) { this[name] = value; },
            getContext: () => ({}),
            classList: {
                add: name => classes.add(name),
                remove: name => classes.delete(name),
                contains: name => classes.has(name),
                toggle(name, value) { if (value) classes.add(name); else classes.delete(name); },
            },
        };
    }
    for (const id of ['climateChart', 'thermalChart', 'chart-status', 'live-readings',
        'telemetry-live-dot', 'telemetry-status-label',
        'gallery-grid-view', 'gallery-log-view', 'timelapse-btn', 'camera-display',
        'camera-empty', 'stream-live-pill', 'stream-loading', 'camera-caption',
        'toggle-stream-btn', 'camera-stage', 'camera-fullscreen-btn']) element(id);
    if (reports) for (const id of ['view-grid-btn', 'view-log-btn']) element(id);
    nodes['telemetry-status-label'].dataset.status = status;
    nodes['live-readings'].dataset.staleAfter = '180';
    if (timestamp) element('telemetry-time').dataset.timestamp = timestamp;
    const storage = new Map([['citruspi-gallery-view', storedView]]);
    let fetchHistory = async () => ({ ok: true, json: async () => [] });
    const context = vm.createContext({
        document: {
            documentElement: { getAttribute: () => 'dark' },
            getElementById: id => nodes[id] || null,
            querySelectorAll: () => [],
            addEventListener: (name, fn) => { listeners[name] = fn; },
            body: { addEventListener: (name, fn) => { listeners[name] = fn; } },
        },
        localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
        window: { addEventListener() {}, location: { search: '' } },
        fetch: (...args) => fetchHistory(...args),
        Chart: function(ctx, config) {
            Object.assign(this, config);
            this.destroy = () => { this.destroyed = true; };
        },
        setInterval() {}, clearInterval() {}, URLSearchParams, Date,
        setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
        clearTimeout: id => timers.delete(id),
    });
    const template = fs.readFileSync(path.join(__dirname, '../src/citruspi/templates', name), 'utf8');
    const script = template.match(/<script>([\s\S]*)<\/script>/)[1]
        .replace('const photos = {{ photos | tojson }};', 'const photos = [];')
        .replaceAll('{{ config.stream_max_session_sec }}', '300');
    vm.runInContext(script, context);
    return {
        nodes, listeners, storage, timers, document: context.document,
        run: code => vm.runInContext(code, context),
        fetch: fn => { fetchHistory = fn; },
    };
}

const reading = {
    timestamp: new Date().toISOString(), temp_f: 60, root_temp_f: 58, humidity: 65, vpd_kpa: 0.5,
};

test('camera expands inside the page when native fullscreen is unavailable', async () => {
    const p = page('dashboard.html');
    await p.run('toggleCameraFullscreen()');
    assert.equal(p.nodes['camera-stage'].classList.contains('is-expanded'), true);
    assert.equal(p.nodes['camera-fullscreen-btn'].textContent, '✕ Exit fullscreen');
    p.listeners.keydown({ key: 'Escape' });
    assert.equal(p.nodes['camera-stage'].classList.contains('is-expanded'), false);
    assert.equal(p.nodes['camera-fullscreen-btn'].textContent, '⛶ Fullscreen');
});

test('camera fullscreen button also closes the page expansion', async () => {
    const p = page('dashboard.html');
    await p.run('toggleCameraFullscreen();');
    await p.run('toggleCameraFullscreen();');
    assert.equal(p.nodes['camera-stage'].classList.contains('is-expanded'), false);
});

test('camera uses native fullscreen and keeps the exit button synchronized', async () => {
    const p = page('dashboard.html');
    const stage = p.nodes['camera-stage'];
    stage.requestFullscreen = async () => { p.document.fullscreenElement = stage; };
    p.document.exitFullscreen = async () => { p.document.fullscreenElement = null; };
    await p.run('toggleCameraFullscreen()');
    assert.equal(p.document.fullscreenElement, stage);
    assert.equal(stage.classList.contains('is-expanded'), false);
    assert.equal(p.nodes['camera-fullscreen-btn']['aria-label'], 'Exit camera fullscreen');
    await p.run('toggleCameraFullscreen()');
    assert.equal(p.document.fullscreenElement, null);
    assert.equal(p.nodes['camera-fullscreen-btn']['aria-label'], 'Enter camera fullscreen');
    p.document.fullscreenElement = stage;
    p.listeners.fullscreenchange();
    assert.equal(p.nodes['camera-fullscreen-btn'].textContent, '✕ Exit fullscreen');
    p.document.fullscreenElement = null;
    p.listeners.fullscreenchange();
    assert.equal(p.nodes['camera-fullscreen-btn'].textContent, '⛶ Fullscreen');
});

test('a rejected fullscreen request falls back to page expansion', async () => {
    const p = page('dashboard.html');
    p.nodes['camera-stage'].requestFullscreen = async () => { throw new Error('unsupported'); };
    await p.run('toggleCameraFullscreen()');
    assert.equal(p.nodes['camera-stage'].classList.contains('is-expanded'), true);
});

test('live view shows loading until the first stream image loads', () => {
    const p = page('dashboard.html');
    p.run('startStream()');
    const img = p.nodes['camera-display'];
    assert.equal(p.nodes['stream-loading'].style.display, 'flex');
    assert.equal(p.nodes['stream-live-pill'].style.display, 'none');
    assert.equal(img.style.visibility, 'hidden');
    assert.equal(p.nodes['toggle-stream-btn'].textContent, '✕ Cancel');
    img.currentSrc = '/captures/old-photo.jpg';
    img.onload();
    assert.equal(p.nodes['stream-loading'].style.display, 'flex');
    img.currentSrc = img.src;
    img.onload();
    assert.equal(p.nodes['stream-loading'].style.display, 'none');
    assert.equal(p.nodes['stream-live-pill'].style.display, 'flex');
    assert.equal(img.style.visibility, 'visible');
    assert.equal(p.nodes['toggle-stream-btn'].textContent, '⏹ Stop');
    assert.equal([...p.timers.values()].some(t => t.delay === 20000), false);
});

test('live view can be cancelled while the camera starts', () => {
    const p = page('dashboard.html');
    p.run('startStream(); toggleStream()');
    assert.equal(p.nodes['stream-loading'].style.display, 'none');
    assert.equal(p.nodes['stream-live-pill'].style.display, 'none');
    assert.equal(p.nodes['camera-display'].onload, null);
    assert.equal(p.nodes['camera-display'].onerror, null);
    assert.equal(p.timers.size, 0);
    assert.equal(p.nodes['toggle-stream-btn'].textContent, '▶ Live View');
});

test('camera startup times out instead of showing an endless spinner', () => {
    const p = page('dashboard.html');
    p.run('startStream()');
    [...p.timers.values()].find(t => t.delay === 20000).fn();
    assert.match(p.nodes['camera-caption'].textContent, /startup timed out/);
    assert.equal(p.nodes['stream-loading'].style.display, 'none');
    assert.equal(p.nodes['stream-live-pill'].style.display, 'none');
    assert.equal(p.nodes['toggle-stream-btn'].textContent, '▶ Live View');
});

test('camera error clears loading and restores the saved photo', () => {
    const p = page('dashboard.html');
    p.run('startStream()');
    p.nodes['camera-display'].onerror();
    assert.match(p.nodes['camera-caption'].textContent, /unavailable/);
    assert.equal(p.nodes['stream-loading'].style.display, 'none');
    assert.equal(p.nodes['camera-display'].style.visibility, 'visible');
    assert.equal(p.timers.size, 0);
});

test('empty history clears previously drawn charts', async () => {
    const p = page('dashboard.html');
    p.fetch(async () => ({ ok: true, json: async () => [reading] }));
    await p.run('loadCharts(24)');
    const oldChart = p.run('climateChartInstance');
    assert.equal(oldChart.data.datasets[0].data[0].y, 60);
    p.fetch(async () => ({ ok: true, json: async () => [] }));
    await p.run('loadCharts(72)');
    assert.equal(oldChart.destroyed, true);
    assert.equal(p.run('climateChartInstance'), null);
    assert.match(p.nodes['chart-status'].textContent, /No readings/);
});

test('failed history requests show an error and recover on the next refresh', async () => {
    const p = page('dashboard.html');
    p.fetch(async () => ({ ok: false }));
    await p.run('loadCharts(24)');
    assert.match(p.nodes['chart-status'].textContent, /Could not load/);
    p.fetch(async () => { throw new Error('offline'); });
    await p.run('loadCharts(24)');
    assert.equal(p.nodes['chart-status'].hidden, false);
    p.fetch(async () => ({ ok: true, json: async () => [reading] }));
    await p.run('loadCharts(24)');
    assert.equal(p.nodes['chart-status'].hidden, true);
    assert.ok(p.run('climateChartInstance'));
});

test('charts wait for assets and explain asset failures', async () => {
    const p = page('dashboard.html');
    p.fetch(async () => ({ ok: true, json: async () => [reading] }));
    p.run('window.chartsReady = Promise.resolve(false)');
    await p.run('loadCharts(24)');
    assert.match(p.nodes['chart-status'].textContent, /Chart assets could not be loaded/);
    p.run('window.chartsReady = Promise.resolve(true)');
    await p.run('loadCharts(24)');
    assert.ok(p.run('climateChartInstance'));
});

function chartLoader() {
    const scripts = [];
    let timeout;
    const context = vm.createContext({
        window: {},
        document: { createElement: () => ({}), head: { append: script => scripts.push(script) } },
        setTimeout: fn => { timeout = fn; return 0; }, clearTimeout() {},
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/citruspi/static/charts.js'), 'utf8'), context);
    return { scripts, ready: vm.runInContext('window.chartsReady', context), timeout: () => timeout() };
}

test('date adapter loads only after Chart.js finishes its CDN fallback', async () => {
    const { scripts, ready } = chartLoader();
    assert.equal(scripts.length, 1);
    scripts[0].onerror();
    assert.match(scripts[0].src, /^https:/);
    assert.equal(scripts.length, 1);
    scripts[0].onload();
    await new Promise(setImmediate);
    assert.equal(scripts.length, 2);
    assert.match(scripts[1].src, /adapter/);
    scripts[1].onload();
    assert.equal(await ready, true);
});

test('failed local and CDN assets resolve without an unhandled rejection', async () => {
    const { scripts, ready } = chartLoader();
    scripts[0].onerror();
    scripts[0].onerror();
    assert.equal(await ready, false);
    assert.equal(scripts.length, 1);
});

test('unresponsive chart assets do not leave charts loading indefinitely', async () => {
    const { ready, timeout } = chartLoader();
    timeout();
    assert.equal(await ready, false);
});

test('an older range response cannot replace the latest range', async () => {
    const p = page('dashboard.html');
    let resolve;
    p.fetch(() => new Promise(done => { resolve = done; }));
    const older = p.run('loadCharts(24)');
    p.fetch(async () => ({ ok: true, json: async () => [] }));
    await p.run('loadCharts(72)');
    resolve({ ok: true, json: async () => [reading] });
    await older;
    assert.equal(p.run('climateChartInstance'), null);
    assert.match(p.nodes['chart-status'].textContent, /No readings/);
});

test('missing sensors do not add a redundant layout-shifting notice', async () => {
    const p = page('dashboard.html');
    p.fetch(async () => ({ ok: true, json: async () => [{
        timestamp: reading.timestamp, temp_f: null, root_temp_f: null, humidity: null, vpd_kpa: null,
    }] }));
    await p.run('loadCharts(24)');
    assert.equal(p.nodes['chart-status'].hidden, true);
    assert.equal(p.nodes['chart-status'].textContent, '');
});

test('fresh timestamps do not turn missing sensors green', () => {
    for (const status of ['Canopy sensor offline', 'Root probe offline', 'No readings yet']) {
        const p = page('dashboard.html', { status, timestamp: reading.timestamp });
        assert.equal(p.nodes['telemetry-live-dot'].className, 'stale-dot');
        assert.equal(p.nodes['telemetry-status-label'].textContent, status);
    }
});

test('staleness respects configured polling intervals', () => {
    const p = page('dashboard.html', { timestamp: new Date(Date.now() - 300000).toISOString() });
    assert.equal(p.nodes['telemetry-status-label'].textContent, 'Telemetry (Stale)');
    p.nodes['live-readings'].dataset.staleAfter = '900';
    p.run('checkTelemetryStaleness()');
    assert.equal(p.nodes['telemetry-status-label'].textContent, 'Telemetry Feed');
    assert.equal(p.nodes['telemetry-live-dot'].className, 'live-dot');
});

test('polling errors persist until a successful readings swap', () => {
    const p = page('dashboard.html', { timestamp: reading.timestamp });
    const event = { detail: { target: { id: 'live-readings' } } };
    p.listeners['htmx:sendError'](event);
    p.run('checkTelemetryStaleness()');
    assert.equal(p.nodes['telemetry-status-label'].textContent, 'Telemetry (Offline)');
    p.listeners['htmx:afterSwap'](event);
    assert.equal(p.nodes['telemetry-status-label'].textContent, 'Telemetry Feed');
});

test('saved health-log preference falls back to photos when reports are absent', () => {
    const p = page('gallery.html', { storedView: 'log', reports: false });
    assert.equal(p.nodes['gallery-grid-view'].style.display, 'grid');
    assert.equal(p.nodes['gallery-log-view'].style.display, 'none');
    assert.notEqual(p.nodes['timelapse-btn'].style.display, 'none');
    assert.equal(p.storage.get('citruspi-gallery-view'), 'grid');
});

test('health-log preference is retained when reports exist', () => {
    const p = page('gallery.html', { storedView: 'log' });
    assert.equal(p.nodes['gallery-grid-view'].style.display, 'none');
    assert.equal(p.nodes['gallery-log-view'].style.display, 'flex');
    assert.notEqual(p.nodes['timelapse-btn'].style.display, 'none');
    assert.equal(p.nodes['view-log-btn']['aria-pressed'], 'true');
    p.run("switchGalleryView('grid')");
    assert.notEqual(p.nodes['timelapse-btn'].style.display, 'none');
    assert.equal(p.nodes['view-grid-btn']['aria-pressed'], 'true');
    assert.equal(p.nodes['view-log-btn']['aria-pressed'], 'false');
});
