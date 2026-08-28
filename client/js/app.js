// ── app.js — estado global, renders principales, nav, temas, init ────────────
// Módulos: api.js → ui.js → detail.js → app.js
// norm() (normalización de acentos/mayúsculas) vive en api.js, no acá — ver
// el comentario ahí para el porqué.

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
const _scrollSave   = {};   // guarda scrollTop de cada página antes de entrar al detalle

const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

// ── HELPERS ───────────────────────────────────────────────────────────────────
function imgSrc(src)      { return API.imgSrc(src); }
function thumbSrc(src)    { return API.thumbSrc(src); }
function isAdultEnabled()     { return localStorage.getItem('adult_content')      === 'true'; }
function isOnlyAdultEnabled() { return localStorage.getItem('only_adult_content') === 'true'; }
// "Mostrar mangas/manhwas/manhuas": tres botones independientes, todos
// activos por defecto. Si el usuario apaga uno, ese tipo de contenido
// desaparece de todos lados (Inicio, Series, Rankings, Capítulos, búsqueda),
// igual de completo que como ya funciona +18.
function isTypeEnabled(type) {
  const key = 'show_' + String(type || 'Manga').toLowerCase();
  return localStorage.getItem(key) !== 'false'; // default: activo
}
function isPC()           { return window.innerWidth >= 768; }

