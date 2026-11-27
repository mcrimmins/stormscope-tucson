// StormScope Tucson viewer: live runs + case studies
const PRODUCTS = [
  { id: 'storm', label: 'Storm chance', sub: '≥35 dBZ within 10 km', legend: 'prob' },
  { id: 'new', label: 'New storm development', sub: 'Storm chance where none within 30 km at start', legend: 'prob' },
  { id: 'rain', label: 'Rain chance', sub: '≥20 dBZ within 10 km', legend: 'prob' },
  { id: 'strong', label: 'Strong storm cores', sub: '≥45 dBZ · first hour only', legend: 'prob', maxLead: 60 },
  { id: 'lightning', label: 'Lightning chance', sub: 'GLM flashes within 10 km', legend: 'prob' },
  { id: 'member', label: 'One possible outcome', sub: 'Ensemble member 1 reflectivity', legend: 'refl' },
];
const PROB_COLORS = ['#17344D', '#1F5784', '#2E7BBE', '#5FA9E6', '#A9D6F7', '#F2A65A'];
const REFL_COLORS = ['#04E9E7', '#019FF4', '#0300F4', '#02FD02', '#01C501', '#008E00', '#FDF802', '#E5BC00', '#FD9500', '#FD0000', '#D40000', '#BC0000', '#F800FD', '#9854C6'];
// Confidence by lead time, from the 12-event hindcast verification (FSS vs persistence)
const CONF = [
  { max: 60, key: 'high', label: 'High confidence', opacity: 0.9 },
  { max: 120, key: 'mod', label: 'Moderate confidence', opacity: 0.72 },
  { max: 9999, key: 'low', label: 'Low confidence', opacity: 0.5 },
];
const conf = (L, p) => (p && p.maxLead && L > p.maxLead)
  ? { key: 'low', label: 'Low confidence: strong cores fade after the first hour', opacity: 0.3 }
  : CONF.find(c => L <= c.max);
const MINOR = new Set(['Marana', 'Oro Valley', 'Vail', 'Benson', 'Sells']);

const S = { cases: [], data: null, live: false, product: 'storm', lead: 0, city: 'Tucson', playing: false, timer: null, compare: true, fitted: false, liveRun: null, nws: null, showWWA: true, showLSR: true };
const $ = (id) => document.getElementById(id);
const getJSON = async (u) => { const r = await fetch(u, { cache: 'no-store' }); if (!r.ok) throw new Error(u); return r.json(); };

const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';
const baseStyle = {
  version: 8,
  sources: {
    base: { type: 'raster', tileSize: 256, maxzoom: 16, attribution: 'Basemap © Esri, HERE, Garmin, USGS, NGA, EPA, USDA, NPS',
      tiles: [`${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`] },
    hillshade: { type: 'raster', tileSize: 256, maxzoom: 16, tiles: [`${ESRI}/Elevation/World_Hillshade_Dark/MapServer/tile/{z}/{y}/{x}`] },
    labels: { type: 'raster', tileSize: 256, maxzoom: 16, tiles: [`${ESRI}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`] },
  },
  layers: [
    { id: 'base', type: 'raster', source: 'base' },
    { id: 'hillshade', type: 'raster', source: 'hillshade', paint: { 'raster-opacity': 0.55 } },
  ],
};

function makeMap(el) {
  const m = new maplibregl.Map({ container: el, style: baseStyle, center: [-111.0, 32.4], zoom: 5.6, attributionControl: { compact: true }, dragRotate: false });
  m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  return m;
}
const mapL = makeMap('mapL');
const mapR = makeMap('mapR');
let syncing = false;
function sync(a, b) { a.on('move', () => { if (syncing) return; syncing = true; b.jumpTo({ center: a.getCenter(), zoom: a.getZoom() }); syncing = false; }); }
sync(mapL, mapR); sync(mapR, mapL);

function setOverlay(map, url, bounds, opacity = 0.9) {
  const src = map.getSource('ov');
  if (src) { src.updateImage({ url, coordinates: bounds }); }
  else {
    map.addSource('ov', { type: 'image', url, coordinates: bounds });
    map.addLayer({ id: 'ov', type: 'raster', source: 'ov', paint: { 'raster-fade-duration': 0, 'raster-resampling': 'nearest' } });
    map.addLayer({ id: 'labels', type: 'raster', source: 'labels' });
  }
  map.setPaintProperty('ov', 'raster-opacity', opacity);
}

