const express = require('express');
const fs      = require('fs');
const path    = require('path');
const router  = express.Router();

const catalogIndex = require('../data/catalogIndex');
const imageCache   = require('../lib/imageCache');
const { writeJsonAtomic, formatDate } = require('../lib/fsHelpers');
const { ALLOWED_FORCED_SOURCES } = require('../lib/sources');

const PROGRESS_FILE = path.join(__dirname, '../progress.json');
const ORPHANS_FILE  = path.join(__dirname, '../progress_orphans.json');
const BACKUP_DIR    = path.join(__dirname, '../backups');

// Cuántos días debe faltar un manga en TODOS los escaneos completos antes de
// borrar su historial de forma definitiva. Mientras no pase este tiempo, solo
// queda "en observación" — nunca se borra en el momento en que desaparece.
const ORPHAN_GRACE_DAYS = 7;
// Backups rotativos de progress.json que se guardan antes de cualquier borrado
// definitivo (bak1 = más reciente, bak5 = más viejo).
const MAX_PROGRESS_BACKUPS = 5;

// El nombre de un manga (viene del cliente: body o params) se usa varias
// veces más abajo como CLAVE de un objeto (progress[manga] = ...). Si
// alguien manda literalmente "__proto__", "constructor" o "prototype" como
// nombre, esa asignación no crea una propiedad común — dispara el setter
// heredado de Object.prototype y cambia el [[Prototype]] del objeto local
// en cuestión. No es una contaminación global (cada objeto de progreso acá
// es local a ese request), pero cuesta una línea cerrarlo del todo.
const UNSAFE_OBJECT_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

// ── BACKUP ROTATIVO (solo se llama antes de un borrado definitivo, no en
// cada guardado normal, para no generar I/O innecesario) ─────────────────────
function backupBeforeDestructiveWrite(filePath) {
  if (!fs.existsSync(filePath)) return;
  try {
    fs.mkdirSync(BACKUP_DIR, { recursive: true });
    const base = path.basename(filePath);
    for (let i = MAX_PROGRESS_BACKUPS - 1; i >= 1; i--) {
      const src = path.join(BACKUP_DIR, `${base}.bak${i}`);
      const dst = path.join(BACKUP_DIR, `${base}.bak${i + 1}`);
      if (fs.existsSync(src)) fs.renameSync(src, dst);
    }
    fs.copyFileSync(filePath, path.join(BACKUP_DIR, `${base}.bak1`));
  } catch(e) { console.error('[Backup] Error creando backup:', e.message); }
}

// ── ORPHAN TRACKING (mangas que no aparecieron en el último escaneo) ────────
// Formato: { "NombreManga": "2026-07-09T12:00:00.000Z" } → fecha en que se
// notó ausente por primera vez. Solo se borra su progreso si sigue ausente
// después de ORPHAN_GRACE_DAYS días consecutivos.
function loadOrphans() {
  if (!fs.existsSync(ORPHANS_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(ORPHANS_FILE, 'utf8')); } catch { return {}; }
}
function saveOrphans(data) {
  try { writeJsonAtomic(ORPHANS_FILE, data); } catch(e) { console.error('[Progress] Error guardando orphans:', e.message); }
}

// ── CACHÉ DE PROGRESO (por usuario, TTL corto — esto SÍ sigue siendo un
// archivo propio que conviene no releer en cada request) ────────────────────
let _progressCache     = null;
let _progressCacheTime = 0;
const PROGRESS_TTL     = 10 * 1000;

// ── PROGRESO POR USUARIO ─────────────────────────────────────────────────────
// Formato: { "userId": { "mangaName": { readChapters, lastChapter, lastPage } } }
// Migración automática desde formato viejo (sin userId)