const ADULT_MARKER_GENRES = ['hentai','ecchi','adultos','+18','adult','18+'];
function isAdultManga(m) {
  if (m.metadata?.adult || m.adult) return true;
  const genres = (m.metadata?.genres || []).map(g => g.toLowerCase());
  return genres.some(g => ADULT_MARKER_GENRES.includes(g));
}
// "Contenido +18" (isAdultEnabled) muestra u oculta lo +18. "Solo +18"
// (isOnlyAdultEnabled) es el espejo: cuando está activo, deja ÚNICAMENTE lo
// +18 y oculta todo lo demás — en Inicio, Series, Rankings, Capítulos,
// búsqueda y la lista de géneros, igual que +18 afecta a todo eso hoy.
// También aplica acá el filtro de tipo (Manga/Manhwa/Manhua).
function filterAdult(list) {
  const onlyAdult = isOnlyAdultEnabled();
  const showAdult = isAdultEnabled();
  return list.filter(m => {
    const adult = isAdultManga(m);
    if (onlyAdult) { if (!adult) return false; }
    else if (!showAdult && adult) return false;
    if (!isTypeEnabled(m.metadata?.type || m.type)) return false;
    return true;
  });
}
function getVisibleGenres() {
  // Agrupa por versión sin acentos (norm) para que "Retorno" y "Retórno" cuenten
  // como el mismo género y no aparezcan como dos chips separados en el filtro.
  const canon = new Map(); // norm(g) -> etiqueta a mostrar
  filterAdult(allMangas).forEach(m => (m.metadata?.genres || []).forEach(g => {
    const key = norm(g);
    const current = canon.get(key);
    if (!current || g < current) canon.set(key, g);
  }));
  return new Set(canon.values());
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
function refreshAdultFilteredViews() {
  _featSlides = []; // Forzar rebuild del carrusel al cambiar el filtro +18
  renderHome(); renderSeries(); renderRankings(); renderCapitulos(1);
  renderSearch(document.getElementById('sinput').value);
}
function toggleAdult(el) {
  el.classList.toggle('on');
  const on = el.classList.contains('on');
  localStorage.setItem('adult_content', on ? 'true' : 'false');
  // Si se apaga "Contenido +18", "Solo +18" tampoco tiene sentido — se apaga junto.
  if (!on && isOnlyAdultEnabled()) {
    localStorage.setItem('only_adult_content', 'false');
    document.getElementById('toggle-only-adult')?.classList.remove('on');
  }
  refreshAdultFilteredViews();
}
function toggleOnlyAdult(el) {
  el.classList.toggle('on');
  const on = el.classList.contains('on');
  localStorage.setItem('only_adult_content', on ? 'true' : 'false');
  // Para ver "Solo +18" hace falta tener "Contenido +18" habilitado también.
  if (on && !isAdultEnabled()) {
    localStorage.setItem('adult_content', 'true');
    document.getElementById('toggle-adult')?.classList.add('on');
  }
  refreshAdultFilteredViews();
}
function toggleShowType(type, el) {
  el.classList.toggle('on');
  localStorage.setItem('show_' + type, el.classList.contains('on') ? 'true' : 'false');
  refreshAdultFilteredViews();
}

// ── SINCRONIZAR PROGRESO EN MEMORIA (sin recargar la página) ─────────────────
// Actualiza allMangas/currentManga in-place y vuelve a pintar Inicio/Series/
// Rankings. Así, marcar un capítulo como leído (desde el lector o desde el
// detalle) se refleja ahí mismo, sin que el usuario tenga que refrescar.
function applyMangaProgressLocal(mangaName, readChapter) {
  const m = allMangas.find(x => x.name === mangaName);
  if (m) {
    if (!m.progress) m.progress = {};
    const rc = new Set(m.progress.readChapters || []);
    rc.add(readChapter);
    m.progress.readChapters = [...rc];
    m.progress.lastChapter  = readChapter;
  }
  if (currentManga && currentManga.name === mangaName) {
    currentManga.chapters = currentManga.chapters.map(ch =>
      ch.number === readChapter ? { ...ch, read: true } : ch);
    if (!currentManga.progress) currentManga.progress = {};
    const rc2 = new Set(currentManga.progress.readChapters || []);
    rc2.add(readChapter);
    currentManga.progress.readChapters = [...rc2];
    currentManga.progress.lastChapter  = readChapter;
  }
  renderHome(); renderSeries(); renderRankings();
}

// Igual, pero reemplazando toda la lista de leídos de una vez (marcar/
// desmarcar todos los capítulos de un manga).
function replaceMangaProgressLocal(mangaName, readChapters, lastChapter) {
  const m = allMangas.find(x => x.name === mangaName);
  if (m) {
    if (!m.progress) m.progress = {};
    m.progress.readChapters = [...readChapters];
    m.progress.lastChapter  = lastChapter ?? m.progress.lastChapter;
  }
  renderHome(); renderSeries(); renderRankings();
}

// ── REFRESCO SILENCIOSO AL VISITAR INICIO/SERIES/RANKINGS ────────────────────
// "Capítulos" siempre pidió datos frescos al servidor; ahora Inicio/Series/
// Rankings hacen lo mismo. Gracias al caché+ETag de api.js esto es casi
// gratis cuando no cambió nada (304), y trae datos nuevos al instante cuando
// sí cambió (mangas agregados por el scraper, progreso actualizado, etc.) —
// sin necesitar un refresco manual de la página.
async function refreshMangasIfStale() {
  try {
    API.invalidate('mangas'); // fuerza a re-consultar al servidor (barato: usa ETag)
    const fresh = await API.getMangas();
    if (fresh && fresh.length > 0) {
      allMangas = fresh;
      renderHome(); renderSeries(); renderRankings();
    }
  } catch(e) { console.error('refreshMangasIfStale error:', e); }
}

// ── INICIO ────────────────────────────────────────────────────────────────────
let contReadingExpanded = false; // "Seguir Leyendo": false = muestra 8, true = muestra todos

function renderHome() {
  renderCarousel();
  const visible       = filterAdult(allMangas);
  const inProgressAll = visible.filter(m => {
    const rc = m.progress?.readChapters?.length || 0;
    return rc > 0 && rc < m.chapterCount;
  });
  const inProgress = contReadingExpanded ? inProgressAll : inProgressAll.slice(0, 8);

  document.getElementById('cont-reading').innerHTML = inProgress.length === 0
    ? '<p class="loading" style="grid-column:1/-1;">Aún no has leído ningún manga.</p>'
    : inProgress.map(m => {
        const pct = Math.round((m.progress.readChapters.length / m.chapterCount) * 100);
        const src = m.cover ? thumbSrc(m.cover) : '';
        return`<div class="ri ri-with-bg" data-open-manga="${esc(m.name)}">
          ${src ? `<img class="ri-bg" src="${src}" alt="" aria-hidden="true">` : ''}
          <div class="rcov" style="position:relative;z-index:1;">${m.cover ? coverImg(m.cover, m.name) : ''}</div>
          <div style="flex:1;min-width:0;overflow:hidden;position:relative;z-index:1;">
            <div class="ri-title">${esc(m.name)}</div>
            <div class="ri-sub" style="font-size:12px;color:var(--mut);">Cap. ${chLabel(m.progress.lastChapter)} · ${pct}%</div>
            <div class="prbar"><div class="prfill" style="width:${pct}%;"></div></div>
          </div>
        </div>`;
      }).join('');

  // Flecha de expandir/contraer — solo se muestra si hay más de 8 en progreso.
  const btnToggle = document.getElementById('btn-toggle-reading');
  if (btnToggle) {
    btnToggle.style.display = inProgressAll.length > 8 ? 'inline-flex' : 'none';
    btnToggle.classList.toggle('open', contReadingExpanded);
    btnToggle.title = contReadingExpanded ? 'Ver menos' : 'Ver todos';
  }

  const recent = [...visible].filter(m => m.addedDate).sort((a,b) => new Date(b.addedDate)-new Date(a.addedDate)).slice(0,10);
  document.getElementById('recent-grid').innerHTML = recent.length
    ? recent.map(m => rankCard(m, 0, false)).join('')
    : '<p class="empty">No hay mangas recientes.</p>';
}

function toggleContReading() {
  contReadingExpanded = !contReadingExpanded;
  renderHome();
}

// ── SERIES ────────────────────────────────────────────────────────────────────
function renderSeries() { applySeriesFilter(); }
function applySeriesFilter() {
  let list = filterAdult(allMangas);
  const f  = activeFilters;
  if (f.types.length)  list = list.filter(m => f.types.includes(m.metadata?.type));
  if (f.search) { const q = norm(f.search); list = list.filter(m => norm(m.name).includes(q)||(m.metadata?.genres||[]).some(g => norm(g).includes(q))); }
  if (f.genres.length) { const fg = f.genres.map(norm); list = list.filter(m => (m.metadata?.genres||[]).some(g => fg.includes(norm(g)))); }
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
    const adult     = isAdultEnabled();
    const onlyAdult = isOnlyAdultEnabled();
    const types     = ['manga','manhwa','manhua'].filter(isTypeEnabled).join(',');
    const res   = await API.fetchRaw(`/api/mangas/latest-paged?page=${page}&limit=20&adult=${adult}&onlyAdult=${onlyAdult}&types=${encodeURIComponent(types)}`);
    if (!res) { clist.innerHTML = '<p class="empty" style="grid-column:1/-1;">No se pudo conectar al servidor.</p>'; return; }
    const data = await res.json();
    const {items, total, totalPages} = data;
    if (!items || items.length === 0) { clist.innerHTML = '<p class="empty">No hay capítulos recientes.</p>'; renderCapPagination(0,0,0); return; }
    const itemsHTML = items.map(g => `
      <div class="lci">
        ${g.cover?`<img class="lci-bg" src="${thumbSrc(g.cover)}" alt="">` : ''}
        <div class="lci-head" data-open-manga="${esc(g.manga)}">
          <div class="lci-cov">${g.cover ? coverImg(g.cover, g.manga) : ''}</div>
          <div class="lci-title">${esc(g.manga)}</div>
          ${statusBadge(g.status)}
        </div>
        ${g.chapters.map(ch => `
        <div class="lci-ch" data-open-chapter data-manga="${esc(g.manga)}" data-chapter="${esc(ch.chapter)}" data-slug="${esc(ch.chapterSlug||'')}">
          <div class="dot ${ch.read?'r':'u'}" style="margin-right:10px;"></div>
          <div style="flex:1;"><div style="font-size:13px;font-weight:600;color:var(--text);">${esc(chLabel(ch.chapter))}</div></div>
          <div style="font-size:12px;color:var(--mut);display:flex;align-items:center;gap:3px;"><i class="ti ti-calendar" style="font-size:12px;"></i>${esc(ch.dateLabel)}</div>
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
    : `<button class="cap-pg-btn${p===current?' on':''}" onclick="goToCapPage(${p})">${p}</button>`).join('');
  container.innerHTML = `
    <div class="cap-pg-info">Mostrando <b>${from}</b> a <b>${to}</b> de <b>${totalItems}</b> Series</div>
    <div class="cap-pg-row">
      <button class="cap-pg-arrow" onclick="goToCapPage(${current-1})" ${current===1?'disabled':''}><i class="ti ti-chevron-left"></i></button>
      ${btns}
      <button class="cap-pg-arrow" onclick="goToCapPage(${current+1})" ${current===total?'disabled':''}><i class="ti ti-chevron-right"></i></button>
    </div>`;
}

// Wrapper para clicks reales de paginación: sincroniza la URL (?page=N) y
// después llama a renderCapitulos. Aparte de renderCapitulos a propósito —
// renderCapitulos también se llama internamente (init(), popstate) para
// pre-cargar datos sin que el usuario esté necesariamente en esa pestaña, y
// esos casos NO deben pisar la URL actual.
function goToCapPage(page) {
  history.replaceState({page:'capitulos', capPage:page}, '', Router.buildPath('capitulos', {page}));
  renderCapitulos(page);
}

// ── BÚSQUEDA (listener) ───────────────────────────────────────────────────────
document.getElementById('sinput').addEventListener('input', e => renderSearch(e.target.value));

// ── NAVEGACIÓN ────────────────────────────────────────────────────────────────
// Interpretar la URL con la que se cargó la página (F5, link directo, o "/"
// normal) — antes esto siempre arrancaba en 'inicio' sin mirar la URL. El
// resultado se aplica en init() (más abajo), una vez que allMangas ya está
// cargado — hace falta para poder resolver slug -> manga/capítulo real.
const _initialRoute = Router.parseRoute(location.pathname, location.search);
history.replaceState({ page: _initialRoute.page }, '', location.pathname + location.search);

// Mostrar la página correcta YA, de forma sincrónica — sin esto, se ve
// "inicio" (el estado por defecto del HTML) durante todo lo que tarda
// init() en arrancar (espera el evento 'load' + 50ms + fetches de red)
// y recién ahí cambia a la página real. Achica esa ventana a prácticamente
// cero: esto corre apenas se parsea el script, antes de que haya siquiera
// arrancado un fetch. Para detalle/lector no hay datos todavía (eso lo
// resuelve el listener de DOMContentLoaded más abajo), pero al menos se ve
// el esqueleto de "Cargando..." de esa página en vez de "inicio".
(function showInitialPageSync() {
  const r = _initialRoute;
  const navPage = ['inicio','series','rankings','capitulos','busqueda'].includes(r.page) ? r.page : null;
  if (navPage) {
    document.querySelectorAll('.ni[data-p]').forEach(x => x.classList.toggle('on', x.dataset.p === navPage));
  }
  if (r.page === 'reader') {
    // OJO: #p-reader no tiene clase ".pg" — se abre/cierra solo con
    // classList ("on"), nunca con inline style (ver showReaderPage() /
    // closeReaderPage() en reader.js). showPage() genérico le pondría un
    // style.display="block" inline, que le gana en especificidad a la
    // clase CSS — closeReaderPage() saca la clase después pero el inline
    // style se lo pisa y queda pegado en pantalla para siempre, sin
    // importar cuántas veces cambie la URL con "atrás". Por eso acá se
    // replica a mano SOLO lo que showReaderPage() hace para mostrarlo.
    document.querySelectorAll('.pg').forEach(p => { p.style.display = 'none'; p.classList.remove('on'); });
    document.getElementById('p-reader')?.classList.add('on');
  } else {
    showPage(r.page === 'detail' ? 'detail' : r.page);
  }
  // El <head> arranca con el body oculto (visibility:hidden) para que nunca
  // se llegue a pintar "inicio" mientras cargan los 6 <script> por red —
  // recién ahora, con la página real ya decidida, lo mostramos.
  document.body.style.visibility = 'visible';
})();

// Apenas terminan de parsearse TODOS los scripts (mucho antes que 'load',
// que encima espera imágenes/CSS) — si la ruta inicial es detalle o lector,
// arrancar el fetch de sus datos ya mismo, en paralelo a lo que haga init().
// openDetail/openReader ya toleran que allMangas todavía esté vacío (caen
// al slug/nombre tal cual y se autocorrigen con replaceState apenas responde
// el fetch), así que no hace falta esperar a que init() cargue el catálogo.
document.addEventListener('DOMContentLoaded', () => {
  const r = _initialRoute;
  if (r.page === 'detail' && r.mangaSlug) {
    openDetail(encodeURIComponent(r.mangaSlug), true);
  } else if (r.page === 'reader' && r.mangaSlug && r.chapterSlug) {
    openReader(encodeURIComponent(r.mangaSlug), r.chapterSlug, r.chapterSlug, true);
  }
});

document.querySelectorAll('.ni[data-p]').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.ni').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    const page = b.dataset.p;
    history.pushState({page}, '', Router.buildPath(page));
    showPage(page);
    if (page === 'capitulos') renderCapitulos(1);
    if (page === 'rankings') {
      const rMobile = document.getElementById('rlist');
      const rPC     = document.getElementById('rlist-pc');
      if (rMobile) rMobile.style.display = isPC() ? 'none' : '';
      if (rPC)     rPC.style.display     = isPC() ? 'grid' : 'none';
    }
    if (page === 'inicio' || page === 'series' || page === 'rankings') refreshMangasIfStale();
  });
});

