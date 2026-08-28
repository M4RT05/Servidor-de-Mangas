// ── reader.js — lector de capítulos, integrado a la SPA ──────────────────────
// Antes era reader.html: un documento HTML aparte al que se navegaba con
// location.href, con recarga completa de página en cada capítulo y un
// "back-button trap" para interceptar el botón atrás del navegador. Eso
// causaba: flash de la pantalla de Inicio al volver, historial del navegador
// contaminado con entradas de páginas que ya no existían, y que Inicio/
// Series/Rankings quedaran desactualizados hasta refrescar a mano.
//
// Ahora el lector es una página más del SPA (#p-reader, oculta por defecto),
// sin recargas de documento. Cambiar de capítulo actualiza el mismo DOM;
// salir del lector es un history.back() normal que el popstate de app.js
// resuelve como cualquier otra transición.

// ── ESTADO ────────────────────────────────────────────────────────────────────
let READER_MANGA   = '';
let READER_CHAPTER = '';
let chapData    = null;
let curPage     = 0;
let isLast      = false;
let rMode       = localStorage.getItem('rm_mode')   || 'scroll';
let nightOn     = localStorage.getItem('rm_night')  === 'true';
let screenOn    = localStorage.getItem('rm_screen') === 'true';
let asOn        = localStorage.getItem('rm_as')     === 'true';
let asSpeed     = parseInt(localStorage.getItem('rm_speed') || '130');
let asActive    = false;
let asRAF       = null;
let asLastTime  = null;
let lastScrollY = 0;

// ── REFS (se resuelven en cada uso: el DOM del lector vive en index.html
// desde el arranque, así que esto siempre encuentra los elementos) ───────────
function readerEl()       { return document.getElementById('p-reader'); }
function scrollReaderEl() { return document.getElementById('scroll-reader'); }
function pageReaderEl()   { return document.getElementById('page-reader'); }
function pageImgEl()      { return document.getElementById('page-img'); }
function botBarEl()       { return document.getElementById('bot-bar'); }
function floatPanelEl()   { return document.getElementById('float-panel'); }
function loaderEl()       { return document.getElementById('loader'); }

// ── ABRIR / CERRAR LA PÁGINA DEL LECTOR ───────────────────────────────────────
function openReader(encodedManga, chapter, chapterSlug, isColdStart = false) {
  const manga = decodeURIComponent(encodedManga);
  // Resolver el slug de manga desde lo que ya tenemos en memoria (allMangas
  // es el catálogo completo, currentManga el que se está viendo) — si
  // ninguno lo tiene todavía (cold load raro), se usa el propio "manga" tal
  // cual y se corrige con replaceState apenas responda loadChapter().
  const mangaSlug = allMangas.find(m => m.name === manga)?.slug
                 || (currentManga?.name === manga ? currentManga.slug : null)
                 || manga;
  // Mismo criterio para el capítulo: el slug pasado explícitamente (todos
  // los call sites de siempre ya lo mandan), o buscarlo en currentManga si
  // coincide con el manga que se está abriendo, o el número tal cual.
  const chSlug = chapterSlug
              || (currentManga?.name === manga ? currentManga.chapters?.find(c => c.number === chapter)?.slug : null)
              || chapter;
  const url = Router.buildPath('reader', { mangaSlug, chapterSlug: chSlug });
  if (isColdStart) history.replaceState({ page: 'reader', manga, chapter }, '', url);
  else             history.pushState({ page: 'reader', manga, chapter }, '', url);
  showReaderPage(manga, chapter);
}

function showReaderPage(manga, chapter) {
  READER_MANGA   = manga;
  READER_CHAPTER = chapter;
  readerEl().classList.add('on');
  setReaderViewport(true);
  initReaderAvatar();
  loadChapter();
}

function closeReaderPage() {
  if (asActive) stopAS();
  readerEl().classList.remove('on');
  setReaderViewport(false);
}

