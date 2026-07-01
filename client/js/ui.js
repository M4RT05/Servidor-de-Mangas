// ── ui.js — helpers visuales, skeletons, cards, filtros, búsqueda ────────────

// ── HELPERS VISUALES ─────────────────────────────────────────────────────────
function synopsisHTML(text, id) {
  const lim = 130;
  if (!text || text.length <= lim) return`<span>${text||'Sin sinopsis disponible.'}</span>`;
  const uid = 'syn_'+String(id).replace(/[^a-z0-9]/gi,'_');
  return`<span id="${uid}_s">${text.slice(0,lim)}<span class="det-mas" onclick="expandSyn('${uid}')"> ... más</span></span>
    <span id="${uid}_f" style="display:none;">${text}<span class="det-mas" onclick="collapseSyn('${uid}')"> — menos</span></span>`;
}
function expandSyn(uid)   { document.getElementById(uid+'_s').style.display='none'; document.getElementById(uid+'_f').style.display='inline'; }
function collapseSyn(uid) { document.getElementById(uid+'_s').style.display='inline'; document.getElementById(uid+'_f').style.display='none'; }

function coverImg(src, alt='', extraClass='') {
  if (!src) return '';
  return `<div class="cover-wrap${extraClass?' '+extraClass:''}">
    <div class="cover-diamond-wrap"><div class="cover-diamond"></div></div>
    <img src="${imgSrc(src)}" alt="${alt}" onload="this.closest('.cover-wrap').classList.add('cover-loaded')" onerror="this.closest('.cover-wrap').classList.add('cover-loaded')" loading="lazy">
  </div>`;
}

// ── SKELETON LOADERS ──────────────────────────────────────────────────────────
function skCapList(n=5) {
  return Array.from({length:n},()=>`
    <div class="lci sk-lci">
      <div class="lci-head" style="pointer-events:none;">
        <div class="sk-thumb sk-shine" style="width:40px;height:54px;border-radius:8px;flex-shrink:0;"></div>
        <div style="flex:1;display:flex;flex-direction:column;gap:7px;">
          <div class="sk-line sk-shine" style="width:70%;height:13px;border-radius:6px;"></div>
          <div class="sk-line sk-shine" style="width:30%;height:10px;border-radius:5px;"></div>
        </div>
      </div>
    </div>`).join('');
}
function skChapRows(n=8) {
  return Array.from({length:n},()=>`
    <div class="sk-row" style="padding:12px;margin-bottom:2px;border-radius:12px;background:var(--card);">
      <div class="sk-dot sk-shine" style="width:10px;height:10px;border-radius:50%;flex-shrink:0;"></div>
      <div style="flex:1;display:flex;flex-direction:column;gap:6px;">
        <div class="sk-line sk-shine" style="width:60%;height:12px;border-radius:6px;"></div>
        <div class="sk-line sk-shine" style="width:35%;height:10px;border-radius:5px;"></div>
      </div>
    </div>`).join('');
}

// ── CARDS ─────────────────────────────────────────────────────────────────────
function seriesCard(m) {
  const src  = m.cover ? imgSrc(m.cover) : '';
  const meta = m.metadata || {};
  const coverInner = src
    ? `<div class="cover-wrap">
        <div class="cover-diamond-wrap"><div class="cover-diamond"></div></div>
        <img src="${src}" alt="${m.name}" loading="lazy"
          onload="this.closest('.cover-wrap').classList.add('cover-loaded')"
          onerror="this.closest('.cover-wrap').classList.add('cover-loaded')">
      </div>`
    : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:40px;color:var(--mut);">M</div>`;
  return `<div class="sc rank-card" onclick="openDetail('${encodeURIComponent(m.name)}')">
    ${src ? `<img class="rank-card-bg" src="${src}" alt="" aria-hidden="true">` : ''}
    ${meta.adult ? '<div class="rank-card-badge" style="background:rgba(231,76,60,.9);color:#fff;">+18</div>' : ''}
    <div class="rank-card-cover-area">
      <div class="rank-card-cover">${coverInner}</div>
      <div class="rank-card-grad"></div>
      <div class="rank-card-footer">
        <div class="rank-card-name">${m.name}</div>
        <div class="rank-card-meta">
          ${meta.type?`<span class="${typeClass(meta.type)}">${meta.type}</span>`:''}
          ${meta.status?statusBadge(meta.status):''}
        </div>
        <div class="sc-caps-badge">${m.chapterCount} Cap${m.chapterCount!==1?'s':''}</div>
      </div>
    </div>
  </div>`;
}

