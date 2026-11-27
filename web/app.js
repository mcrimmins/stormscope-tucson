// StormScope Tucson – case-study viewer
const PRODUCTS = [
  { id: 'storm', label: 'Storm chance', sub: '≥35 dBZ within 10 km', legend: 'prob' },
  { id: 'rain', label: 'Rain chance', sub: '≥20 dBZ within 10 km', legend: 'prob' },
  { id: 'strong', label: 'Strong storm cores', sub: '≥45 dBZ · trust first hour only', legend: 'prob' },
  { id: 'lightning', label: 'Lightning chance', sub: 'GLM flashes within 10 km', legend: 'prob' },
  { id: 'member', label: 'One possible outcome', sub: 'Ensemble member 1 reflectivity', legend: 'refl' },
];
const PROB_COLORS = ['#17344D', '#1F5784', '#2E7BBE', '#5FA9E6', '#A9D6F7', '#F2A65A'];
const REFL_COLORS = ['#04E9E7', '#019FF4', '#0300F4', '#02FD02', '#01C501', '#008E00', '#FDF802', '#E5BC00', '#FD9500', '#FD0000', '#D40000', '#BC0000', '#F800FD', '#9854C6'];

const S = { cases: [], data: null, product: 'storm', lead: 0, city: 'Tucson', playing: false, timer: null, compare: true };
const $ = (id) => document.getElementById(id);

const baseStyle = {
  version: 8,
  sources: {
    base: { type: 'raster', tileSize: 256, attribution: '© OpenStreetMap contributors © CARTO',
      tiles: ['a', 'b', 'c'].map(s => `https://${s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png`) },
    labels: { type: 'raster', tileSize: 256,
      tiles: ['a', 'b', 'c'].map(s => `https://${s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}.png`) },
  },
  layers: [{ id: 'base', type: 'raster', source: 'base' }],
};

function makeMap(el) {
  const m = new maplibregl.Map({ container: el, style: baseStyle, center: [-111.0, 32.4], zoom: 5.6,
    attributionControl: { compact: true }, dragRotate: false });
  m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  return m;
}
const mapL = makeMap('mapL');
const mapR = makeMap('mapR');

// keep both maps in sync
let syncing = false;
function sync(a, b) {
  a.on('move', () => { if (syncing) return; syncing = true; b.jumpTo({ center: a.getCenter(), zoom: a.getZoom() }); syncing = false; });
}
sync(mapL, mapR); sync(mapR, mapL);

function setOverlay(map, url, bounds) {
  const src = map.getSource('ov');
  if (src) { src.updateImage({ url, coordinates: bounds }); return; }
  map.addSource('ov', { type: 'image', url, coordinates: bounds });
  map.addLayer({ id: 'ov', type: 'raster', source: 'ov', paint: { 'raster-fade-duration': 0, 'raster-resampling': 'nearest' } });
  map.addLayer({ id: 'labels', type: 'raster', source: 'labels' });
}

let cityMarkers = [];
function drawCities() {
  cityMarkers.forEach(m => m.remove());
  cityMarkers = [];
  for (const [name, ll] of Object.entries(S.data.cities)) {
    const el = document.createElement('button');
    el.className = 'city' + (name === S.city ? ' on' : '');
    el.setAttribute('aria-label', `Show timeline for ${name}`);
    el.innerHTML = `<i></i>${name}`;
    el.onclick = () => { S.city = name; render(); };
    cityMarkers.push(new maplibregl.Marker({ element: el, anchor: 'left', offset: [-6, 0] }).setLngLat(ll).addTo(mapL));
  }
}

function preload(urls) { urls.forEach(u => { const i = new Image(); i.src = u; }); }

function leadText(min) { const h = Math.floor(min / 60), m = min % 60; return `+${h}h${String(m).padStart(2, '0')}`; }

