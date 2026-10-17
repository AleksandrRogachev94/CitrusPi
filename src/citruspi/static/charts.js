// The date adapter depends on Chart.js, including when local assets fall back to the CDN.
window.chartsReady = (async () => {
    for (const [local, remote] of [
        ['/static/vendor/chart.umd.min.js', 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js'],
        ['/static/vendor/chartjs-adapter-date-fns.bundle.min.js', 'https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js'],
    ]) {
        const loaded = await new Promise(resolve => {
            const script = document.createElement('script');
            const finish = loaded => {
                clearTimeout(timer);
                resolve(loaded);
            };
            const timer = setTimeout(() => finish(false), 15000);
            script.onload = () => finish(true);
            script.onerror = () => {
                script.onerror = () => finish(false);
                script.src = remote;
            };
            script.src = local;
            document.head.append(script);
        });
        if (!loaded) return false;
    }
    return true;
})();