function rankCard(m, i, showBadge=true) {
  const src  = m.cover ? imgSrc(m.cover) : '';
  const meta = m.metadata || {};
  const idx  = (typeof i === 'number') ? i : 0;
  const coverInner = src
    ? `<div class="cover-wrap">
        <div class="cover-diamond-wrap"><div class="cover-diamond"></div></div>
        <img src="${src}" alt="${m.name}" loading="lazy"
          onload="this.closest('.cover-wrap').classList.add('cover-loaded')"
          onerror="this.closest('.cover-wrap').classList.add('cover-loaded')">
      </div>`
    : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:40px;color:var(--mut);">M</div>`;
  return `<div class="rank-card" onclick="openDetail('${encodeURIComponent(m.name)}')">
    ${src ? `<img class="rank-card-bg" src="${src}" alt="" aria-hidden="true">` : ''}
    ${showBadge ? `<div class="rank-card-badge${idx===0?' t1':''}">
      <i class="ti ti-trending-up" style="font-size:11px;"></i> Puesto ${idx+1}
    </div>` : ''}
    <div class="rank-card-cover-area">
      <div class="rank-card-cover">${coverInner}</div>
      <div class="rank-card-grad"></div>
      <div class="rank-card-footer">
        <div class="rank-card-name">${m.name}</div>
        <div class="rank-card-meta">
          ${meta.type?`<span class="${typeClass(meta.type)}">${meta.type}</span>`:''}
          ${meta.status?statusBadge(meta.status):''}
        </div>
      </div>
    </div>
  </div>`;
}

// ── FILTROS ───────────────────────────────────────────────────────────────────
let pendingFilters = {};

function openFilter() {
  pendingFilters = JSON.parse(JSON.stringify(activeFilters));
  const genreSet = getVisibleGenres();
  document.getElementById('filter-types').innerHTML = ['Manga','Manhwa','Manhua'].map(t =>
    `<button class="filter-chip${pendingFilters.types.includes(t)?' on':''}" onclick="toggleFilterChip(this,'types','${t}')">${t}</button>`).join('');
  document.getElementById('filter-genres').innerHTML = [...genreSet].sort().map(g =>
    `<button class="filter-chip${pendingFilters.genres.includes(g)?' on':''}" onclick="toggleFilterChip(this,'genres','${g}')">${g}</button>`).join('');
  document.getElementById('filter-status').innerHTML = ['Activo','Hiatus','Finalizado'].map(s =>
    `<button class="filter-chip${pendingFilters.status.includes(s)?' on':''}" onclick="toggleFilterChip(this,'status','${s}')">${s}</button>`).join('');
  document.getElementById('filter-overlay').style.display = 'block';
  document.getElementById('filter-panel').style.display   = 'block';
  _filterPushed = true;
  history.pushState({panel:'filter'}, '');
}
function toggleFilterChip(el, cat, val) {
  el.classList.toggle('on');
  const arr = pendingFilters[cat];
  const i = arr.indexOf(val);
  i >= 0 ? arr.splice(i,1) : arr.push(val);
}
function applyFilter()  { activeFilters = pendingFilters; closeFilter(); applySeriesFilter(); }
function clearFilter()  {
  pendingFilters = {types:[],genres:[],status:[]};
  document.querySelectorAll('#filter-types .filter-chip,#filter-genres .filter-chip,#filter-status .filter-chip').forEach(e => e.classList.remove('on'));
}
function closeFilter() {
  document.getElementById('filter-overlay').style.display = 'none';
  document.getElementById('filter-panel').style.display   = 'none';
  if (_filterPushed) { _filterPushed = false; _skipPop = true; history.back(); }
}
function setSortMode(mode) { localStorage.setItem('series_sort', mode); applySeriesFilter(); }

// ── BÚSQUEDA ──────────────────────────────────────────────────────────────────
function renderSearch(q) {
  const query = (q||'').toLowerCase().trim();
  document.getElementById('sclear').style.display = query ? 'block' : 'none';
  const base = filterAdult(allMangas);
  if (!query) { document.getElementById('sresults').innerHTML = '<p style="color:var(--mut);font-size:13px;text-align:center;padding:20px 0;">Escribe para buscar...</p>'; return; }
  const results = base.filter(m => m.name.toLowerCase().includes(query));
  document.getElementById('sresults').innerHTML = results.length
    ? results.map(m => { const meta = m.metadata||{}; return`<div class="sri" onclick="openDetail('${encodeURIComponent(m.name)}')">
        <div class="sri-cov">${m.cover ? coverImg(m.cover, m.name) : ''}</div>
        <div class="sri-info"><div class="sri-name">${m.name}</div><div class="sri-meta">
          ${meta.type?`<span class="${typeClass(meta.type)}">${meta.type}</span>`:''}
          ${meta.status?statusBadge(meta.status):''}
          ${(meta.genres||[]).slice(0,2).map(g=>`<span class="b bx">${g}</span>`).join('')}
          ${meta.adult?'<span class="b b18">+18</span>':''}
        </div></div>
        <i class="ti ti-chevron-right" style="color:var(--mut);font-size:16px;flex-shrink:0;"></i>
      </div>`; }).join('')
    : `<p class="empty">Sin resultados para "${q}"</p>`;
}
function clearSearch() { document.getElementById('sinput').value=''; renderSearch(''); document.getElementById('sinput').focus(); }