let cityMarkers = [];
function drawCities() {
  cityMarkers.forEach(m => m.remove()); cityMarkers = [];
  for (const [name, ll] of Object.entries(S.data.cities)) {
    const el = document.createElement('button');
    el.className = 'city' + (name === S.city ? ' on' : '');
    el.setAttribute('aria-label', `Show timeline for ${name}`);
    el.innerHTML = MINOR.has(name) ? '<i></i>' : `<i></i>${name}`;
    el.title = name; el.dataset.city = name;
    el.onclick = () => { S.city = name; render(); };
    cityMarkers.push(new maplibregl.Marker({ element: el, anchor: 'left', offset: [-6, 0] }).setLngLat(ll).addTo(mapL));
  }
}

const preload = (urls) => (urls || []).forEach(u => { const i = new Image(); i.src = u; });
const leadText = (min) => `+${Math.floor(min / 60)}h${String(min % 60).padStart(2, '0')}`;
const obsUrl = (i) => S.live ? S.data.obs_init : S.data.products.obs[i];

function render() {
  const d = S.data; if (!d) return;
  if (!S.city || !d.points[S.city]) S.city = Object.keys(d.points)[0];
  const p = PRODUCTS.find(x => x.id === S.product);
  const L = d.leads[S.lead], c = conf(L, p);
  $('productTitle').textContent = `${p.label} · ${p.sub}`;
  $('validLabel').textContent = `Valid ${d.valid_mst[S.lead]} MST · ${leadText(L)} after a ${d.init_utc.slice(11)} UTC start`;
  $('leadLabel').textContent = `${leadText(L)} · ${d.valid_mst[S.lead]}`;
  $('confBadge').textContent = c.label;
  $('confBadge').className = 'badge ' + c.key;
  $('leftTag').textContent = `Forecast · ${p.label}`;
  $('rightTag').textContent = S.live ? 'Observed radar at start time' : 'Observed radar (MRMS)';
  $('legend').innerHTML = p.legend === 'prob'
    ? `<span>10%</span><span class="bar">${PROB_COLORS.map(c => `<i style="background:${c}"></i>`).join('')}</span><span>90%+</span>`
    : `<span>15</span><span class="bar">${REFL_COLORS.map(c => `<i style="background:${c}"></i>`).join('')}</span><span>75 dBZ</span>`;
  const go = () => {
    setOverlay(mapL, d.products[S.product][S.lead], d.bounds, c.opacity);
    nwsLayers(mapL, true); nwsFilter(mapL);
    if (S.compare && mapR.loaded()) { setOverlay(mapR, obsUrl(S.lead), d.bounds); nwsLayers(mapR, false); nwsFilter(mapR); }
  };
  mapL.loaded() ? go() : mapL.once('load', go);
  if (S.compare && !mapR.loaded()) mapR.once('load', go);
  document.querySelectorAll('.prod').forEach(b => b.classList.toggle('on', b.dataset.id === S.product));
  document.querySelectorAll('.city').forEach(b => b.classList.toggle('on', b.dataset.city === S.city));
  drawPoint(); drawTiming(); drawEventStrip();
}