// El resto de la app bloquea el zoom con los dedos (viewport maximum-scale=1),
// pero en el lector conviene permitirlo para ver el detalle del arte — como
// hacía el reader.html original. Se activa/desactiva solo mientras el lector
// está abierto, sin afectar al resto de la SPA.
function setReaderViewport(zoomEnabled) {
  const vp = document.querySelector('meta[name="viewport"]');
  if (!vp) return;
  vp.setAttribute('content', zoomEnabled
    ? 'width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover'
    : 'width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover');
}

function initReaderAvatar() {
  const av = document.getElementById('t-avatar');
  if (!av) return;
  const url = localStorage.getItem('manga_avatar');
  if (url) av.innerHTML = `<img src="${url}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">`;
  else av.textContent = (API.getUsername() || 'M').slice(0, 2).toUpperCase();
}

// ── CARGAR CAPÍTULO ────────────────────────────────────────────────────────────
let _loadingChapter = false;

async function loadChapter() {
  if (_loadingChapter) return;
  _loadingChapter = true;
  const loader = loaderEl();
  loader.classList.remove('off');
  loader.innerHTML = 'Cargando capítulo...';
  try {
    const r = await fetch(`/api/mangas/${encodeURIComponent(READER_MANGA)}/${encodeURIComponent(READER_CHAPTER)}/images`, {
      headers: { Authorization: 'Bearer ' + API.getToken() }
    });
    if (!r.ok) {
      const msg = r.status === 404
        ? 'Este capítulo no existe o no tienes acceso a él.'
        : 'Error cargando el capítulo (código ' + r.status + ').';
      loader.innerHTML = `<div style="text-align:center;padding:0 24px;">
        <div style="margin-bottom:16px;">${msg}</div>
        <button onclick="goBack()" style="padding:10px 22px;border-radius:10px;border:none;background:var(--acc);color:var(--acc-text);font-size:13px;font-weight:700;cursor:pointer;">Volver</button>
      </div>`;
      return;
    }
    chapData = await r.json();
    // El server siempre devuelve el nombre real y el número real de
    // capítulo (resolvió lo que le hayamos mandado, sea nombre/número real
    // o slug). Nos alineamos a eso ahora — así, sin importar con qué se
    // haya llamado a openReader (slug de un cold load, número real de
    // siempre, etc.), el progreso se guarda siempre con la clave correcta.
    READER_MANGA   = chapData.manga;
    READER_CHAPTER = chapData.chapter;
    renderChapter();
  } catch(e) {
    loader.innerHTML = `<div style="text-align:center;padding:0 24px;">
      <div style="margin-bottom:16px;">Error cargando el capítulo. (${e.message})</div>
      <button onclick="loadChapter()" style="padding:10px 22px;border-radius:10px;border:none;background:var(--acc);color:var(--acc-text);font-size:13px;font-weight:700;cursor:pointer;">Reintentar</button>
    </div>`;
  } finally {
    _loadingChapter = false;
  }
}

