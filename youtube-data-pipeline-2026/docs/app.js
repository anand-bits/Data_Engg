(() => {
  const fmt = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
  const fullFmt = new Intl.NumberFormat('en-US');
  const $ = (id) => document.getElementById(id);
  let analytics;
  let model;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  async function loadAssets() {
    const [analyticsResponse, modelResponse] = await Promise.all([
      fetch('assets/analytics.json'),
      fetch('assets/model.json')
    ]);
    if (!analyticsResponse.ok || !modelResponse.ok) throw new Error('Dashboard assets not found');
    [analytics, model] = await Promise.all([analyticsResponse.json(), modelResponse.json()]);
    initialize();
  }

  function initialize() {
    const { metadata, regions, category_names: categoryNames } = analytics;
    $('data-period').textContent = `${metadata.period_start} to ${metadata.period_end}`;
    $('sidebar-period').textContent = `${metadata.period_start} — ${metadata.period_end}`;
    $('metric-records').textContent = fmt.format(metadata.records);
    $('metric-videos').textContent = fmt.format(metadata.unique_videos);
    $('metric-regions').textContent = metadata.regions_count;
    const regionOptions = regions.map((row) => `<option value="${escapeHtml(row.region)}">${escapeHtml(row.region)}</option>`).join('');
    $('region-filter').innerHTML = `<option value="ALL">All regions</option>${regionOptions}`;
    $('model-region').innerHTML = regionOptions;
    const categoryIds = Object.keys(categoryNames).sort((a, b) => Number(a) - Number(b));
    $('model-category').innerHTML = categoryIds.map((id) =>
      `<option value="${escapeHtml(id)}">${escapeHtml(categoryNames[id])}</option>`).join('');
    $('model-r2').textContent = model.metrics.r2_log_views.toFixed(3);
    $('model-mae').textContent = model.metrics.mae_log_views.toFixed(3);
    $('model-split').textContent = `${fmt.format(model.test_rows)} rows`;

    $('region-filter').addEventListener('change', render);
    $('video-search').addEventListener('input', renderVideos);
    ['model-region', 'model-category', 'model-title', 'model-age', 'model-hour', 'model-weekday', 'model-tags']
      .forEach((id) => $(id).addEventListener('input', predict));
    $('model-region').addEventListener('change', predict);
    $('model-category').addEventListener('change', predict);
    render();
    predict();
  }

  function render() {
    const region = $('region-filter').value;
    const regionRows = region === 'ALL' ? analytics.regions : analytics.regions.filter((row) => row.region === region);
    const videoCount = regionRows.reduce((sum, row) => sum + row.videos, 0);
    $('metric-region-videos').textContent = fmt.format(videoCount);
    $('metric-region-label').textContent = region === 'ALL' ? 'across all regions' : `${region} selected`;
    renderCategories(region);
    renderActivity(region);
    renderVideos();
  }

  function renderCategories(region) {
    const rows = analytics.categories
      .filter((row) => region === 'ALL' || row.region === region)
      .reduce((acc, row) => {
        const current = acc.get(row.category_id) || { category_id: row.category_id, name: row.category_name, total: 0, count: 0 };
        current.total += row.avg_views * row.records;
        current.count += row.records;
        acc.set(row.category_id, current);
        return acc;
      }, new Map());
    const top = [...rows.values()].map((row) => ({ ...row, avg_views: row.count ? row.total / row.count : 0 }))
      .sort((a, b) => b.avg_views - a.avg_views).slice(0, 7);
    const max = Math.max(...top.map((row) => row.avg_views), 1);
    const width = 620;
    const rowHeight = 25;
    const height = Math.max(160, top.length * rowHeight + 13);
    const bars = top.map((row, index) => {
      const y = index * rowHeight + 5;
      const barWidth = Math.max(2, (row.avg_views / max) * 310);
      const color = index === 0 ? '#59e0bf' : index < 3 ? '#4b9c9b' : '#36566b';
      return `<g><text x="0" y="${y + 12}" fill="#a9b8c9" font-size="10">${escapeHtml(row.name)}</text>
        <rect x="176" y="${y}" width="310" height="15" rx="4" fill="#172638"/>
        <rect x="176" y="${y}" width="${barWidth}" height="15" rx="4" fill="${color}"/>
        <text x="${Math.min(498, 185 + barWidth)}" y="${y + 11}" fill="#e0ebf5" font-size="9">${fmt.format(row.avg_views)}</text></g>`;
    }).join('');
    $('category-chart').innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMinYMin meet" aria-hidden="true">${bars || '<text x="4" y="22" fill="#8292a8" font-size="12">No category records for this region.</text>'}</svg>`;
    $('category-legend').innerHTML = `<span class="legend-item"><i class="legend-swatch"></i>Top category</span><span class="legend-item">${escapeHtml(region === 'ALL' ? 'All regions combined' : `Region ${region}`)}</span>`;
  }

  function renderActivity(region) {
    const byDate = analytics.daily.filter((row) => region === 'ALL' || row.region === region)
      .reduce((map, row) => map.set(row.date, (map.get(row.date) || 0) + row.views), new Map());
    let points = [...byDate.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    if (points.length > 64) {
      const stride = (points.length - 1) / 63;
      points = Array.from({ length: 64 }, (_, index) => points[Math.round(index * stride)]);
    }
    const W = 620, H = 196, left = 38, right = 10, top = 12, bottom = 25;
    const values = points.map((entry) => entry[1]);
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const x = (index) => left + (points.length <= 1 ? 0 : index * (W - left - right) / (points.length - 1));
    const y = (value) => H - bottom - ((value - min) / Math.max(max - min, 1)) * (H - top - bottom);
    const line = points.map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(point[1]).toFixed(1)}`).join(' ');
    const area = points.length ? `${line} L${x(points.length - 1)},${H - bottom} L${x(0)},${H - bottom} Z` : '';
    const grid = [0, 1, 2, 3].map((i) => {
      const gy = top + i * (H - top - bottom) / 3;
      return `<line x1="${left}" y1="${gy}" x2="${W - right}" y2="${gy}" stroke="#203147" stroke-dasharray="3 5"/>
        <text x="0" y="${gy + 3}" fill="#6d8198" font-size="8">${fmt.format(max - i * (max - min) / 3)}</text>`;
    }).join('');
    $('activity-chart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="area-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#59e0bf" stop-opacity=".27"/><stop offset="1" stop-color="#59e0bf" stop-opacity="0"/></linearGradient></defs>${grid}${area ? `<path d="${area}" fill="url(#area-fill)"/><path d="${line}" fill="none" stroke="#59e0bf" stroke-width="2" vector-effect="non-scaling-stroke"/>` : ''}${points.filter((_, i) => points.length < 24 || i % 5 === 0).map((point) => `<circle cx="${x(points.findIndex((candidate) => candidate === point))}" cy="${y(point[1])}" r="2.1" fill="#8af0d9"><title>${escapeHtml(point[0])}: ${fullFmt.format(point[1])} views</title></circle>`).join('')}</svg>`;
    $('activity-range').innerHTML = points.length ? `<span>${escapeHtml(points[0][0])}</span><span>${points.length} daily snapshots shown</span><span>${escapeHtml(points.at(-1)[0])}</span>` : '<span>No daily records available.</span>';
  }

  function renderVideos() {
    const region = $('region-filter').value;
    const query = $('video-search').value.trim().toLowerCase();
    const rows = analytics.top_videos.filter((row) => (region === 'ALL' || row.region === region) && row.title.toLowerCase().includes(query)).slice(0, 24);
    $('video-count').textContent = `${rows.length} VIDEOS`;
    $('empty-state').hidden = rows.length > 0;
    $('video-rows').innerHTML = rows.map((row) => `<tr>
      <td><div class="video-cell"><span class="video-thumb">▷</span><div><a class="video-title" href="https://www.youtube.com/watch?v=${encodeURIComponent(row.video_id)}" target="_blank" rel="noreferrer">${escapeHtml(row.title || row.video_id)}</a><div class="video-id">${escapeHtml(row.video_id)}</div></div></div></td>
      <td>${escapeHtml(row.category_name || `Category ${row.category_id}`)}</td>
      <td class="number-cell">${fmt.format(row.views)}</td>
      <td class="engagement">${row.engagement_rate.toFixed(2)}%</td>
      <td><span class="region-chip">${escapeHtml(row.region)}</span></td>
      </tr>`).join('');
  }

  function predict() {
    if (!model) return;
    const title = $('model-title').value.trim();
    const numeric = {
      title_chars: title.length,
      title_words: title ? title.split(/\s+/).length : 0,
      publish_hour: clamp(Number($('model-hour').value), 0, 23),
      publish_weekday: clamp(Number($('model-weekday').value), 0, 6),
      video_age_days: clamp(Number($('model-age').value), 0, 3650),
      tag_count: clamp(Number($('model-tags').value), 0, 100),
    };
    const categories = { region: $('model-region').value, category_id: $('model-category').value };
    let logViews = model.intercept;
    for (const [feature, spec] of Object.entries(model.numeric)) {
      logViews += ((numeric[feature] - spec.mean) / (spec.scale || 1)) * spec.coefficient;
    }
    for (const [feature, coefficients] of Object.entries(model.categorical)) {
      logViews += coefficients[categories[feature]] || 0;
    }
    const predicted = Math.max(0, Math.expm1(clamp(logViews, 0, 30)));
    $('estimate-value').textContent = `${fmt.format(Math.round(predicted))} views`;
    $('estimate-context').textContent = `Expected historical snapshot for ${categories.region} · ${analytics.category_names[categories.category_id] || `Category ${categories.category_id}`}`;
  }

  function clamp(value, min, max) {
    return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min;
  }

  loadAssets().catch((error) => {
    console.error(error);
    $('load-error').hidden = false;
  });
})();
