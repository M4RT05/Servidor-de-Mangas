// ── app.js — estado global, renders principales, nav, temas, init ────────────
// Módulos: api.js → ui.js → detail.js → app.js

// ── NORMALIZACIÓN ─────────────────────────────────────────────────────────────
function norm(str) { return String(str).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim(); }

// ── ESTADO GLOBAL ─────────────────────────────────────────────────────────────
let allMangas     = [];
let currentManga  = null;
let fromPage      = 'inicio';
let chapSortAsc   = false;
let activeFilters = { types: [], genres: [], status: [], search: '' };
let chapSearchVisible = false;
let _filterPushed   = false;
let _userPushed     = false;
let _skipPop        = false;
let capPage         = 1;

const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

// ── HELPERS ───────────────────────────────────────────────────────────────────
function imgSrc(src)      { return API.imgSrc(src); }
function isAdultEnabled() { return localStorage.getItem('adult_content') === 'true'; }
function isPC()           { return window.innerWidth >= 768; }

function filterAdult(list) {
  if (isAdultEnabled()) return list;
  const adultGenres = ['hentai','ecchi','adultos','+18','adult','18+'];
  return list.filter(m => {
    if (m.metadata?.adult || m.adult) return false;
    const genres = (m.metadata?.genres || []).map(g => g.toLowerCase());
    return !genres.some(g => adultGenres.includes(g));
  });
}
function getVisibleGenres() {
  const genreSet = new Set();
  filterAdult(allMangas).forEach(m => (m.metadata?.genres || []).forEach(g => genreSet.add(g)));
  return genreSet;
}
function chLabel(str) {
  const m = String(str).match(/(\d+(?:\.\d+)?)/);
  if (!m) return 'Capítulo ' + str;
  const n = parseFloat(m[1]);
  return 'Capítulo ' + (Number.isInteger(n) ? n : n);
}
function typeClass(tp)  { if(tp==='Manhwa')return'b type-manhwa'; if(tp==='Manhua')return'b type-manhua'; return'b type-manga'; }
function statusBadge(s) {
  if(!s) return '';
  const sl = s.toLowerCase();
  if(sl==='activo'||sl==='en emisión') return`<span class="b s-activo">Activo</span>`;
  if(sl==='hiatus')     return`<span class="b s-hiatus">Hiatus</span>`;
  if(sl==='finalizado') return`<span class="b s-finalizado">Finalizado</span>`;
  return`<span class="b bx">${s}</span>`;
}
function showPage(name) {
  document.querySelectorAll('.pg').forEach(p => { p.style.display='none'; p.classList.remove('on'); });
  const pg = document.getElementById('p-'+name);
  if (pg) { pg.style.display='block'; pg.classList.add('on'); }
  document.getElementById('cnt').scrollTop = 0;
}
function logout() { localStorage.clear(); window.location.href='/login.html'; }
function toggleAdult(el) {
  el.classList.toggle('on');
  localStorage.setItem('adult_content', el.classList.contains('on') ? 'true' : 'false');
  renderHome(); renderSeries(); renderRankings(); renderCapitulos(1);
  renderSearch(document.getElementById('sinput').value);
}

// ── INICIO ────────────────────────────────────────────────────────────────────
function renderHome() {
  const visible    = filterAdult(allMangas);
  const inProgress = visible.filter(m => {
    const rc = m.progress?.readChapters?.length || 0;
    return rc > 0 && rc < m.chapterCount;
  }).slice(0, 8);

  document.getElementById('cont-reading').innerHTML = inProgress.length === 0
    ? '<p class="loading" style="grid-column:1/-1;">Aún no has leído ningún manga.</p>'
    : inProgress.map(m => {
        const pct = Math.round((m.progress.readChapters.length / m.chapterCount) * 100);
        const src = m.cover ? imgSrc(m.cover) : '';
        return`<div class="ri ri-with-bg" onclick="openDetail('${encodeURIComponent(m.name)}')">
          ${src ? `<img class="ri-bg" src="${src}" alt="" aria-hidden="true">` : ''}
          <div class="rcov" style="position:relative;z-index:1;">${m.cover ? coverImg(m.cover, m.name) : ''}</div>
          <div style="flex:1;min-width:0;overflow:hidden;position:relative;z-index:1;">
            <div class="ri-title">${m.name}</div>
            <div class="ri-sub" style="font-size:12px;color:var(--mut);">Cap. ${chLabel(m.progress.lastChapter)} · ${pct}%</div>
            <div class="prbar"><div class="prfill" style="width:${pct}%;"></div></div>
          </div>
        </div>`;
      }).join('');

  const recent = [...visible].filter(m => m.addedDate).sort((a,b) => new Date(b.addedDate)-new Date(a.addedDate)).slice(0,10);
  document.getElementById('recent-grid').innerHTML = recent.length
    ? recent.map(m => rankCard(m, 0, false)).join('')
    : '<p class="empty">No hay mangas recientes.</p>';
}