function renderChapter() {
  isLast = !chapData.nextChapter;
  _preloaded.clear();

  // Si la URL con la que se entró no tenía todavía el slug canónico (cold
  // load por slug de fallback, o cualquier discrepancia) la corregimos
  // ahora que ya sabemos los valores reales — sin apilar otra entrada de
  // historial (replaceState).
  if (chapData.slug && chapData.chapterSlug) {
    const canonicalUrl = Router.buildPath('reader', { mangaSlug: chapData.slug, chapterSlug: chapData.chapterSlug });
    if (location.pathname !== canonicalUrl) {
      history.replaceState({ page: 'reader', manga: READER_MANGA, chapter: READER_CHAPTER }, '', canonicalUrl);
    }
  }

  document.getElementById('t-title').textContent = READER_MANGA;
  document.getElementById('t-chapnum-label').textContent = chapLabel(READER_CHAPTER);

  if (chapData.allChapters && chapData.allChapters.length > 0) {
    const reversed = [...chapData.allChapters].reverse();
    document.getElementById('chap-drawer-list').innerHTML = reversed.map(ch => `
      <div class="drawer-item${ch.number===READER_CHAPTER?' current':''}" data-goto-drawer data-chapter="${esc(ch.number)}" data-slug="${esc(ch.slug||'')}">
        <span>${esc(chapLabel(ch.number))}</span>
        <i class="ti ti-check drawer-item-icon"></i>
      </div>`).join('');
  } else {
    document.getElementById('chap-drawer-list').innerHTML =
      `<div class="drawer-item current"><span>${esc(chapLabel(READER_CHAPTER))}</span><i class="ti ti-check drawer-item-icon"></i></div>`;
  }

  document.getElementById('t-prev').disabled = !chapData.prevChapter;
  const tNext = document.getElementById('t-next');
  tNext.disabled = false;
  tNext.style.color = isLast ? '#e74c3c' : '';

  document.getElementById('b-prev').disabled = !chapData.prevChapter;
  const bNext = document.getElementById('b-next');
  bNext.disabled = false;
  bNext.classList.toggle('last', isLast);
  bNext.title = isLast ? 'Volver al manga' : 'Siguiente capítulo';
  updatePageNum(1, chapData.images.length);

  loaderEl().classList.add('off');

  if (rMode === 'scroll') renderScroll();
  else renderPage();

  saveProgress();
}

function chapLabel(ch) {
  const m = String(ch).match(/(\d+(?:\.\d+)?)/);
  if (!m) return ch;
  const n = parseFloat(m[1]);
  return Number.isInteger(n) ? String(n) : String(n);
}

// ── MODO SCROLL ────────────────────────────────────────────────────────────────
function renderScroll() {
  pageReaderEl().classList.remove('on');
  pageReaderEl().style.display = 'none';

  // El JWT ya no viaja en la URL de cada página — la cookie httpOnly
  // 'img_token' (seteada al iniciar sesión, ver login.html) ya autentica
  // estos pedidos. Antes se armaba acá con API.getToken() y quedaba
  // pegado en el historial del navegador de forma redundante.
  // Las primeras páginas se cargan sin "lazy" (van a estar visibles apenas
  // se abre el capítulo, no tiene sentido esperar); el resto sigue con lazy
  // nativo, que el navegador empieza a pedir un poco antes de que entren en
  // pantalla a medida que el usuario hace scroll.
  const EAGER_COUNT = 3;
  scrollReaderEl().innerHTML = chapData.images.map((src, i) =>
    `<img src="${src}" alt="Pag ${i+1}"${i < EAGER_COUNT ? '' : ' loading="lazy"'}>`
  ).join('');

  const el = readerEl();
  el.removeEventListener('scroll', onScroll);
  el.addEventListener('scroll', onScroll, { passive: true });
  el.scrollTop = 0;
  lastScrollY = 0;
  if (asOn && asActive) startAS();
}

function onScroll() {
  const el = readerEl();
  updatePageNum(getCurPage(), chapData?.images?.length || 1);
  const y = el.scrollTop;
  const atBottom = (y + el.clientHeight) >= (el.scrollHeight - 30);
  if (atBottom) showBars();
  else if (y > lastScrollY + 8) hideBars();
  else if (y < lastScrollY - 8) showBars();
  lastScrollY = y;
}

function getCurPage() {
  const imgs = scrollReaderEl().querySelectorAll('img');
  if (!imgs.length) return 1;
  const el  = readerEl();
  const mid = el.scrollTop + el.clientHeight / 2;
  let closest = 0, minD = Infinity;
  imgs.forEach((img, i) => {
    const d = Math.abs(img.offsetTop + img.offsetHeight / 2 - mid);
    if (d < minD) { minD = d; closest = i; }
  });
  return closest + 1;
}