// ---------- NWS warnings & storm reports ----------
const initEpoch = () => Date.parse(S.data.init_utc + 'Z') / 1000;
const validEpoch = (i = S.lead) => initEpoch() + 60 * S.data.leads[i];
function mstTime(epoch) {
  const d = new Date((epoch - 7 * 3600) * 1000);
  let h = d.getUTCHours(); const m = String(d.getUTCMinutes()).padStart(2, '0');
  const ap = h >= 12 ? 'pm' : 'am'; h = h % 12 || 12;
  return `${h}:${m} ${ap}`;
}
const EMPTY = { type: 'FeatureCollection', features: [] };
let popupsBound = false;
function nwsLayers(map, interactive) {
  const wwa = S.nws ? S.nws.warnings : EMPTY, lsr = S.nws ? S.nws.lsrs : EMPTY;
  if (map.getSource('wwa')) { map.getSource('wwa').setData(wwa); map.getSource('lsr').setData(lsr); }
  else {
    map.addSource('wwa', { type: 'geojson', data: wwa });
    map.addSource('lsr', { type: 'geojson', data: lsr });
    map.addLayer({ id: 'wwa-fill', type: 'fill', source: 'wwa', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.1 } });
    map.addLayer({ id: 'wwa-line', type: 'line', source: 'wwa',
      paint: { 'line-color': ['get', 'color'], 'line-width': ['match', ['slice', ['get', 'key'], 3], 'A', 1.2, 2.4] } });
    map.addLayer({ id: 'lsr', type: 'circle', source: 'lsr',
      paint: { 'circle-radius': 6, 'circle-color': ['get', 'color'], 'circle-stroke-color': '#0E1317', 'circle-stroke-width': 1.5 } });
  }
  if (interactive && !popupsBound) {
    popupsBound = true;
    map.on('click', 'lsr', e => {
      const p = e.features[0].properties;
      const mag = p.magnitude && p.magnitude !== 'null' ? ` · ${p.magnitude} ${p.unit && p.unit !== 'null' ? p.unit : ''}` : '';
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(
        `<strong>${p.typetext}${mag}</strong><br>${mstTime(p.t)} MST · ${p.city || ''}${p.county && p.county !== 'null' ? ', ' + p.county + ' Co.' : ''}<br><span style="color:#9DB0BF">${p.remark && p.remark !== 'null' ? p.remark : ''}</span><br><span style="color:#7F909D">Source: ${p.source || 'n/a'}</span>`).addTo(map);
    });
    map.on('click', 'wwa-fill', e => {
      if (map.queryRenderedFeatures(e.point, { layers: ['lsr'] }).length) return;
      const html = e.features.map(f => { const p = f.properties;
        return `<strong style="color:${p.color}">${p.label}</strong><br>NWS ${p.wfo === 'TWC' ? 'Tucson' : 'Phoenix'} · issued ${mstTime(p.iss)}, until ${mstTime(p.exp)} MST`; }).join('<hr style="border-color:#33414D">');
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
    });
    ['lsr', 'wwa-fill'].forEach(l => { map.on('mouseenter', l, () => map.getCanvas().style.cursor = 'pointer'); map.on('mouseleave', l, () => map.getCanvas().style.cursor = ''); });
  }
}
function nwsFilter(map) {
  if (!map.getLayer('wwa-fill')) return;
  const t = validEpoch();
  const wf = ['all', ['<=', ['get', 'iss'], t], ['>', ['get', 'exp'], t]];
  map.setFilter('wwa-fill', wf); map.setFilter('wwa-line', wf);
  map.setFilter('lsr', ['all', ['<=', ['get', 't'], t], ['>', ['get', 't'], t - 3 * 3600]]);
  map.setPaintProperty('lsr', 'circle-opacity', ['interpolate', ['linear'], ['-', t, ['get', 't']], 0, 1, 1800, 0.9, 10800, 0.35]);
  map.setPaintProperty('lsr', 'circle-stroke-opacity', ['interpolate', ['linear'], ['-', t, ['get', 't']], 0, 1, 10800, 0.4]);
  ['wwa-fill', 'wwa-line'].forEach(l => map.setLayoutProperty(l, 'visibility', S.showWWA ? 'visible' : 'none'));
  map.setLayoutProperty('lsr', 'visibility', S.showLSR ? 'visible' : 'none');
}
function nwsLegend() {
  if (!S.nws) { $('nwsLegend').innerHTML = '<span class="muted">No NWS data for this run.</span>'; return; }
  const w = new Map(), l = new Map();
  S.nws.warnings.features.forEach(f => w.set(f.properties.label, f.properties.color));
  S.nws.lsrs.features.forEach(f => l.set(f.properties.group, f.properties.color));
  $('nwsLegend').innerHTML = [...w].map(([k, c]) => `<span><i style="border-color:${c};background:${c}22"></i>${k}</span>`).join('') +
    [...l].map(([k, c]) => `<span><b style="background:${c}"></b>${k} report</span>`).join('') || '<span class="muted">None in this period.</span>';
}
function drawEventStrip() {
  const svg = $('eventStrip'), d = S.data, W = Math.max(320, svg.clientWidth || 640);
  svg.setAttribute('viewBox', `0 0 ${W} 46`);
  const t0 = initEpoch(), t1 = validEpoch(d.leads.length - 1);
  const X0 = 60, X1 = W - 14, x = t => X0 + (t - t0) / (t1 - t0) * (X1 - X0);
  let g = `<text x="0" y="16" fill="#9DB0BF" font-size="10">Warnings</text><text x="0" y="38" fill="#9DB0BF" font-size="10">Reports</text>`;
  g += `<line x1="${X0}" x2="${X1}" y1="12" y2="12" stroke="#25303A"/><line x1="${X0}" x2="${X1}" y1="34" y2="34" stroke="#25303A"/>`;
  if (S.nws) {
    S.nws.warnings.features.forEach(f => { const p = f.properties;
      if (p.key.endsWith('.A') || p.exp <= t0 || p.iss > t1) return;
      const a = x(Math.max(p.iss, t0)), b = x(Math.min(p.exp, t1));
      g += `<rect x="${a}" y="6" width="${Math.max(2, b - a)}" height="12" rx="2" fill="${p.color}" fill-opacity=".35" stroke="${p.color}"><title>${p.label} ${mstTime(p.iss)}–${mstTime(p.exp)}</title></rect>`; });
    S.nws.lsrs.features.forEach(f => { const p = f.properties;
      if (p.t < t0 || p.t > t1) return;
      g += `<circle cx="${x(p.t)}" cy="34" r="4.5" fill="${p.color}" stroke="#0E1317"><title>${p.typetext} ${mstTime(p.t)} ${p.city || ''}</title></circle>`; });
  }
  g += `<line x1="${x(validEpoch())}" x2="${x(validEpoch())}" y1="2" y2="44" stroke="#F2A65A" stroke-width="2"/>`;
  svg.innerHTML = g;
  svg.onclick = e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * (W / r.width);
    const t = t0 + (px - X0) / (X1 - X0) * (t1 - t0);
    let best = 0; d.leads.forEach((L, i) => { if (Math.abs(validEpoch(i) - t) < Math.abs(validEpoch(best) - t)) best = i; });
    stop(); S.lead = best; $('leadSlider').value = best; render();
  };
}
function drawNwsVerify() {
  const v = S.nws && S.nws.verify;
  $('nwsCard').style.display = v && (v.warnings.length || v.reports.length) ? '' : 'none';
  if (!v) return;
  const pct = x => `${Math.round(x * 100)}%`, sm = v.summary;
  const cls = x => x >= 0.5 ? 'likely' : x >= 0.3 ? 'possible' : 'unlikely';
  $('nwsSummary').innerHTML = `<strong>${sm.warnings_ge50} of ${sm.warnings}</strong> warnings/advisories issued during this forecast had a ≥50% forecast storm chance inside the polygon while in effect. ` +
    `<strong>${sm.reports_ge50} of ${sm.reports}</strong> storm reports had ≥50% forecast storm chance within 10 km (±20 min).`;
  $('warnTable').innerHTML = v.warnings.length ? `<thead><tr><th>Warning / advisory</th><th>Issued</th><th>Forecast chance<br><span>at issue time</span></th><th>Forecast chance<br><span>peak while in effect</span></th><th>Forecast signal<br><span>first ≥50% in polygon</span></th></tr></thead><tbody>` +
    v.warnings.map(r => `<tr><td>${r.label} <span class="muted">(${r.wfo})</span></td><td class="mono">${mstTime(Date.parse(r.issued) / 1000)} <span class="muted">+${Math.floor(r.lead_at_issue / 60)}h${String(r.lead_at_issue % 60).padStart(2, '0')}</span></td><td><span class="tag ${cls(r.p_at_issue)}">${pct(r.p_at_issue)}</span></td><td><span class="tag ${cls(r.p_during)}">${pct(r.p_during)}</span></td><td class="mono">${r.first_ge50_lead == null ? '—' : (r.signal_before_issue_min > 0 ? `${r.signal_before_issue_min} min before issue` : r.signal_before_issue_min === 0 ? 'at issue' : `${-r.signal_before_issue_min} min after issue`)}</td></tr>`).join('') + '</tbody>' : '';
  $('lsrTable').innerHTML = v.reports.length ? `<thead><tr><th>Storm report</th><th>Time</th><th>Place</th><th>Forecast chance<br><span>within 10 km, ±20 min</span></th></tr></thead><tbody>` +
    v.reports.map(r => `<tr><td>${r.typetext}</td><td class="mono">${mstTime(Date.parse(r.valid) / 1000)}</td><td>${r.city || ''}</td><td><span class="tag ${cls(r.p_near)}">${pct(r.p_near)}</span></td></tr>`).join('') + '</tbody>' : '';
}