function _loadRawProgress() {
  const now = Date.now();
  if (_progressCache && (now - _progressCacheTime) < PROGRESS_TTL) return _progressCache;
  if (!fs.existsSync(PROGRESS_FILE)) { _progressCache = {}; _progressCacheTime = now; return {}; }
  try {
    let data = JSON.parse(fs.readFileSync(PROGRESS_FILE, 'utf8'));
    const keys = Object.keys(data);
    if (keys.length > 0 && data[keys[0]]?.readChapters) {
      console.log('[Progress] Migrando formato viejo → nuevo (por usuario)...');
      const migrated = { __legacy__: data };
      writeJsonAtomic(PROGRESS_FILE, migrated);
      data = migrated;
    }
    _progressCache = data;
    _progressCacheTime = now;
    return data;
  } catch { return {}; }
}

function getProgress(userId) {
  const all = _loadRawProgress();
  const key = String(userId || '__legacy__');
  if (all[key] && Object.keys(all[key]).length > 0) return all[key];
  if (all['__legacy__'] && Object.keys(all['__legacy__']).length > 0) {
    all[key] = { ...all['__legacy__'] };
    delete all['__legacy__'];
    _progressCache = all;
    _progressCacheTime = Date.now();
    try { writeJsonAtomic(PROGRESS_FILE, all); } catch(e) { console.error('[Progress] Error migrando:', e.message); }
    console.log(`[Progress] Migrado __legacy__ → usuario ${key}`);
    return all[key];
  }
  return {};
}

function saveProgress(userId, data) {
  const key = String(userId || '__legacy__');
  const all = _loadRawProgress();
  all[key] = data;
  _progressCache = all;
  _progressCacheTime = Date.now();
  try { writeJsonAtomic(PROGRESS_FILE, all); }
  catch(e) { console.error('[Progress] Error guardando:', e.message); }
}

// ── LIMPIEZA DE PROGRESO Y FECHAS (con período de gracia) ────────────────────
// Regla de seguridad #1: si el escaneo de carpetas de este arranque no fue
// completo (algún MANGA_PATH_N configurado en .env no estaba disponible, o
// tiró un error a mitad de camino), NO se borra absolutamente nada.
//
// Regla de seguridad #2: incluso con un escaneo completo, un manga que ya no
// aparece no se borra de inmediato — se marca "en observación" con la fecha
// en que se notó ausente por primera vez (progress_orphans.json). Solo si
// sigue faltando en escaneos completos posteriores durante ORPHAN_GRACE_DAYS
// días seguidos, se borra su historial (con backup previo).
function runProgressCleanup(validMangas, scanComplete) {
  if (!scanComplete) {
    console.warn('[Progress] Limpieza omitida: no se pudo escanear alguna carpeta configurada en este arranque. No se borra nada.');
    return;
  }

  const validSet = new Set(validMangas);
  const orphans  = loadOrphans();
  const now      = Date.now();
  const graceMs  = ORPHAN_GRACE_DAYS * 24 * 60 * 60 * 1000;
  const detectedDates = catalogIndex.detectedDatesRef;

  const all = _loadRawProgress();
  const trackedNames = new Set();
  for (const userId of Object.keys(all)) {
    const userProgress = all[userId];
    if (typeof userProgress !== 'object') continue;
    Object.keys(userProgress).forEach(m => trackedNames.add(m));
  }
  Object.keys(detectedDates).forEach(key => trackedNames.add(key.split('/')[0]));

  for (const name of Object.keys(orphans)) {
    if (validSet.has(name)) delete orphans[name];
  }

  const toDelete = [];
  for (const name of trackedNames) {
    if (validSet.has(name)) continue;
    if (!orphans[name]) { orphans[name] = now; continue; }
    if (now - orphans[name] >= graceMs) toDelete.push(name);
  }

  if (toDelete.length > 0) {
    backupBeforeDestructiveWrite(PROGRESS_FILE);

    let removedProgress = 0;
    for (const userId of Object.keys(all)) {
      const userProgress = all[userId];
      if (typeof userProgress !== 'object') continue;
      for (const manga of toDelete) {
        if (userProgress[manga]) { delete userProgress[manga]; removedProgress++; }
      }
    }
    if (removedProgress > 0) {
      writeJsonAtomic(PROGRESS_FILE, all);
      _progressCache = all;
      _progressCacheTime = Date.now();
    }

    let removedDates = 0;
    for (const key of Object.keys(detectedDates)) {
      if (toDelete.includes(key.split('/')[0])) { delete detectedDates[key]; removedDates++; }
    }
    if (removedDates > 0) catalogIndex.saveDetectedDatesDebounced();

    toDelete.forEach(name => delete orphans[name]);
    console.log(`[Progress] Limpieza definitiva tras ${ORPHAN_GRACE_DAYS} días ausentes: ${toDelete.join(', ')} (${removedProgress} entrada(s) de progreso, ${removedDates} fecha(s), backup guardado en ${BACKUP_DIR}).`);
  }

  saveOrphans(orphans);
  const stillWatching = Object.keys(orphans);
  if (stillWatching.length > 0) {
    console.log(`[Progress] En observación (aún no se borra su historial, esperando ${ORPHAN_GRACE_DAYS} días ausentes): ${stillWatching.join(', ')}`);
  }
}