// ── MODO PÁGINA — imagen centrada en pantalla completa limpia ──────────────────
function renderPage() {
  readerEl().removeEventListener('scroll', onScroll);

  pageReaderEl().style.display = 'flex';
  pageReaderEl().classList.add('on');

  pageImgEl().src = '';
  curPage = 0;
  showPgImg(0);
}

function showPgImg(idx) {
  if (!chapData || idx < 0 || idx >= chapData.images.length) return;
  curPage = idx;
  const img = pageImgEl();
  img.style.opacity = '0';
  img.onload  = () => { img.style.opacity = '1'; };
  img.onerror = () => { img.style.opacity = '1'; };
  img.src = chapData.images[idx];
  updatePageNum(idx + 1, chapData.images.length);
  preloadPages(idx + 1, 2);
}

// ── PRECARGA DE PRÓXIMAS PÁGINAS (modo página) ────────────────────────────────
// Antes, cada vuelta de página pagaba el viaje completo (pedido → auth →
// disco externo → transferencia) recién cuando el usuario ya estaba mirando
// esa página. Ahora, apenas se muestra una página, se dispara en paralelo la
// descarga de las próximas para que el navegador ya las tenga en caché
// cuando el usuario avance.
const _preloaded = new Set();
function preloadPages(fromIdx, count) {
  if (!chapData) return;
  for (let i = fromIdx; i < Math.min(fromIdx + count, chapData.images.length); i++) {
    const src = chapData.images[i];
    if (_preloaded.has(src)) continue;
    _preloaded.add(src);
    const pre = new Image();
    pre.src = src;
  }
}

function prevPage() { if (curPage > 0) showPgImg(curPage - 1); else toggleBars(); }
function nextPage() {
  if (!chapData) return;
  if (curPage < chapData.images.length - 1) showPgImg(curPage + 1);
  else nextChap();
}

// ── MODO (scroll / página) ────────────────────────────────────────────────────
function setMode(m) {
  rMode = m;
  localStorage.setItem('rm_mode', m);
  setModeUI(m);
  if (!chapData) return;
  if (m === 'scroll') {
    pageReaderEl().classList.remove('on');
    pageReaderEl().style.display = 'none';
    renderScroll();
  } else {
    scrollReaderEl().innerHTML = '';
    readerEl().removeEventListener('scroll', onScroll);
    renderPage();
  }
}
function setModeUI(m) {
  document.getElementById('btn-scroll').classList.toggle('on', m === 'scroll');
  document.getElementById('btn-page').classList.toggle('on',   m === 'page');
}

// ── NAVEGACIÓN ENTRE CAPÍTULOS (sin recargar la página) ───────────────────────
function prevChap() { if (!_loadingChapter && chapData?.prevChapter) goToChap(chapData.prevChapter, chapData.prevChapterSlug); }
function nextChap() {
  if (_loadingChapter) return;
  if (isLast) { goBack(); return; }
  if (chapData?.nextChapter) goToChap(chapData.nextChapter, chapData.nextChapterSlug);
}
function goToChap(ch, chSlug) {
  if (asActive) stopAS();
  READER_CHAPTER = ch;
  // El slug de manga no cambia al cambiar de capítulo — lo sacamos de la
  // data del capítulo que ya teníamos cargada.
  const mangaSlug = chapData?.slug || allMangas.find(m => m.name === READER_MANGA)?.slug || READER_MANGA;
  const chapterSlug = chSlug || ch;
  // replaceState (no pushState): cambiar de capítulo no debe apilar entradas
  // de historial. "Atrás" siempre sale del lector hacia el manga, sin
  // importar cuántos capítulos se hayan leído — es más predecible.
  history.replaceState({ page: 'reader', manga: READER_MANGA, chapter: ch }, '', Router.buildPath('reader', { mangaSlug, chapterSlug }));
  loadChapter();
}
function goToChapFromDrawer(ch, slug) {
  closeChapDrawer();
  if (!_loadingChapter && ch && ch !== READER_CHAPTER) goToChap(ch, slug);
}