// ── SERIES ────────────────────────────────────────────────────────────────────
function renderSeries() { applySeriesFilter(); }
function applySeriesFilter() {
  let list = filterAdult(allMangas);
  const f  = activeFilters;
  if (f.types.length)  list = list.filter(m => f.types.includes(m.metadata?.type));
  if (f.search) { const q = norm(f.search); list = list.filter(m => norm(m.name).includes(q)||(m.metadata?.genres||[]).some(g => norm(g).includes(q))); }
  if (f.genres.length) list = list.filter(m => (m.metadata?.genres||[]).some(g => f.genres.includes(g)));
  if (f.status.length) list = list.filter(m => f.status.includes(m.metadata?.status));
  const sortMode = localStorage.getItem('series_sort') || 'az';
  if      (sortMode==='az')    list.sort((a,b) => norm(a.name).localeCompare(norm(b.name)));
  else if (sortMode==='za')    list.sort((a,b) => norm(b.name).localeCompare(norm(a.name)));
  else if (sortMode==='caps')  list.sort((a,b) => b.chapterCount-a.chapterCount);
  else if (sortMode==='added') list.sort((a,b) => new Date(b.addedDate||0)-new Date(a.addedDate||0));
  document.getElementById('sgrid').innerHTML = list.length ? list.map(seriesCard).join('') : '<p class="empty">Sin resultados.</p>';
  document.getElementById('scnt').textContent = `Mostrando ${list.length} series`;
  const total = f.types.length + f.genres.length + f.status.length;
  const btn   = document.getElementById('filter-btn');
  btn.innerHTML = total > 0
    ? `<i class="ti ti-adjustments-horizontal" style="font-size:16px;"></i> Filtrar <span style="background:var(--acc);color:var(--acc-text);border-radius:50%;width:18px;height:18px;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;">${total}</span>`
    : `<i class="ti ti-adjustments-horizontal" style="font-size:16px;"></i> Filtrar`;
}
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('sort-sel');
  if (sel) sel.value = localStorage.getItem('series_sort') || 'az';
});

// ── RANKINGS ──────────────────────────────────────────────────────────────────
function renderRankings() {
  const visible = filterAdult(allMangas);
  const sorted  = [...visible].sort((a,b) => {
    const ra = a.metadata?.ranking ?? 9999, rb = b.metadata?.ranking ?? 9999;
    return ra !== rb ? ra - rb : b.chapterCount - a.chapterCount;
  });
  const rlistEl = document.getElementById('rlist');
  rlistEl.innerHTML = '<div id="rlist-mobile-grid">' + sorted.map((m,i) => rankCard(m, i)).join('') + '</div>';
  const rPC = document.getElementById('rlist-pc');
  if (rPC) rPC.innerHTML = sorted.map((m, i) => rankCard(m, i)).join('');
}