function render() {
  const d = S.data; if (!d) return;
  const p = PRODUCTS.find(x => x.id === S.product);
  const L = d.leads[S.lead];
  $('productTitle').textContent = `${p.label} · ${p.sub}`;
  $('validLabel').textContent = `Valid ${d.valid_mst[S.lead]} MST · ${leadText(L)} after a ${d.init_utc.slice(11)} UTC start`;
  $('leadLabel').textContent = `${leadText(L)} · ${d.valid_mst[S.lead]}`;
  $('leftTag').textContent = `Forecast · ${p.label}`;
  $('legend').innerHTML = p.legend === 'prob'
    ? `<span>10%</span><span class="bar">${PROB_COLORS.map(c => `<i style="background:${c}"></i>`).join('')}</span><span>90%+</span>`
    : `<span>15</span><span class="bar">${REFL_COLORS.map(c => `<i style="background:${c}"></i>`).join('')}</span><span>75 dBZ</span>`;
  const go = () => {
    setOverlay(mapL, d.products[S.product][S.lead], d.bounds);
    if (S.compare) setOverlay(mapR, d.products.obs[S.lead], d.bounds);
  };
  mapL.loaded() ? go() : mapL.once('load', go);
  if (S.compare && !mapR.loaded()) mapR.once('load', go);
  document.querySelectorAll('.prod').forEach(b => b.classList.toggle('on', b.dataset.id === S.product));
  document.querySelectorAll('.city').forEach(b => b.classList.toggle('on', b.textContent === S.city));
  drawPoint(); drawRanked();
}

// ---------- charts ----------
function path(vals, x, y) {
  let s = '', pen = false;
  vals.forEach((v, i) => { if (v == null) { pen = false; return; } s += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; pen = true; });
  return s;
}

function drawPoint() {
  const d = S.data, pt = d.points[S.city], n = d.leads.length;
  const X0 = 34, X1 = 312, Y0 = 140, H = 124;
  const x = i => X0 + (i / Math.max(1, n - 1)) * (X1 - X0), y = v => Y0 - v * H;
  const storm = pt.storm, light = pt.lightning;
  const peak = Math.max(...storm.map(v => v ?? 0)), pi = storm.indexOf(peak);
  const ticks = d.leads.map((L, i) => [L, i]).filter(([L]) => L % 60 === 0);
  let g = '';
  [0, .25, .5, .75, 1].forEach(v => g += `<line x1="${X0}" x2="${X1}" y1="${y(v)}" y2="${y(v)}" stroke="${v ? '#25303A' : '#3A4854'}"/>`);
  g += `<text x="${X0 - 6}" y="${y(0) + 3}" text-anchor="end" fill="#9DB0BF" font-size="10">0</text><text x="${X0 - 6}" y="${y(.5) + 3}" text-anchor="end" fill="#9DB0BF" font-size="10">50</text><text x="${X0 - 6}" y="${y(1) + 3}" text-anchor="end" fill="#9DB0BF" font-size="10">100%</text>`;
  g += `<line x1="${x(S.lead)}" x2="${x(S.lead)}" y1="${y(1)}" y2="${Y0}" stroke="#F2A65A" stroke-opacity=".5" stroke-width="2"/>`;
  g += `<path d="${path(storm, x, y)} L${X1} ${Y0} L${X0} ${Y0} Z" fill="#5FA9E6" fill-opacity=".16"/>`;
  g += `<path d="${path(storm, x, y)}" fill="none" stroke="#5FA9E6" stroke-width="2.5"/>`;
  g += `<path d="${path(light, x, y)}" fill="none" stroke="#F2A65A" stroke-width="2" stroke-dasharray="5 4"/>`;
  pt.obs_storm.forEach((o, i) => { if (o) g += `<circle cx="${x(i)}" cy="${Y0 + 10}" r="3.2" fill="#E6EDF2"/>`; });
  ticks.forEach(([L, i]) => g += `<text x="${x(i)}" y="170" text-anchor="middle" fill="#9DB0BF" font-size="10">${d.valid_mst[i].replace(':00', '')}</text>`);
  $('pointChart').innerHTML = g;
  $('pointName').textContent = S.city;
  const obsAny = pt.obs_storm.some(Boolean);
  $('pointSummary').textContent = `Peak storm chance ${Math.round(peak * 100)}% around ${d.valid_mst[pi]}. ` +
    (obsAny ? 'Storms were observed within 10 km during this period.' : 'No storms were observed within 10 km.');
}