// ── SALIR DEL LECTOR ────────────────────────────────────────────────────────────
function goBack() {
  if (asActive) stopAS();
  saveProgress();
  // history.back() dispara el popstate de app.js, que cierra #p-reader y
  // muestra la página/manga anterior — mismo mecanismo que closeDetail().
  history.back();
}

// ── BARRAS ─────────────────────────────────────────────────────────────────────
function showBars()   { botBarEl().classList.remove('hidden'); }
function hideBars()   { botBarEl().classList.add('hidden'); }
function toggleBars() { botBarEl().classList.toggle('hidden'); }

function updatePageNum(page, total) {
  const el = document.getElementById('b-page-num');
  if (el) el.textContent = `${page} / ${total}`;
}
function scrollToTop() { readerEl().scrollTo({ top: 0, behavior: 'smooth' }); }

// ── AUTO-SCROLL ──────────────────────────────────────────────────────────────────
function toggleAutoScroll() {
  asOn = !asOn;
  localStorage.setItem('rm_as', asOn);
  document.getElementById('tg-as').classList.toggle('on', asOn);
  document.getElementById('speed-row').style.display = asOn ? 'block' : 'none';
  if (!asOn && asActive) stopAS();
  updateFB();
}
function togglePlay() {
  if (!asOn) {
    asOn = true;
    localStorage.setItem('rm_as', true);
    document.getElementById('tg-as').classList.add('on');
    document.getElementById('speed-row').style.display = 'block';
  }
  if (asActive) stopAS(); else startAS();
}
function startAS() {
  if (!asOn || rMode === 'page') return;
  const el = readerEl();
  asActive = true; asLastTime = performance.now();
  updateFB();
  function step(now) {
    if (!asActive) return;
    const dt = (now - asLastTime) / 1000;
    asLastTime = now;
    el.scrollBy(0, asSpeed * dt);
    const atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 5;
    if (atEnd) { stopAS(); return; }
    asRAF = requestAnimationFrame(step);
  }
  asRAF = requestAnimationFrame(step);
}
function stopAS() {
  asActive = false;
  if (asRAF) { cancelAnimationFrame(asRAF); asRAF = null; }
  updateFB();
}
function setSpeed(val) {
  asSpeed = parseInt(val);
  localStorage.setItem('rm_speed', asSpeed);
  setSpeedUI(asSpeed);
}
function setSpeedUI(val) {
  document.getElementById('spd-val').textContent = val + 'px/s';
  const pct = ((val - 10) / (500 - 10) * 100).toFixed(1) + '%';
  document.getElementById('spd-slider').style.setProperty('--pct', pct);
}

// ── MODO NOCHE ─────────────────────────────────────────────────────────────────
function toggleNight() {
  nightOn = !nightOn;
  localStorage.setItem('rm_night', nightOn);
  document.body.classList.toggle('night', nightOn);
  document.getElementById('tg-night').classList.toggle('on', nightOn);
  updateFB();
}

// ── PANEL FLOTANTE EN PANTALLA ───────────────────────────────────────────────────
function toggleScreen() {
  screenOn = !screenOn;
  localStorage.setItem('rm_screen', screenOn);
  document.getElementById('tg-screen').classList.toggle('on', screenOn);
  floatPanelEl().classList.toggle('hidden', !screenOn);
}
function updateFB() {
  document.getElementById('fb-night').classList.toggle('on-night', nightOn);
  document.getElementById('fb-play').classList.toggle('on-play', asActive);
  document.getElementById('fb-play').innerHTML = asActive
    ? '<i class="ti ti-player-pause-filled"></i>'
    : '<i class="ti ti-player-play-filled"></i>';
}