function startWatcher() { catalogIndex.startWatcher(runProgressCleanup); }
function stopWatcher()  { catalogIndex.stopWatcher(); }

function setCacheHeaders(res, seconds = 30) {
  res.setHeader('Cache-Control', `public, max-age=${seconds}, stale-while-revalidate=${seconds * 2}`);
}
// Para respuestas que incluyen datos por usuario mutables (progreso de
// lectura: capítulos marcados/desmarcados como leídos). "no-store" en vez de
// "no-cache": no-cache SIGUE permitiendo que el navegador guarde la
// respuesta y la sirva de su caché si el ETag coincide (vía 304) — y como el
// progreso puede cambiar sin que cambie nada del catálogo (mismo ETag),
// el navegador podía servir un cuerpo cacheado con el progreso desactualizado.
// "no-store" prohíbe ese guardado directamente: cada pedido va sí o sí al
// servidor y trae el progreso real tal como está en ese momento.
function setUserDataCacheHeaders(res) {
  res.setHeader('Cache-Control', 'no-store');
}

const { visibleTo } = require('../lib/visibility');

// ── RUTAS ─────────────────────────────────────────────────────────────────────

// GET /api/mangas — ahora lee directo del índice en memoria, sin tocar disco.
router.get('/', (req, res) => {
  if (!catalogIndex.getMangaNames().length) return res.status(404).json({ error: 'No se encontraron carpetas de mangas.' });
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };

  const progress = getProgress(req.user?.userId);
  const mangas = catalogIndex.getAllEntries()
    .filter(e => visibleTo(e, restrictions))
    .map(e => ({
      name: e.name, slug: e.slug, cover: e.cover, chapterCount: e.chapters.length,
      lastChapter: e.chapters.length ? e.chapters[e.chapters.length - 1].number : null,
      lastChapterDate: e.lastChapterDate,
      addedDate: e.addedDate,
      metadata: e.metadata,
      progress: progress[e.name] || {}
    }));

  setUserDataCacheHeaders(res);
  res.json(mangas);
});

// GET /api/mangas/latest-paged — antes escaneaba TODO el catálogo por cada
// combinación de filtros/página aunque solo se devolvieran 20 items. Ahora
// arma la lista completa de "últimos capítulos por manga" en memoria (barato:
// son operaciones de array sobre el índice, no I/O) y recién ahí pagina.
router.get('/latest-paged', (req, res) => {
  const page      = Math.max(1, parseInt(req.query.page)  || 1);
  const limit     = Math.min(parseInt(req.query.limit) || 20, 50);
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  const { canViewAdult, canViewNormal, blockedMangas } = restrictions;
  const showAdult = req.query.adult     === 'true' && canViewAdult;
  const onlyAdult = req.query.onlyAdult === 'true' && canViewAdult;
  const typesParam  = String(req.query.types || 'manga,manhwa,manhua').toLowerCase();
  const enabledTypes = new Set(typesParam.split(',').filter(Boolean));

  const progress = getProgress(req.user?.userId);
  const groups = [];
  for (const e of catalogIndex.getAllEntries()) {
    if (!e.chapters.length) continue;
    if (blockedMangas.includes(e.name)) continue;
    if (!enabledTypes.has(String(e.metadata.type).toLowerCase())) continue;
    if (onlyAdult) {
      if (!e.metadata.adult) continue;
    } else {
      if (!showAdult && e.metadata.adult) continue;
      if (!canViewNormal && !e.metadata.adult) continue;
    }
    const prog = progress[e.name] || {};
    const lastChaps = e.chapters.slice(-2).reverse().map(ch => ({
      chapter: ch.number, chapterSlug: ch.slug, date: ch.date, dateLabel: formatDate(ch.date),
      read: prog.readChapters?.includes(ch.number) || false
    }));
    groups.push({
      manga: e.name, slug: e.slug, cover: e.cover,
      status: e.metadata.status, adult: e.metadata.adult, type: e.metadata.type,
      sources: (e.sources?.ranked || []).slice(0, 2),
      latestDate: lastChaps[0]?.date || null,
      chapters: lastChaps
    });
  }
  groups.sort((a, b) => new Date(b.latestDate) - new Date(a.latestDate));

  const total      = groups.length;
  const totalPages = Math.ceil(total / limit);
  const offset     = (page - 1) * limit;
  const pageItems  = groups.slice(offset, offset + limit);

  setUserDataCacheHeaders(res);
  res.json({ items: pageItems, total, page, totalPages, perPage: limit });
});