// ── CAPÍTULOS ─────────────────────────────────────────────────────────────────
async function renderCapitulos(page=1) {
  capPage = page;
  const clist = document.getElementById('clist');
  if (!clist) return;
  clist.innerHTML = skCapList(5);
  document.getElementById('cap-pagination').innerHTML = '';
  try {
    const adult = isAdultEnabled();
    const res   = await API.fetchRaw(`/api/mangas/latest-paged?page=${page}&limit=20&adult=${adult}`);
    if (!res) { clist.innerHTML = '<p class="empty" style="grid-column:1/-1;">No se pudo conectar al servidor.</p>'; return; }
    const data = await res.json();
    const {items, total, totalPages} = data;
    if (!items || items.length === 0) { clist.innerHTML = '<p class="empty">No hay capítulos recientes.</p>'; renderCapPagination(0,0,0); return; }
    const itemsHTML = items.map(g => `
      <div class="lci">
        ${g.cover?`<img class="lci-bg" src="${imgSrc(g.cover)}" alt="">` : ''}
        <div class="lci-head" onclick="openDetail('${encodeURIComponent(g.manga)}')">
          <div class="lci-cov">${g.cover ? coverImg(g.cover, g.manga) : ''}</div>
          <div class="lci-title">${g.manga}</div>
          ${statusBadge(g.status)}
        </div>
        ${g.chapters.map(ch => `
        <div class="lci-ch" onclick="openReader('${encodeURIComponent(g.manga)}','${ch.chapter}')">
          <div class="dot ${ch.read?'r':'u'}" style="margin-right:10px;"></div>
          <div style="flex:1;"><div style="font-size:13px;font-weight:600;color:var(--text);">${chLabel(ch.chapter)}</div></div>
          <div style="font-size:12px;color:var(--mut);display:flex;align-items:center;gap:3px;"><i class="ti ti-calendar" style="font-size:12px;"></i>${ch.dateLabel}</div>
        </div>`).join('')}
      </div>`).join('');
    if (isPC()) { clist.style.display='grid'; clist.style.gridTemplateColumns='repeat(2,1fr)'; clist.style.gap='12px'; }
    else { clist.style.display=''; clist.style.gridTemplateColumns=''; clist.style.gap=''; }
    clist.innerHTML = itemsHTML;
    renderCapPagination(page, totalPages, total);
    document.getElementById('cnt').scrollTop = 0;
  } catch(err) {
    console.error('renderCapitulos error:', err);
    clist.style.display = '';
    clist.style.gridTemplateColumns = '';
    clist.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:32px 16px;">
      <div style="font-size:32px;margin-bottom:12px;">⚠️</div>
      <div style="font-size:14px;color:var(--mut);margin-bottom:16px;">No se pudo conectar al servidor.<br>Asegúrate de que el servidor esté encendido.</div>
      <button onclick="renderCapitulos(1)" style="padding:10px 24px;border-radius:12px;border:none;background:var(--acc);color:var(--acc-text);font-size:14px;font-weight:700;cursor:pointer;">Reintentar</button>
    </div>`;
    document.getElementById('cap-pagination').innerHTML = '';
  }
}

function renderCapPagination(current, total, totalItems) {
  const container = document.getElementById('cap-pagination');
  if (!container || total <= 1) { if(container) container.innerHTML=''; return; }
  const perPage = 20;
  const from = (current-1)*perPage+1, to = Math.min(current*perPage, totalItems);
  const nums = total <= 12
    ? Array.from({length:total},(_,i)=>i+1)
    : [1,2,3,4,5,6,7,8,9,10,'...',total-1,total];
  const btns = nums.map(p => p==='...'
    ? `<span class="cap-pg-dots">…</span>`
    : `<button class="cap-pg-btn${p===current?' on':''}" onclick="renderCapitulos(${p})">${p}</button>`).join('');
  container.innerHTML = `
    <div class="cap-pg-info">Mostrando <b>${from}</b> a <b>${to}</b> de <b>${totalItems}</b> Series</div>
    <div class="cap-pg-row">
      <button class="cap-pg-arrow" onclick="renderCapitulos(${current-1})" ${current===1?'disabled':''}><i class="ti ti-chevron-left"></i></button>
      ${btns}
      <button class="cap-pg-arrow" onclick="renderCapitulos(${current+1})" ${current===total?'disabled':''}><i class="ti ti-chevron-right"></i></button>
    </div>`;
}

// ── BÚSQUEDA (listener) ───────────────────────────────────────────────────────
document.getElementById('sinput').addEventListener('input', e => renderSearch(e.target.value));

// ── NAVEGACIÓN ────────────────────────────────────────────────────────────────
history.replaceState({page:'inicio'}, '');

document.querySelectorAll('.ni[data-p]').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.ni').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    const page = b.dataset.p;
    history.pushState({page}, '');
    showPage(page);
    if (page === 'capitulos') renderCapitulos(1);
    if (page === 'rankings') {
      const rMobile = document.getElementById('rlist');
      const rPC     = document.getElementById('rlist-pc');
      if (rMobile) rMobile.style.display = isPC() ? 'none' : '';
      if (rPC)     rPC.style.display     = isPC() ? 'grid' : 'none';
    }
  });
});

window.addEventListener('popstate', function(e) {
  if (_skipPop) { _skipPop=false; return; }
  const state = e.state || {};
  if (state.panel==='filter') {
    document.getElementById('filter-overlay').style.display='none';
    document.getElementById('filter-panel').style.display='none';
    _filterPushed=false; return;
  }
  if (state.panel==='user') {
    document.getElementById('upanel').classList.remove('on');
    document.getElementById('ov').classList.remove('on');
    _userPushed=false; return;
  }
  if (state.page==='detail') {
    // Viniendo del reader (goto) o navegando hacia adelante — mostrar detalle
    if (currentManga) showPage('detail');
    else showPage('inicio');
    return;
  }
  if (state.page) {
    // Viniendo de un detail via history.back() → mostrar la página anterior
    showPage(state.page);
    document.querySelectorAll('.ni[data-p]').forEach(x => x.classList.toggle('on', x.dataset.p===state.page));
  }
});

function closeUserPanel() {
  document.getElementById('upanel').classList.remove('on');
  document.getElementById('ov').classList.remove('on');
  if (_userPushed) { _userPushed=false; _skipPop=true; history.back(); }
}
document.getElementById('ubtn').addEventListener('click', () => {
  document.getElementById('upanel').classList.add('on');
  document.getElementById('ov').classList.add('on');
  _userPushed = true;
  history.pushState({panel:'user'}, '');
});
['cpanel','ov'].forEach(id => document.getElementById(id).addEventListener('click', closeUserPanel));

// ── TEMAS ─────────────────────────────────────────────────────────────────────
function setTheme(theme) {
  ['theme-dark','theme-lunar-tide','theme-white'].forEach(t => document.body.classList.remove(t));
  if (theme) document.body.classList.add(theme);
  localStorage.setItem('manga_theme', theme);
  document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.theme===theme));
}
function loadTheme() { setTheme(localStorage.getItem('manga_theme') || ''); }
loadTheme();

// ── BRAVE DETECTION ───────────────────────────────────────────────────────────
(async () => { const isBrave = navigator.brave && await navigator.brave.isBrave().catch(()=>false); if(isBrave) document.body.classList.add('is-brave'); })();

// ── AVATAR ────────────────────────────────────────────────────────────────────
function setAvatarUI(avatarUrl) {
  const initials = (API.getUsername()||'M').slice(0,2).toUpperCase();
  const navAv = document.getElementById('nav-avatar');
  if (navAv) navAv.innerHTML = avatarUrl ? `<img src="${avatarUrl}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">` : initials;
  const panelAv = document.getElementById('panel-avatar'), initialsEl = document.getElementById('panel-avatar-initials'), overlay = document.getElementById('avatar-overlay');
  if (panelAv) {
    if (avatarUrl) {
      if (initialsEl) initialsEl.style.display='none';
      let img = panelAv.querySelector('img');
      if (!img) { img=document.createElement('img'); img.style.cssText='position:absolute;inset:0;width:100%;height:100%;object-fit:cover;border-radius:50%;'; panelAv.insertBefore(img, overlay||null); }
      img.src = avatarUrl;
    } else { if(initialsEl){initialsEl.style.display='';initialsEl.textContent=initials;} const img=panelAv.querySelector('img'); if(img) img.remove(); }
  }
}
function triggerAvatarInput() {
  const inp=document.createElement('input'); inp.type='file'; inp.accept='image/jpeg,image/png,image/webp'; inp.style.display='none';
  document.body.appendChild(inp);
  inp.addEventListener('change', function() { uploadAvatar(this); document.body.removeChild(inp); });
  inp.click();
}
async function uploadAvatar(input) {
  const file=input.files[0]; if(!file) return;
  const formData=new FormData(); formData.append('avatar',file);
  try {
    const r=await fetch('/api/avatar',{method:'POST',headers:{'Authorization':'Bearer '+API.getToken()},body:formData});
    const data=await r.json();
    if(data.ok){localStorage.setItem('manga_avatar',data.avatar);setAvatarUI(data.avatar+'?t='+Date.now());}
    else alert(data.error||'Error al subir la imagen.');
  } catch(e){alert('Error de conexión al subir la imagen.');}
  input.value='';
}
function initUserUI() {
  const username=API.getUsername(), role=API.getRole();
  document.querySelectorAll('.user-name-display').forEach(el=>el.textContent=username);
  document.querySelectorAll('.user-role-display').forEach(el=>el.textContent=role==='admin'?'Administrador':'Lector');
  setAvatarUI(localStorage.getItem('manga_avatar')||null);
  document.querySelectorAll('.admin-only').forEach(el=>el.style.display=API.isAdmin()?'':'none');
}
if (isAdultEnabled()) document.getElementById('toggle-adult')?.classList.add('on');

// ── PC LAYOUT ─────────────────────────────────────────────────────────────────
function applyPCLayout() {
  const pc=window.innerWidth>=768;
  const spacer=document.getElementById('nav-spacer');
  if(spacer) spacer.style.display=pc?'block':'none';
  document.body.style.overflow=pc?'auto':'hidden';
  document.body.style.height=pc?'auto':'100%';
  const app=document.getElementById('app');
  if(app){app.style.height=pc?'auto':'100vh';app.style.overflow=pc?'visible':'hidden';}
  const rMobile=document.getElementById('rlist'), rPC=document.getElementById('rlist-pc');
  if(rMobile) rMobile.style.display=pc?'none':'';
  if(rPC)     rPC.style.display=pc?'grid':'none';
}
applyPCLayout();
window.addEventListener('resize', applyPCLayout);

// ── EXPORT / IMPORT PROGRESO ─────────────────────────────────────────────────
async function exportProgress() {
  try {
    const r    = await fetch('/api/mangas/progress/export', { headers: { Authorization: 'Bearer ' + API.getToken() } });
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `progreso-manga-${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToastApp('✅ Progreso exportado');
  } catch(e) { showToastApp('❌ Error al exportar', 'err'); }
}

async function importProgressFromPanel(input) {
  const file = input.files[0]; if (!file) return;
  try {
    const text = await file.text();
    const json = JSON.parse(text);
    const progress = json.progress || json;
    const r = await fetch('/api/mangas/progress/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + API.getToken() },
      body: JSON.stringify({ progress, merge: false })
    });
    const data = await r.json();
    if (data.ok) {
      showToastApp(`✅ Importados ${data.imported} mangas`);
      API.invalidateAll();
      setTimeout(init, 600);
    } else { showToastApp('❌ ' + (data.error||'Error'), 'err'); }
  } catch(e) { showToastApp('❌ Archivo inválido', 'err'); }
  input.value = '';
}