// ---------- charts ----------
function path(vals, x, y) {
  let s = '', pen = false;
  (vals || []).forEach((v, i) => { if (v == null) { pen = false; return; } s += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; pen = true; });
  return s;
}
function confBands(leads, x, yTop, yBot, labels = true) {
  let g = '';
  const idx = (m) => leads.findIndex(L => L > m);
  const edges = [0, idx(60), idx(120), leads.length - 1].map(i => i < 0 ? leads.length - 1 : i);
  const fills = ['rgba(255,255,255,0)', 'rgba(255,255,255,0.035)', 'rgba(255,255,255,0.07)'];
  const names = ['High', 'Moderate', 'Low'];
  for (let k = 0; k < 3; k++) {
    const a = x(edges[k]), b = x(edges[k + 1]);
    if (b <= a) continue;
    g += `<rect x="${a}" y="${yTop}" width="${b - a}" height="${yBot - yTop}" fill="${fills[k]}"/>`;
    if (labels) g += `<text x="${(a + b) / 2}" y="${yTop + 11}" text-anchor="middle" fill="#7F909D" font-size="9.5">${names[k]}</text>`;
  }
  return g;
}

function drawPoint() {
  const d = S.data, pt = d.points[S.city], n = d.leads.length;
  const svg = $('pointChart'), W = Math.max(280, svg.clientWidth || 320);
  svg.setAttribute('viewBox', `0 0 ${W} 180`);
  const X0 = 34, X1 = W - 8, Y0 = 140, H = 124;
  const x = i => X0 + (i / Math.max(1, n - 1)) * (X1 - X0), y = v => Y0 - v * H;
  const storm = pt.storm, light = pt.lightning;
  const peak = Math.max(...storm.map(v => v ?? 0)), pi = storm.indexOf(peak);
  let g = confBands(d.leads, x, y(1), Y0);
  [0, .5, 1].forEach(v => g += `<line x1="${X0}" x2="${X1}" y1="${y(v)}" y2="${y(v)}" stroke="${v ? '#25303A' : '#3A4854'}"/><text x="${X0 - 6}" y="${y(v) + 3}" text-anchor="end" fill="#9DB0BF" font-size="10">${v === 1 ? '100%' : v * 100}</text>`);
  g += `<line x1="${x(S.lead)}" x2="${x(S.lead)}" y1="${y(1)}" y2="${Y0}" stroke="#F2A65A" stroke-opacity=".5" stroke-width="2"/>`;
  g += `<path d="${path(storm, x, y)} L${X1} ${Y0} L${X0} ${Y0} Z" fill="#5FA9E6" fill-opacity=".16"/>`;
  g += `<path d="${path(storm, x, y)}" fill="none" stroke="#5FA9E6" stroke-width="2.5"/>`;
  g += `<path d="${path(light, x, y)}" fill="none" stroke="#F2A65A" stroke-width="2" stroke-dasharray="5 4"/>`;
  (pt.obs_storm || []).forEach((o, i) => { if (o) g += `<circle cx="${x(i)}" cy="${Y0 + 10}" r="3.2" fill="#E6EDF2"/>`; });
  d.leads.forEach((L, i) => { if (L % 60 === 0) g += `<text x="${x(i)}" y="172" text-anchor="middle" fill="#9DB0BF" font-size="10">${d.valid_mst[i].replace(':00', '')}</text>`; });
  svg.innerHTML = g;
  $('pointName').textContent = S.city;
  const obsAny = (pt.obs_storm || []).some(Boolean);
  $('pointSummary').textContent = `Peak storm chance ${Math.round(peak * 100)}% around ${d.valid_mst[pi]}.` +
    (S.live ? '' : obsAny ? ' Storms were observed within 10 km during this period.' : ' No storms were observed within 10 km.');
  $('obsKey').style.display = S.live ? 'none' : '';
}