// ── ESTADÍSTICAS ──────────────────────────────────────────────────────────────

// GET /api/mangas/stats/summary
router.get('/stats/summary', (req, res) => {
  const progress = getProgress(req.user?.userId);
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  let totalMangas = 0, totalChapters = 0;
  const mangaList = [];

  for (const e of catalogIndex.getAllEntries()) {
    if (!visibleTo(e, restrictions)) continue;
    const prog = progress[e.name] || {};
    const read = prog.readChapters?.length || 0;
    totalMangas++;
    totalChapters += e.chapters.length;
    mangaList.push({
      name: e.name, total: e.chapters.length, read,
      completed: read >= e.chapters.length && e.chapters.length > 0,
      type: e.metadata.type, status: e.metadata.status
    });
  }

  const readChapters  = Object.values(progress).reduce((s, p) => s + (p.readChapters?.length || 0), 0);
  const inProgress    = mangaList.filter(m => m.read > 0 && !m.completed);
  const completed     = mangaList.filter(m => m.completed);
  const notStarted    = mangaList.filter(m => m.read === 0);
  const byType        = mangaList.reduce((acc, m) => { acc[m.type] = (acc[m.type]||0)+1; return acc; }, {});
  const byStatus      = mangaList.reduce((acc, m) => { acc[m.status] = (acc[m.status]||0)+1; return acc; }, {});
  const topRead       = [...mangaList].sort((a,b)=>b.read-a.read).slice(0,5);

  res.json({
    totalMangas, totalChapters, readChapters,
    inProgress: inProgress.length, completed: completed.length, notStarted: notStarted.length,
    completionPct: totalChapters > 0 ? Math.round(readChapters / totalChapters * 100) : 0,
    byType, byStatus, topRead,
    mangaList: mangaList.sort((a,b) => b.read - a.read)
  });
});

// ── EXPORT / IMPORT PROGRESO ──────────────────────────────────────────────────

// GET /api/mangas/progress/export
router.get('/progress/export', (req, res) => {
  const progress = getProgress(req.user?.userId);
  const exportData = { exportedAt: new Date().toISOString(), version: 1, progress };
  res.setHeader('Content-Disposition', `attachment; filename="progreso-manga-${new Date().toISOString().slice(0,10)}.json"`);
  res.setHeader('Content-Type', 'application/json');
  res.json(exportData);
});