function showToastApp(msg, type='ok') {
  let t = document.getElementById('app-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'app-toast';
    t.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:var(--card);border:1px solid var(--brd);border-radius:12px;padding:11px 20px;font-size:13px;font-weight:600;z-index:9999;display:none;box-shadow:0 8px 32px rgba(0,0,0,.4);white-space:nowrap;';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.color = type === 'err' ? '#e74c3c' : '#3fb950';
  t.style.borderColor = type === 'err' ? '#e74c3c' : '#3fb950';
  t.style.display = 'block';
  clearTimeout(t._to);
  t._to = setTimeout(() => { t.style.display = 'none'; }, 3000);
}

// ── INIT ──────────────────────────────────────────────────────────────────────
async function init() {
  try {
    initUserUI();
    const storedCache = localStorage.getItem('manga_list_cache');
    if (storedCache) {
      try {
        allMangas = JSON.parse(storedCache);
        if (allMangas?.length > 0) { renderHome(); renderSeries(); renderRankings(); }
      } catch {}
    }
    const [, freshMangas] = await Promise.all([
      fetch('/api/verify', { headers:{ Authorization:'Bearer '+API.getToken() } })
        .then(r=>r.ok?r.json():null)
        .then(vd=>{ if(!vd) return; if(vd.avatar){localStorage.setItem('manga_avatar',vd.avatar);setAvatarUI(vd.avatar+'?t='+Date.now());} })
        .catch(()=>{}),
      API.getMangas()
    ]);
    if (freshMangas?.length > 0) { allMangas=freshMangas; renderHome(); renderSeries(); renderRankings(); }
    else if (!storedCache) {
      ['sgrid','rlist','rlist-pc','cont-reading','recent-grid'].forEach(id=>{
        const el=document.getElementById(id);
        if(el) el.innerHTML='<p class="empty">No se encontraron mangas. Configura MANGA_PATH en el .env</p>';
      });
    }
    renderCapitulos(1);
    const gotoManga=new URLSearchParams(window.location.search).get('goto');
    if(gotoManga){window.history.replaceState({},'','/');await openDetail(encodeURIComponent(gotoManga));}
  } catch(err) {
    console.error('Error iniciando app:', err);
    ['sgrid','rlist','rlist-pc','cont-reading','recent-grid'].forEach(id=>{
      const el=document.getElementById(id);
      if(el&&!el.innerHTML.trim()) el.innerHTML=`<div style="grid-column:1/-1;text-align:center;padding:24px 16px;">
        <div style="font-size:13px;color:var(--mut);margin-bottom:12px;">Error al cargar. ¿Está el servidor encendido?</div>
        <button onclick="init()" style="padding:9px 20px;border-radius:10px;border:none;background:var(--acc);color:var(--acc-text);font-size:13px;font-weight:700;cursor:pointer;">Reintentar</button>
      </div>`;
    });
  }
}
window.addEventListener('load', () => setTimeout(init, 50));