// ── PANTALLA COMPLETA ─────────────────────────────────────────────────────────────
function toggleFS() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen?.().catch(()=>{});
    screen.orientation?.lock?.('portrait').catch(()=>{});
  } else {
    document.exitFullscreen?.();
  }
}
document.addEventListener('fullscreenchange', () => {
  const fs = !!document.fullscreenElement;
  const icon = fs ? 'ti-arrows-minimize' : 'ti-arrows-maximize';
  const tfs = document.getElementById('t-fs-btn'); if (tfs) tfs.innerHTML = `<i class="ti ${icon}"></i>`;
  const bfs = document.getElementById('b-fs-btn'); if (bfs) bfs.innerHTML = `<i class="ti ${icon}"></i>`;
});

// ── HOJA DE CONFIGURACIÓN ────────────────────────────────────────────────────────
function openCfg()  { document.getElementById('cfg-bg').classList.add('on'); document.getElementById('cfg-sheet').classList.add('on'); }
function closeCfg() { document.getElementById('cfg-bg').classList.remove('on'); document.getElementById('cfg-sheet').classList.remove('on'); }

// ── PROGRESO ───────────────────────────────────────────────────────────────────
async function saveProgress() {
  if (!READER_MANGA || !READER_CHAPTER) return;
  try {
    const page = rMode === 'page' ? curPage : (getCurPage?.() || 0);
    await fetch('/api/mangas/progress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.getToken() },
      body: JSON.stringify({ manga: READER_MANGA, chapter: READER_CHAPTER, page })
    });
    // Reflejar el capítulo como leído en Inicio/Series/Rankings al instante,
    // sin esperar a que el usuario refresque la página a mano.
    if (typeof applyMangaProgressLocal === 'function') applyMangaProgressLocal(READER_MANGA, READER_CHAPTER);
    API.invalidateManga(READER_MANGA);
  } catch {}
}

// ── DRAWER DE CAPÍTULOS ───────────────────────────────────────────────────────────
function openChapDrawer() {
  document.getElementById('chap-drawer-bg').classList.add('on');
  document.getElementById('chap-drawer').classList.add('on');
  setTimeout(() => {
    const current = document.querySelector('.drawer-item.current');
    if (current) current.scrollIntoView({ block: 'center' });
  }, 80);
}
function closeChapDrawer() {
  document.getElementById('chap-drawer-bg').classList.remove('on');
  document.getElementById('chap-drawer').classList.remove('on');
}

// ── TECLADO (PC) — solo cuando el lector está realmente abierto ───────────────────
document.addEventListener('keydown', e => {
  if (!readerEl().classList.contains('on')) return;
  if (['INPUT','TEXTAREA'].includes(e.target.tagName)) return;
  const k = e.key;
  if (k === 'ArrowLeft'  || k === 'ArrowUp')   prevPage();
  if (k === 'ArrowRight' || k === 'ArrowDown') nextPage();
  if (k === 'Escape')  goBack();
  if (k === 'f' || k === 'F') toggleFS();
  if (k === ' ') { e.preventDefault(); togglePlay(); }
  if (k === 'n' || k === 'N') toggleNight();
});

// ── INICIALIZAR CONTROLES UNA SOLA VEZ (antes se repetía en cada recarga de
// reader.html; ahora el documento no se recarga, así que esto corre una vez) ──
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('tg-night').classList.toggle('on', nightOn);
  document.getElementById('tg-as').classList.toggle('on', asOn);
  document.getElementById('tg-screen').classList.toggle('on', screenOn);
  document.getElementById('speed-row').style.display = asOn ? 'block' : 'none';
  document.getElementById('spd-slider').value = asSpeed;
  setSpeedUI(asSpeed);
  setModeUI(rMode);
  updateFB();
  if (!screenOn) floatPanelEl().classList.add('hidden');
  if (nightOn) document.body.classList.add('night');
});