// POST /api/mangas/progress/import
router.post('/progress/import', (req, res) => {
  const { progress, merge } = req.body;
  if (!progress || typeof progress !== 'object') return res.status(400).json({ error: 'Datos inválidos.' });

  const keys = Object.keys(progress);
  let mangaMap = progress;

  if (keys.length > 0) {
    const firstVal = progress[keys[0]];
    if (firstVal && typeof firstVal === 'object' && !Array.isArray(firstVal)) {
      if (firstVal.readChapters && Array.isArray(firstVal.readChapters)) {
        mangaMap = progress;
      } else if (typeof Object.values(firstVal)[0] === 'object') {
        mangaMap = firstVal;
      }
    }
  }

  const current  = merge ? getProgress(req.user?.userId) : {};
  const imported = { ...current };
  let count = 0;

  for (const [manga, data] of Object.entries(mangaMap)) {
    if (UNSAFE_OBJECT_KEYS.has(manga)) continue;
    if (!data || !Array.isArray(data.readChapters)) continue;
    if (merge && imported[manga]) {
      const combined = new Set([...(imported[manga].readChapters||[]), ...data.readChapters]);
      imported[manga] = { ...imported[manga], ...data, readChapters: [...combined] };
    } else {
      imported[manga] = data;
    }
    count++;
  }

  saveProgress(req.user?.userId, imported);
  res.json({ ok: true, imported: count, total: Object.keys(imported).length });
});

// GET /api/mangas/:manga — antes recalculaba imageCount por capítulo con un
// readdirSync por cada uno en cada cache miss. Ahora sale directo del índice.
router.get('/:manga', (req, res) => {
  // Acepta el nombre real de carpeta (compatibilidad) O el slug de la URL
  // linda (/series/:slug) — se prueba nombre exacto primero, slug después.
  const rawParam = decodeURIComponent(req.params.manga);
  const name = catalogIndex.resolveMangaParam(rawParam) || rawParam;
  const entry = catalogIndex.ensureFresh(name);
  if (!entry) return res.status(404).json({ error: 'Manga no encontrado.' });

  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  if (!visibleTo(entry, restrictions)) return res.status(404).json({ error: 'Manga no encontrado.' });

  const progress = getProgress(req.user?.userId);
  const prog = progress[name] || {};

  setUserDataCacheHeaders(res);
  res.json({
    name: entry.name, slug: entry.slug, cover: entry.cover, chapterCount: entry.chapters.length,
    metadata: entry.metadata,
    sources: (entry.sources?.ranked || []).slice(0, 1),
    chapters: entry.chapters.map(ch => ({
      number: ch.number, slug: ch.slug, imageCount: ch.images.length,
      read: prog.readChapters?.includes(ch.number) || false,
      date: ch.date, dateLabel: formatDate(ch.date)
    })),
    progress: prog
  });
});

// GET /api/mangas/:manga/:chapter/images — antes hacía readdirSync del
// capítulo en cada apertura. Ahora la lista de imágenes ya está en el
// índice, así que esta ruta no toca el disco para nada (salvo el
// precalentado en background de las primeras páginas).
router.get('/:manga/:chapter/images', (req, res) => {
  // Igual que en /:manga: acepta nombre real o slug para el manga. El
  // capítulo se resuelve DESPUÉS de tener la entry (el slug de capítulo solo
  // tiene sentido buscándolo dentro de los capítulos de ESE manga puntual).
  const rawMangaParam = decodeURIComponent(req.params.manga);
  const name  = catalogIndex.resolveMangaParam(rawMangaParam) || rawMangaParam;
  const entry = catalogIndex.ensureFresh(name);
  if (!entry) return res.status(404).json({ error: 'Capítulo no encontrado.' });

  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  if (!visibleTo(entry, restrictions)) return res.status(404).json({ error: 'Capítulo no encontrado.' });

  const rawChapterParam = decodeURIComponent(req.params.chapter);
  const ch = catalogIndex.resolveChapterParam(entry, rawChapterParam) || rawChapterParam;

  const idx = entry.chapters.findIndex(c => c.number === ch);
  if (idx === -1) return res.status(404).json({ error: 'Capítulo no encontrado.' });
  const chapterEntry = entry.chapters[idx];
  const prevEntry = idx > 0 ? entry.chapters[idx - 1] : null;
  const nextEntry = idx < entry.chapters.length - 1 ? entry.chapters[idx + 1] : null;

  const images = chapterEntry.images.map(img =>
    `/api/images/${encodeURIComponent(name)}/${encodeURIComponent(ch)}/${encodeURIComponent(img)}`
  );

  // Precalentar en RAM las primeras páginas mientras el cliente todavía está
  // en la pantalla de "Cargando capítulo..." — para cuando pida la imagen
  // real ya está en memoria en vez de tener que ir al disco externo.
  const root = catalogIndex.findMangaRoot(name);
  const chapterDir = path.join(root, name, ch);
  imageCache.warmChapter(chapterEntry.images.map(img => path.resolve(path.join(chapterDir, img))));

  setCacheHeaders(res, 300);
  res.json({
    manga: name, slug: entry.slug, chapter: ch, chapterSlug: chapterEntry.slug,
    cover: entry.cover, images, total: images.length,
    prevChapter: prevEntry ? prevEntry.number : null,
    prevChapterSlug: prevEntry ? prevEntry.slug : null,
    nextChapter: nextEntry ? nextEntry.number : null,
    nextChapterSlug: nextEntry ? nextEntry.slug : null,
    allChapters: entry.chapters.map(c => ({ number: c.number, slug: c.slug }))
  });
});