// timing table: when does each place get storms / lightning?
function timing(pt, d) {
  const t = d.valid_mst, s = pt.storm.map(v => v ?? 0), l = pt.lightning.map(v => v ?? 0);
  const first = s.findIndex(v => v > 0);
  const win = (th) => { const i = s.findIndex(v => v >= th); if (i < 0) return null; let j = s.length - 1; while (s[j] < th) j--; return [i, j]; };
  const likely = win(0.5), possible = win(0.3);
  const peak = Math.max(...s), pi = s.indexOf(peak);
  const lt = l.map((v, i) => v >= 0.3 ? i : -1).filter(i => i >= 0);
  const lEnd = lt.length ? (lt[lt.length - 1] === l.length - 1 ? `after ${t[t.length - 1]}` : t[lt[lt.length - 1] + 1]) : '—';
  const o = (pt.obs_storm || []).findIndex(Boolean);
  return {
    first: first < 0 ? '—' : t[first],
    window: likely ? `Likely ${t[likely[0]]}–${t[likely[1]]}` : possible ? `Possible ${t[possible[0]]}–${t[possible[1]]}` : 'Unlikely',
    cls: likely ? 'likely' : possible ? 'possible' : 'unlikely',
    peak, peakAt: t[pi], lEnd, obs: o < 0 ? 'None' : t[o],
  };
}
function drawTiming() {
  const d = S.data;
  const rows = Object.entries(d.points).map(([c, p]) => [c, timing(p, d)]).sort((a, b) => b[1].peak - a[1].peak);
  $('timingTable').innerHTML = `<thead><tr><th>Place</th><th>First chance<br><span>any member</span></th><th>Storm window<br><span>≥50% likely · ≥30% possible</span></th><th>Peak</th><th>Lightning risk ends<br><span>below 30%</span></th>${S.live ? '' : '<th>Observed storm<br><span>first within 10 km</span></th>'}</tr></thead><tbody>` +
    rows.map(([c, r]) => `<tr class="${c === S.city ? 'sel' : ''}" data-city="${c}"><td><button class="link">${c}</button></td><td class="mono">${r.first}</td><td><span class="tag ${r.cls}">${r.window}</span></td><td class="mono">${Math.round(r.peak * 100)}% <span class="muted">${r.peak > 0 ? r.peakAt : ''}</span></td><td class="mono">${r.lEnd}</td>${S.live ? '' : `<td class="mono">${r.obs}</td>`}</tr>`).join('') + '</tbody>';
  document.querySelectorAll('#timingTable tr[data-city] button').forEach(b => b.onclick = () => { S.city = b.closest('tr').dataset.city; render(); });
}

