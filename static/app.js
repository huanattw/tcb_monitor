const PAGE_REFRESH_INTERVAL_MS = 60 * 60 * 1000;
const INITIAL_REFRESH_RETRY_MS = 5000;

function escapeHtml(text) {
    return String(text)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

async function loadStatus() {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) {
        throw new Error('API error: ' + response.status);
    }
    const data = await response.json();
    render(data);
    return Object.values(data.markets || {}).every((market) => (
        Boolean(market?.last_checked_local)
    ));
}

function render(data) {
    const updated = document.getElementById('updated');
    const markets = data.markets || {};
    const marketConfig = data.market_config || {};
    const marketCodes = Object.keys(marketConfig).length
        ? Object.keys(marketConfig)
        : Object.keys(markets);
    const updatedText = marketCodes
        .map((code) => markets[code]?.last_checked_local)
        .filter(Boolean)
        .sort()
        .at(-1) || '尚未抓取';

    updated.textContent = `最後更新：${updatedText} · 每小時更新`;

    marketCodes.forEach((code) => renderMarket(
        code,
        markets[code] || null,
        marketConfig[code]?.currency || ''
    ));
}

function sanitizeForId(text) {
    return String(text || '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}

function renderMarket(marketCode, marketData, currencyUnit = '') {
    const cards = document.getElementById(`cards-${marketCode}`);

    if (!marketData) {
        cards.innerHTML = `<article class="card"><p class="name">${marketCode.toUpperCase()}</p><p class="rate bad">No Data</p><p class="tiny">後端尚未提供此市場資料</p></article>`;
        return;
    }

    const marketAffData = getMarketAffData(currencyUnit, marketData.results || []);

    cards.innerHTML = renderMarketAffCard(
        marketCode,
        marketAffData.displayValue,
        marketAffData.historyPoints,
        marketAffData.highText,
        marketAffData.highTime,
        marketAffData.unit
    ) + marketData.results.map((item, index) => {
        const isError = Boolean(item.error);
        const rateClass = isError ? 'bad' : 'ok';
        const rateText = isError ? '抓取失敗' : item.rate;
        const highRate = item.last_high_rate || '尚無';
        const highTime = item.last_high_checked_at_local || '尚無';
        const historyPoints = Array.isArray(item.history_points)
            ? item.history_points.filter((x) => Number.isFinite(x))
            : [];
        const chartId = `chart-${marketCode}-${index}-${sanitizeForId(item.merchant)}`;
        return `
            <article class="card">
                <p class="name">${escapeHtml(item.merchant)}</p>
                <div class="rate-chart-stack">
                    ${renderSparkline(historyPoints, chartId, '%')}
                    <p class="rate rate-overlay ${rateClass}">${escapeHtml(rateText)}</p>
                </div>
                <p class="tiny">上次高點: ${escapeHtml(highRate)} (${escapeHtml(highTime)})</p>
            </article>
        `;
    }).join('');
}

function getMarketAffText(results) {
    const values = [...new Set(
        (results || [])
            .map((item) => (item && item.aff ? String(item.aff).trim() : ''))
            .filter(Boolean)
    )];

    if (!values.length) {
        return '未抓到';
    }
    return values.join(' | ');
}

function getMarketAffHistoryPoints(results) {
    const allSeries = (results || [])
        .map((item) => (Array.isArray(item.aff_history_points) ? item.aff_history_points : []))
        .filter((series) => series.length > 0);

    if (!allSeries.length) {
        return [];
    }

    const picked = allSeries.reduce((best, current) => (current.length > best.length ? current : best), allSeries[0]);
    return picked.filter((x) => Number.isFinite(x));
}

function formatNumber(value) {
    if (!Number.isFinite(value)) {
        return '尚無';
    }
    if (Number.isInteger(value)) {
        return `${value}`;
    }
    return value.toFixed(1);
}

function formatAffTextWithCurrency(affText, unit) {
    if (!affText || affText === '未抓到') {
        return affText;
    }
    if (!unit) {
        return affText;
    }

    return String(affText)
        .split('|')
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => `${part} ${unit}`)
        .join(' | ');
}

function getMarketAffHigh(results) {
    let bestValue = null;
    let bestTime = null;

    (results || []).forEach((item) => {
        const value = Number(item?.aff_last_high_value);
        if (!Number.isFinite(value)) {
            return;
        }

        const time = item?.aff_last_high_checked_at_local || null;
        if (bestValue === null || value > bestValue || (value === bestValue && time && (!bestTime || time > bestTime))) {
            bestValue = value;
            bestTime = time;
        }
    });

    return { value: bestValue, time: bestTime || '尚無' };
}

function getMarketAffData(unit, results) {
    const text = getMarketAffText(results);
    const historyPoints = getMarketAffHistoryPoints(results);
    const high = getMarketAffHigh(results);
    const highText = Number.isFinite(high.value)
        ? `${formatNumber(high.value)} ${unit}`.trim()
        : '尚無';

    return {
        unit,
        displayValue: formatAffTextWithCurrency(text, unit),
        historyPoints,
        highText,
        highTime: high.time,
    };
}

function renderMarketAffCard(marketCode, affText, historyPoints, highText, highTime, unit) {
    const chartId = `aff-chart-${sanitizeForId(marketCode)}`;
    return `
        <article class="card">
            <p class="name">${escapeHtml(marketCode.toUpperCase())} AFF</p>
            <div class="rate-chart-stack">
                ${renderSparkline(historyPoints, chartId, unit)}
                <p class="rate rate-overlay ok">${escapeHtml(affText)}</p>
            </div>
            <p class="tiny">上次高點: ${escapeHtml(highText)} (${escapeHtml(highTime)})</p>
        </article>
    `;
}

function renderSparkline(points, chartId, unit = '%') {
    if (!points || points.length < 2) {
        return '<p class="tiny">折線圖資料不足</p>';
    }

    const visiblePoints = points;
    const width = 240;
    const height = 64;
    const padding = 6;
    const min = Math.min(...visiblePoints);
    const max = Math.max(...visiblePoints);
    const range = (max - min) || 1;

    const coords = visiblePoints.map((value, index) => {
        const x = padding + (index * (width - padding * 2)) / (visiblePoints.length - 1);
        const y = padding + ((max - value) * (height - padding * 2)) / range;
        return { x, y };
    });

    const polyline = coords.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
    const last = coords[coords.length - 1];

    return `
        <div class="sparkline-wrap" aria-label="歷史回饋折線圖">
            <svg class="sparkline" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
                <polyline class="sparkline-line" points="${polyline}"></polyline>
                <circle class="sparkline-dot" cx="${last.x.toFixed(2)}" cy="${last.y.toFixed(2)}" r="2.8"></circle>
            </svg>
            <p class="tiny chart-meta">低 ${min.toFixed(1)} ${escapeHtml(unit)} | 高 ${max.toFixed(1)} ${escapeHtml(unit)}</p>
        </div>
    `;
}

async function boot() {
    let nextRefreshMs = PAGE_REFRESH_INTERVAL_MS;
    try {
        const initialDataReady = await loadStatus();
        if (!initialDataReady) {
            nextRefreshMs = INITIAL_REFRESH_RETRY_MS;
            document.getElementById('updated').textContent = '啟動完成，資料更新中...';
        }
    } catch (err) {
        nextRefreshMs = INITIAL_REFRESH_RETRY_MS;
        document.getElementById('updated').textContent = '讀取失敗，稍後自動重試';
    }
    setTimeout(boot, nextRefreshMs);
}

boot();