// POST /api/mangas/unread
router.post('/unread', (req, res) => {
  const { manga, chapter } = req.body;
  if (!manga || !chapter || UNSAFE_OBJECT_KEYS.has(manga)) return res.status(400).json({ error: 'Faltan datos.' });
  const entry = catalogIndex.getMangaEntry(manga);
  if (!entry) return res.status(404).json({ error: 'Manga no encontrado.' });
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  if (!visibleTo(entry, restrictions)) return res.status(404).json({ error: 'Manga no encontrado.' });

  const progress = getProgress(req.user?.userId);
  if (progress[manga]?.readChapters) {
    progress[manga].readChapters = progress[manga].readChapters.filter(c => c !== chapter);
    saveProgress(req.user?.userId, progress);
  }
  res.json({ ok: true });
});

// POST /api/mangas/mark-all-read
router.post('/mark-all-read', (req, res) => {
  const { manga } = req.body;
  if (!manga || UNSAFE_OBJECT_KEYS.has(manga)) return res.status(400).json({ error: 'Falta el nombre del manga.' });
  const entry = catalogIndex.ensureFresh(manga);
  if (!entry) return res.status(404).json({ error: 'Manga no encontrado.' });
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  if (!visibleTo(entry, restrictions)) return res.status(404).json({ error: 'Manga no encontrado.' });

  const chapters = entry.chapters.map(c => c.number);
  const progress = getProgress(req.user?.userId);
  if (!progress[manga]) progress[manga] = { readChapters: [] };
  progress[manga].readChapters = [...new Set([...(progress[manga].readChapters || []), ...chapters])];
  if (chapters.length > 0) progress[manga].lastChapter = chapters[chapters.length - 1];
  saveProgress(req.user?.userId, progress);
  res.json({ ok: true });
});

// POST /api/mangas/unread-all
router.post('/unread-all', (req, res) => {
  const { manga } = req.body;
  if (!manga || UNSAFE_OBJECT_KEYS.has(manga)) return res.status(400).json({ error: 'Falta el nombre del manga.' });
  const entry = catalogIndex.getMangaEntry(manga);
  if (!entry) return res.status(404).json({ error: 'Manga no encontrado.' });
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  if (!visibleTo(entry, restrictions)) return res.status(404).json({ error: 'Manga no encontrado.' });

  const progress = getProgress(req.user?.userId);
  if (progress[manga]) { progress[manga].readChapters = []; saveProgress(req.user?.userId, progress); }
  res.json({ ok: true });
});

// POST /api/mangas/progress
router.post('/progress', (req, res) => {
  const { manga, chapter, page } = req.body;
  if (!manga || !chapter || UNSAFE_OBJECT_KEYS.has(manga)) return res.status(400).json({ error: 'Faltan datos.' });
  const entry = catalogIndex.getMangaEntry(manga);
  if (!entry) return res.status(404).json({ error: 'Manga no encontrado.' });
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  if (!visibleTo(entry, restrictions)) return res.status(404).json({ error: 'Manga no encontrado.' });

  const progress = getProgress(req.user?.userId);
  if (!progress[manga]) progress[manga] = { readChapters: [] };
  if (!progress[manga].readChapters.includes(chapter)) progress[manga].readChapters.push(chapter);
  progress[manga].lastChapter = chapter;
  progress[manga].lastPage    = page || 0;
  saveProgress(req.user?.userId, progress);
  res.json({ ok: true });
});