function drawSkill() {
  const d = S.data, sc = d.scores;
  $('skillCard').style.display = sc ? '' : 'none';
  if (!sc) return;
  const svg = $('skillChart'), W = Math.max(320, svg.clientWidth || 640), n = d.leads.length;
  svg.setAttribute('viewBox', `0 0 ${W} 200`);
  const X0 = 40, X1 = W - 14, Y0 = 170, H = 158;
  const x = i => X0 + (i / Math.max(1, n - 1)) * (X1 - X0), y = v => Y0 - Math.max(0, v) * H;
  let g = confBands(d.leads, x, y(1), Y0);
  [0, .25, .5, .75, 1].forEach(v => g += `<line x1="${X0}" x2="${X1}" y1="${y(v)}" y2="${y(v)}" stroke="${v ? '#25303A' : '#3A4854'}"/><text x="${X0 - 6}" y="${y(v) + 3}" text-anchor="end" fill="#9DB0BF" font-size="10">${v}</text>`);
  d.leads.forEach((L, i) => { if (L % 30 === 0) g += `<text x="${x(i)}" y="190" text-anchor="middle" fill="#9DB0BF" font-size="10">${leadText(L)}</text>`; });
  [['fss20', '#5FA9E6', ''], ['pers20', '#5FA9E6', '5 4'], ['fss35', '#F2A65A', ''], ['pers35', '#F2A65A', '5 4']].forEach(([k, c, dash]) =>
    g += `<path d="${path(sc[k], x, y)}" fill="none" stroke="${c}" stroke-width="2.2" stroke-dasharray="${dash}"/>`);
  svg.innerHTML = g;
}