function drawRanked() {
  const d = S.data;
  const rows = Object.entries(d.points).map(([c, p]) => [c, Math.max(...p.storm.map(v => v ?? 0)), p.obs_storm.some(Boolean)])
    .sort((a, b) => b[1] - a[1]);
  $('ranked').innerHTML = rows.map(([c, v, o]) =>
    `<button class="rank" data-city="${c}"><span class="n">${c}${o ? ' ●' : ''}</span><span class="b"><i style="width:${v * 100}%;background:${c === S.city ? '#F2A65A' : '#5FA9E6'}"></i></span><span class="v">${Math.round(v * 100)}%</span></button>`).join('') +
    `<p class="muted small" style="margin:8px 0 0">● storm observed within 10 km</p>`;
  document.querySelectorAll('.rank').forEach(b => b.onclick = () => { S.city = b.dataset.city; render(); });
}

function drawSkill() {
  const d = S.data, sc = d.scores, n = d.leads.length;
  const X0 = 40, X1 = 628, Y0 = 170, H = 158;
  const x = i => X0 + (i / Math.max(1, n - 1)) * (X1 - X0), y = v => Y0 - Math.max(0, v) * H;
  let g = '';
  [0, .25, .5, .75, 1].forEach(v => g += `<line x1="${X0}" x2="${X1}" y1="${y(v)}" y2="${y(v)}" stroke="${v ? '#25303A' : '#3A4854'}"/><text x="${X0 - 6}" y="${y(v) + 3}" text-anchor="end" fill="#9DB0BF" font-size="10">${v}</text>`);
  d.leads.forEach((L, i) => { if (L % 30 === 0) g += `<text x="${x(i)}" y="190" text-anchor="middle" fill="#9DB0BF" font-size="10">${leadText(L)}</text>`; });
  [['fss20', '#5FA9E6', ''], ['pers20', '#5FA9E6', '5 4'], ['fss35', '#F2A65A', ''], ['pers35', '#F2A65A', '5 4']].forEach(([k, c, dash]) =>
    g += `<path d="${path(sc[k], x, y)}" fill="none" stroke="${c}" stroke-width="2.2" stroke-dasharray="${dash}"/>`);
  $('skillChart').innerHTML = g;
}

// ---------- controls ----------
$('productList').innerHTML = PRODUCTS.map(p => `<button class="prod" data-id="${p.id}">${p.label}<span>${p.sub}</span></button>`).join('');
document.querySelectorAll('.prod').forEach(b => b.onclick = () => { S.product = b.dataset.id; preload(S.data.products[S.product]); render(); });

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

async function loadCase(name) {
  stop();
  S.data = await (await fetch(`data/${name}.json`)).json();
  S.lead = Math.min(S.data.leads.length - 1, S.data.leads.indexOf(60) >= 0 ? S.data.leads.indexOf(60) : 0);
  const sl = $('leadSlider'); sl.max = S.data.leads.length - 1; sl.value = S.lead;
  $('caseNote').textContent = S.data.note || '';
  const c = S.cases.find(c => c.name === name);
  $('caseMeta').textContent = `Start ${c ? c.init_mst : ''} (${S.data.init_utc.replace('T', ' ')} UTC) · ${S.data.members} members`;
  preload([...S.data.products[S.product], ...S.data.products.obs]);
  drawCities(); drawSkill(); render();
}

(async function init() {
  const idx = await (await fetch('data/cases.json')).json();
  S.cases = idx.cases;
  $('caseSelect').innerHTML = idx.cases.map(c => `<option value="${c.name}">${c.init_mst} — ${c.note.slice(0, 60)}</option>`).join('');
  const start = idx.cases.find(c => c.name.startsWith('2026-08-05')) || idx.cases[0];
  $('caseSelect').value = start.name;
  loadCase(start.name);
})();