// ── METADATA ──────────────────────────────────────────────────────────────────

// GET /api/mangas/:manga/metadata
router.get('/:manga/metadata', (req, res) => {
  const rawParam = decodeURIComponent(req.params.manga);
  const name = catalogIndex.resolveMangaParam(rawParam) || rawParam;
  const entry = catalogIndex.getMangaEntry(name);
  if (!entry) return res.status(404).json({ error: 'Manga no encontrado.' });
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  if (!visibleTo(entry, restrictions)) return res.status(404).json({ error: 'Manga no encontrado.' });
  // 'detectedSources' es solo informativo para el editor (mostrar "detectado
  // automáticamente: X, Y" al lado del selector de fuente forzada) — siempre
  // es el resultado REAL de registro_progreso.json, sin pisar por una fuente
  // forzada activa, así el admin ve ambos datos aunque haya un override puesto.
  res.json({ ...entry.metadata, detectedSources: entry.sources?.autoRanked || [] });
});

// PUT /api/mangas/:manga/metadata
// Géneros que el cliente interpreta como +18 aunque "adult" diga false
// (client/js/app.js → filterAdult / adultGenres). Deben coincidir.
const ADULT_MARKER_GENRES = []; // vacío a propósito: 'adult' manda solo, sin heurística de género (ver conversación 2026-09)

router.put('/:manga/metadata', (req, res) => {
  if (req.user?.role !== 'admin') return res.status(403).json({ error: 'Solo administradores.' });
  const rawParam = decodeURIComponent(req.params.manga);
  const name = catalogIndex.resolveMangaParam(rawParam) || rawParam;
  const root = catalogIndex.findMangaRoot(name);
  const mp   = path.join(root, name);
  if (!fs.existsSync(mp)) return res.status(404).json({ error: 'Manga no encontrado.' });
  const entry = catalogIndex.getMangaEntry(name);
  const allowed = ['type','status','genres','synopsis','ranking','adult','forcedSource'];
  const current = entry ? entry.metadata : {};
  const updated = { ...current };
  for (const key of allowed) {
    if (req.body[key] !== undefined) updated[key] = req.body[key];
  }
  // 'forcedSource': o es null/'' (modo automático, según registro_progreso.json)
  // o una de las fuentes conocidas + 'externa' — cualquier otro valor se
  // rechaza acá para no dejar guardar un typo que después el badge muestre
  // como "fuente desconocida" para siempre (ver lib/sources.js).
  if (updated.forcedSource && !ALLOWED_FORCED_SOURCES.includes(updated.forcedSource)) {
    return res.status(400).json({ error: `forcedSource inválida: "${updated.forcedSource}".` });
  }
  updated.forcedSource = updated.forcedSource || null;
  // Si el manga queda marcado como NO +18, sacar cualquier género que lo
  // siga etiquetando como adulto (ej. "Hentai", "+18"). El editor guarda
  // "adult" y "genres" como campos independientes — si no se hace esto,
  // pueden quedar desincronizados: el archivo dice adult:false pero el
  // filtro del cliente lo sigue mostrando como +18 por el género suelto.
  if (updated.adult === false && Array.isArray(updated.genres)) {
    updated.genres = updated.genres.filter(g => !ADULT_MARKER_GENRES.includes(String(g).toLowerCase().trim()));
  }
  const file = path.join(mp, 'metadata.json');
  try {
    fs.writeFileSync(file, JSON.stringify(updated, null, 2));
    catalogIndex.touchManga(name); // reconstruye esta única entrada del índice
    res.json({ ok: true, metadata: updated });
  } catch(e) { res.status(500).json({ error: e.message }); }
});


module.exports = router;
module.exports.startWatcher = startWatcher;
module.exports.stopWatcher  = stopWatcher;