// ---------- controls ----------
$('productList').innerHTML = PRODUCTS.map(p => `<button class="prod" data-id="${p.id}">${p.label}<span>${p.sub}</span></button>`).join('');
document.querySelectorAll('.prod').forEach(b => b.onclick = () => {
  S.product = b.dataset.id; preload(S.data.products[S.product]);
  const p = PRODUCTS.find(x => x.id === S.product);
  if (p.maxLead && S.data.leads[S.lead] > p.maxLead) { S.lead = Math.max(0, S.data.leads.indexOf(p.maxLead)); $('leadSlider').value = S.lead; }
  render();
});
$('leadSlider').oninput = e => { stop(); S.lead = +e.target.value; render(); };
function stop() { clearInterval(S.timer); S.playing = false; $('playBtn').textContent = '▶'; $('playBtn').setAttribute('aria-label', 'Play loop'); }
$('playBtn').onclick = () => {
  if (S.playing) return stop();
  S.playing = true; $('playBtn').textContent = '❚❚'; $('playBtn').setAttribute('aria-label', 'Pause loop');
  S.timer = setInterval(() => { S.lead = (S.lead + 1) % S.data.leads.length; $('leadSlider').value = S.lead; render(); }, 600);
};
$('compareToggle').onchange = e => {
  S.compare = e.target.checked;
  $('rightWrap').style.display = S.compare ? '' : 'none';
  $('maps').classList.toggle('single', !S.compare);
  setTimeout(() => { mapL.resize(); mapR.resize(); render(); }, 50);
};
$('caseSelect').onchange = e => loadCase(e.target.value);
$('wwaToggle').onchange = e => { S.showWWA = e.target.checked; render(); };
$('lsrToggle').onchange = e => { S.showLSR = e.target.checked; render(); };

async function loadCase(name, keepLead = false) {
  stop();
  if (name === '__live') {
    const idx = await getJSON('data/live/index.json');
    S.liveRun = idx.runs[0];
    S.data = await getJSON(S.liveRun); S.live = true;
  } else {
    S.data = await getJSON(`data/${name}.json`); S.live = false;
  }
  const d = S.data;
  S.nws = null;
  if (d.nws) { try { S.nws = await getJSON(d.nws); } catch (e) { S.nws = null; } }
  if (mapL.getSource('wwa')) nwsLayers(mapL, true);
  if (mapR.getSource('wwa')) nwsLayers(mapR, false);
  nwsLegend(); drawNwsVerify();
  if (!keepLead) S.lead = Math.max(0, d.leads.indexOf(60));
  S.lead = Math.min(S.lead, d.leads.length - 1);
  const sl = $('leadSlider'); sl.max = d.leads.length - 1; sl.value = S.lead;
  $('modeTag').textContent = S.live ? 'Live · updated every 30 min' : 'Case-study mode · past event';
  $('caseHead').textContent = S.live ? 'This run' : 'This case';
  $('caseNote').textContent = S.live ? 'Latest experimental forecast run.' : (d.note || '');
  const c = S.cases.find(c => c.name === name);
  $('caseMeta').textContent = S.live
    ? `Start ${d.init_utc.replace('T', ' ')} UTC · finished ${d.made_utc.replace('T', ' ')} UTC · ${d.members} members`
    : `Start ${c ? c.init_mst : ''} (${d.init_utc.replace('T', ' ')} UTC) · ${d.members} members`;
  preload([...d.products[S.product], ...(d.products.obs || [])]);
  drawCities(); drawSkill(); render();
  if (!S.fitted) {
    const b = d.bounds, fit = () => mapL.fitBounds([b[3], b[1]], { padding: 10, duration: 0 });
    mapL.loaded() ? fit() : mapL.once('load', fit);
    S.fitted = true;
  }
}

// live mode: check for a newer run every 2 minutes
setInterval(async () => {
  if (!S.live) return;
  try { const idx = await getJSON('data/live/index.json'); if (idx.runs[0] !== S.liveRun) loadCase('__live', true); } catch (e) { /* offline */ }
}, 120000);

window.addEventListener('resize', () => { if (S.data) { drawSkill(); drawPoint(); drawEventStrip(); } });

(async function init() {
  const idx = await getJSON('data/cases.json').catch(() => ({ cases: [] }));
  S.cases = idx.cases;
  let hasLive = false;
  try { const li = await getJSON('data/live/index.json'); hasLive = li.runs && li.runs.length > 0; } catch (e) { /* no live runs yet */ }
  const opts = (hasLive ? ['<option value="__live">Latest live run</option>'] : [])
    .concat(idx.cases.map(c => `<option value="${c.name}">${c.init_mst} — ${c.note.slice(0, 60)}</option>`));
  $('caseSelect').innerHTML = opts.join('');
  const want = new URLSearchParams(location.search).get('case');
  const start = want || (hasLive ? '__live' : ((idx.cases.find(c => c.name.startsWith('2026-08-05')) || idx.cases[0] || {}).name));
  if (!start) return;
  $('caseSelect').value = start;
  loadCase(start);
})();