window.addEventListener('popstate', function(e) {
  if (_skipPop) { _skipPop=false; return; }
  const state = e.state || {};

  // Si el lector estaba abierto y el nuevo estado no es 'reader', cerrarlo
  // primero — cubre tanto "atrás" como "adelante" saliendo del lector.
  const readerPg = document.getElementById('p-reader');
  if (readerPg && readerPg.classList.contains('on') && state.page !== 'reader') {
    closeReaderPage();
  }

  if (state.page==='reader') {
    showReaderPage(state.manga, state.chapter);
    return;
  }
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
    // Viniendo del lector o navegando hacia adelante — mostrar detalle,
    // y refrescar la lista de capítulos por si se marcó alguno como leído
    // mientras se estaba en el lector.
    if (currentManga && currentManga.name === state.manga) {
      showPage('detail'); renderDetChapList(currentManga);
    } else if (state.manga) {
      // No tenemos los datos en memoria — típico de F5 estando en el
      // lector: se entra directo al capítulo, nunca se pasa por el
      // detalle, así que currentManga queda null. skipHistory=true porque
      // la URL ya es la correcta (la puso el navegador solo, al volver).
      openDetail(encodeURIComponent(state.manga), false, true);
    } else {
      showPage('inicio');
    }
    return;
  }
  if (state.page) {
    // Viniendo de un detail/reader via history.back() → mostrar la página anterior
    showPage(state.page);
    document.querySelectorAll('.ni[data-p]').forEach(x => x.classList.toggle('on', x.dataset.p===state.page));
    // Restaurar la posición de scroll donde estaba el usuario antes de entrar al detalle
    if (_scrollSave[state.page] != null) {
      document.getElementById('cnt').scrollTop = _scrollSave[state.page];
      delete _scrollSave[state.page];
    }
    if (state.page==='inicio' || state.page==='series' || state.page==='rankings') refreshMangasIfStale();
    if (state.page==='capitulos') renderCapitulos(capPage);
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
  if (navAv) navAv.innerHTML = avatarUrl ? `<img src="${avatarUrl}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">` : esc(initials);
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
if (isAdultEnabled())     document.getElementById('toggle-adult')?.classList.add('on');
if (isOnlyAdultEnabled()) document.getElementById('toggle-only-adult')?.classList.add('on');
['manga','manhwa','manhua'].forEach(t => {
  document.getElementById('toggle-show-' + t)?.classList.toggle('on', isTypeEnabled(t));
});

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
// El fetch/blob/parseo vive en API.exportProgress()/API.importProgress()
// (client/js/api.js), compartido con stats.html — acá solo queda el toast y
// qué refrescar después, que sí es propio de esta página.
async function exportProgress() {
  try {
    await API.exportProgress();
    showToastApp('✅ Progreso exportado');
  } catch(e) { showToastApp('❌ Error al exportar', 'err'); }
}

async function importProgressFromPanel(input) {
  const file = input.files[0]; if (!file) return;
  try {
    const data = await API.importProgress(file, false);
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
    const storedCache = localStorage.getItem(API._listCacheKey());
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
    renderCapitulos(_initialRoute.page === 'capitulos' ? (_initialRoute.capPage || 1) : 1);
    // Deep-link desde páginas externas al SPA (ej. stats.html → "ver detalles"
    // de un manga). Solo abre el detalle, nunca el lector — el lector nunca
    // navega afuera del SPA, así que no necesita esto.
    const gotoManga = new URLSearchParams(window.location.search).get('goto');
    if (gotoManga) {
      window.history.replaceState({}, '', '/');
      await openDetail(encodeURIComponent(gotoManga));
    } else {
      // Aplicar lo que falta de la URL con la que se cargó la página (F5,
      // link directo, o una de las páginas del nav) — la página y el fetch
      // de detalle/lector ya se resolvieron antes.
      applyInitialRoute();
    }
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

// Termina de aplicar la ruta inicial una vez que init() ya cargó allMangas.
// El "mostrar la página correcta" y el fetch de detalle/lector ya se
// hicieron antes (ver showInitialPageSync y el listener de DOMContentLoaded,
// arriba) — acá solo queda lo que sí depende de allMangas: restaurar el
// texto de búsqueda si se entró por /buscar?q=...
function applyInitialRoute() {
  const r = _initialRoute;
  if (r.page === 'busqueda' && r.q) {
    const sinput = document.getElementById('sinput');
    if (sinput) { sinput.value = r.q; renderSearch(r.q); }
  }
}

// ── FEATURED CAROUSEL ─────────────────────────────────────────────────────────
let _featSlides = [];   // mangas seleccionados
let _featIdx    = 0;    // slide activo
let _featTimer  = null; // setTimeout de auto-avance
const FEAT_COUNT = 8;

// % de rating consistente (72-98) derivado del nombre — solo visual
function _featRating(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return 72 + (h % 27);
}
// Duración por slide: 5 / 6 / 7 / 8 s según nombre
function _featDur(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 17 + name.charCodeAt(i)) >>> 0;
  return 5000 + (h % 4) * 1000;
}

function renderCarousel() {
  const wrap = document.getElementById('feat-wrap');
  if (!wrap) return;

  // Solo mangas visibles para este usuario y que tengan portada
  const eligible = filterAdult(allMangas).filter(m => m.cover);
  if (eligible.length === 0) { wrap.style.display = 'none'; return; }

  // Mantener la selección actual si todos los slides siguen siendo válidos
  // (evita re-randomizar en el segundo render de init cuando llegan datos frescos)
  const eligibleSet = new Set(eligible.map(m => m.name));
  const allValid    = _featSlides.length > 0 && _featSlides.every(m => eligibleSet.has(m.name));
  if (allValid) { wrap.style.display = ''; return; }

  // Selección nueva: mezclar y tomar hasta FEAT_COUNT
  _featSlides = [...eligible].sort(() => Math.random() - .5).slice(0, FEAT_COUNT);
  _featIdx    = 0;

  _stopCarousel();
  _buildCarouselDOM();
  _showFeatSlide(0);
  _startCarousel();
  wrap.style.display = '';
}

function _buildCarouselDOM() {
  const slidesEl = document.getElementById('feat-slides');
  const indsEl   = document.getElementById('feat-indicators');
  if (!slidesEl || !indsEl) return;

  slidesEl.innerHTML = _featSlides.map((m, i) => {
    const genres = (m.metadata?.genres || []).slice(0, 5);
    const syn    = (m.metadata?.synopsis || '').trim();
    const cover  = imgSrc(m.cover);
    const coverThumb = thumbSrc(m.cover);

    return `<div class="feat-slide${i === 0 ? ' feat-active' : ''}" data-idx="${i}">
      <div class="feat-hero">
        ${cover ? `<img class="feat-bg-img" src="${cover}" alt="" draggable="false">` : ''}
        <div class="feat-overlay"></div>
        ${cover ? `<img class="feat-cover-hero" src="${cover}" alt="" draggable="false">` : ''}
        <div class="feat-hero-text">
          <div class="feat-title">${esc(m.name)}</div>
          <button class="feat-btn" data-open-manga="${esc(m.name)}">Ver detalles \u2192</button>
        </div>
      </div>
      <div class="feat-info-bar">
        ${coverThumb ? `<img class="feat-thumb-small" src="${coverThumb}" alt="" loading="lazy" draggable="false">` : ''}
        <div class="feat-text-block">
          ${syn ? `<div class="feat-syn">${esc(syn)}</div>` : ''}
          ${genres.length ? `<div class="feat-genres">${genres.map(g => `<span class="feat-chip">${esc(g)}</span>`).join('')}</div>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');

  indsEl.innerHTML = _featSlides.map((_, i) =>
    `<div class="feat-ind${i === 0 ? ' feat-active' : ''}" onclick="carouselGoTo(${i})">
      <div class="feat-ind-fill"></div>
    </div>`
  ).join('');
}

// Anima el fill del indicador activo con JS transition (más fiable que @keyframes + clase)
function _animateIndicators(idx) {
  const fills = document.querySelectorAll('.feat-ind-fill');
  // Reset de todos sin transición
  fills.forEach(f => { f.style.transition = 'none'; f.style.width = '0%'; });
  // Forzar reflow para que el reset sea instantáneo antes de arrancar la animación
  void document.getElementById('feat-indicators')?.offsetWidth;
  // Arrancar el fill del activo
  if (fills[idx]) {
    const dur = _featDur(_featSlides[idx]?.name || '');
    fills[idx].style.transition = `width ${dur}ms linear`;
    fills[idx].style.width = '100%';
  }
  // Actualizar clase activa en los indicadores
  document.querySelectorAll('.feat-ind').forEach((d, i) =>
    d.classList.toggle('feat-active', i === idx));
}

function _showFeatSlide(idx) {
  document.querySelectorAll('.feat-slide').forEach((s, i) =>
    s.classList.toggle('feat-active', i === idx));
  _featIdx = idx;
  _animateIndicators(idx);
}

function _startCarousel() {
  _stopCarousel();
  const dur = _featDur(_featSlides[_featIdx]?.name || '');
  _featTimer = setTimeout(carouselNext, dur);
}
function _stopCarousel() {
  if (_featTimer) { clearTimeout(_featTimer); _featTimer = null; }
}

function carouselNext() {
  if (!_featSlides.length) return;
  _showFeatSlide((_featIdx + 1) % _featSlides.length);
  _startCarousel();
}
function carouselPrev() {
  if (!_featSlides.length) return;
  _showFeatSlide((_featIdx - 1 + _featSlides.length) % _featSlides.length);
  _startCarousel();
}
function carouselGoTo(idx) {
  if (idx === _featIdx || !_featSlides.length) return;
  _showFeatSlide(idx);
  _startCarousel();
}

// Pausar cuando el tab pierde el foco para no avanzar slides en segundo plano
document.addEventListener('visibilitychange', () => {
  if (document.hidden) _stopCarousel();
  else if (_featSlides.length && _featTimer === null) _startCarousel();
});
