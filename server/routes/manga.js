const express = require('express');
const fs      = require('fs');
const path    = require('path');
const router  = express.Router();

const PROGRESS_FILE = path.join(__dirname, '../progress.json');
const DETECTED_FILE = path.join(__dirname, '../detected_dates.json');

let detectedDates = {};

// ── CACHÉ EN MEMORIA ──────────────────────────────────────────────────────────
let _listCache     = null;
let _listCacheTime = 0;
const CACHE_TTL    = 5 * 60 * 1000;

const _detailCache     = new Map();
const _detailCacheTime = new Map();

let _progressCache     = null;
let _progressCacheTime = 0;
const PROGRESS_TTL     = 10 * 1000;

const _imageCache     = new Map();
const IMAGE_CACHE_TTL = 30 * 60 * 1000;

function invalidateCache() {
  _listCache = null;
  _detailCache.clear();
  _detailCacheTime.clear();
}
function invalidateMangaCache(name) {
  _detailCache.delete(name);
  _detailCacheTime.delete(name);
  _listCache = null;
  _progressCache = null;
}

// ── PROGRESO POR USUARIO ─────────────────────────────────────────────────────
// Formato: { "userId": { "mangaName": { readChapters, lastChapter, lastPage } } }
// Migración automática desde formato viejo (sin userId)

function _loadRawProgress() {
  const now = Date.now();
  if (_progressCache && (now - _progressCacheTime) < PROGRESS_TTL) return _progressCache;
  if (!fs.existsSync(PROGRESS_FILE)) { _progressCache = {}; _progressCacheTime = now; return {}; }
  try {
    let data = JSON.parse(fs.readFileSync(PROGRESS_FILE, 'utf8'));
    // Detectar formato viejo: las claves son nombres de manga (strings sin estructura de userId)
    // En el formato nuevo, las claves son IDs de usuario numéricos/cortos
    // Heurística: si algún valor tiene directamente readChapters[] es formato viejo
    const keys = Object.keys(data);
    if (keys.length > 0 && data[keys[0]]?.readChapters) {
      console.log('[Progress] Migrando formato viejo → nuevo (por usuario)...');
      const migrated = { __legacy__: data };
      fs.writeFileSync(PROGRESS_FILE, JSON.stringify(migrated));
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
  // Si no hay datos para este usuario, intentar con __legacy__ (migración gradual)
  if (all[key] && Object.keys(all[key]).length > 0) return all[key];
  if (all['__legacy__'] && Object.keys(all['__legacy__']).length > 0) {
    // Migrar __legacy__ a este usuario la primera vez que accede
    all[key] = { ...all['__legacy__'] };
    delete all['__legacy__'];
    _progressCache = all;
    _progressCacheTime = Date.now();
    fs.writeFile(PROGRESS_FILE, JSON.stringify(all), () => {});
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
  fs.writeFile(PROGRESS_FILE, JSON.stringify(all), err => {
    if (err) console.error('[Progress] Error guardando:', err.message);
  });
}

// ── LIMPIEZA DE PROGRESS ─────────────────────────────────────────────────────
function cleanProgress(validMangas) {
  if (!fs.existsSync(PROGRESS_FILE)) return;
  try {
    const all      = _loadRawProgress();
    const validSet = new Set(validMangas);
    let removed    = 0;
    // Iterar por cada usuario y limpiar sus mangas huérfanos
    for (const userId of Object.keys(all)) {
      const userProgress = all[userId];
      if (typeof userProgress !== 'object') continue;
      for (const manga of Object.keys(userProgress)) {
        if (!validSet.has(manga)) { delete userProgress[manga]; removed++; }
      }
    }
    if (removed > 0) {
      console.log(`[Progress] Limpieza: ${removed} entrada(s) huérfana(s) eliminadas.`);
      fs.writeFileSync(PROGRESS_FILE, JSON.stringify(all));
      _progressCache = all;
      _progressCacheTime = Date.now();
    }
  } catch(e) { console.error('[Progress] Error en limpieza:', e.message); }
}

// ── FECHAS DETECTADAS ─────────────────────────────────────────────────────────
function loadDetectedDates() {
  if (!fs.existsSync(DETECTED_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(DETECTED_FILE, 'utf8')); } catch { return {}; }
}
function saveDetectedDates() {
  fs.writeFile(DETECTED_FILE, JSON.stringify(detectedDates, null, 2), err => {
    if (err) console.error('[Watcher]', err.message);
  });
}
function getDetectedDate(manga, chapter) {
  const key = chapter ? `${manga}/${chapter}` : manga;
  return detectedDates[key] ? new Date(detectedDates[key]) : null;
}

// ── LIMPIEZA DE DETECTED_DATES (mejora #7) ────────────────────────────────────
function cleanDetectedDates(validMangas) {
  const validSet = new Set(validMangas);
  let removed = 0;
  for (const key of Object.keys(detectedDates)) {
    const manga = key.split('/')[0];
    if (!validSet.has(manga)) { delete detectedDates[key]; removed++; }
  }
  if (removed > 0) {
    console.log(`[Watcher] Limpieza: ${removed} fecha(s) huérfana(s) eliminadas.`);
    saveDetectedDates();
  }
}

// ── WATCHER (mejora #5: soporte MANGA_PATH_2) ─────────────────────────────────
let watchers = [];

function startWatcher() {
  detectedDates = loadDetectedDates();
  const roots = getMangaRoots();
  if (!roots.length) { console.warn('[Watcher] No se encontraron carpetas de mangas.'); return; }

  const existing  = new Set(Object.keys(detectedDates));
  const allMangas = [];

  // Escanear todos los roots
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    try {
      fs.readdirSync(root).forEach(manga => {
        const mp = path.join(root, manga);
        if (!fs.statSync(mp).isDirectory()) return;
        allMangas.push(manga);
        if (!existing.has(manga)) detectedDates[manga] = getFolderDate(mp).toISOString();
        getChapters(mp).forEach(ch => {
          const k = `${manga}/${ch}`;
          if (!existing.has(k)) detectedDates[k] = getFolderDate(path.join(mp, ch)).toISOString();
        });
      });
    } catch(e) { console.error(`[Watcher] Error escaneando ${root}:`, e.message); }
  }

  saveDetectedDates();

  // Limpiar entradas huérfanas al arrancar
  cleanProgress(allMangas);
  cleanDetectedDates(allMangas);

  // Un watcher por cada root (mejora #5)
  const pending = new Set(); let debounce = null;

  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    try {
      const w = fs.watch(root, { recursive: true }, (ev, fn) => {
        if (!fn) return;
        const parts = fn.split(path.sep);
        if (parts.length > 2) return;
        const key = parts.join('/');
        if (pending.has(key)) return;
        pending.add(key);
        clearTimeout(debounce);
        debounce = setTimeout(() => {
          for (const k of pending) {
            if (!detectedDates[k]) {
              detectedDates[k] = new Date().toISOString();
              console.log(`[Watcher] ${k.includes('/') ? 'Nuevo capítulo' : 'Nuevo manga'}: ${k}`);
            }
          }
          pending.clear();
          saveDetectedDates();
          invalidateCache();
          _imageCache.clear();
        }, 800);
      });
      watchers.push(w);
      console.log(`[Watcher] Monitoreando: ${root}`);
    } catch(e) { console.warn(`[Watcher] fs.watch no disponible en ${root}:`, e.message); }
  }
}

function stopWatcher() { watchers.forEach(w => w.close()); watchers = []; }

// ── HELPERS ───────────────────────────────────────────────────────────────────
function getMangaRoots() {
  const candidates = [path.resolve(process.env.MANGA_PATH || './main')];
  if (process.env.MANGA_PATH_2) candidates.push(path.resolve(process.env.MANGA_PATH_2));
  const existing = candidates.filter(r => { try { return fs.existsSync(r); } catch { return false; } });
  // Si ninguna existe devolver igualmente la primera para que los errores sean descriptivos
  return existing.length > 0 ? existing : candidates.slice(0, 1);
}
function getMangaRoot() { return getMangaRoots()[0]; }

function findMangaRoot(name) {
  for (const root of getMangaRoots()) {
    try { if (fs.existsSync(path.join(root, name))) return root; } catch {}
  }
  return getMangaRoot();
}

function getMetadata(p) {
  const f = path.join(p, 'metadata.json');
  if (!fs.existsSync(f)) return {};
  try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return {}; }
}

// Ordenamiento natural: maneja 001, 01, 1 correctamente
function naturalCompare(a, b) {
  const re = /(\d+)/g;
  const partsA = String(a).split(re);
  const partsB = String(b).split(re);
  for (let i = 0; i < Math.max(partsA.length, partsB.length); i++) {
    const pa = partsA[i] ?? '';
    const pb = partsB[i] ?? '';
    if (i % 2 === 1) {
      const diff = parseInt(pa || '0', 10) - parseInt(pb || '0', 10);
      if (diff !== 0) return diff;
    } else {
      if (pa < pb) return -1;
      if (pa > pb) return  1;
    }
  }
  return 0;
}
function extractNum(str) { const m = String(str).match(/(\d+(?:\.\d+)?)/); return m ? parseFloat(m[1]) : 0; }

function getChapters(mp) {
  try {
    return fs.readdirSync(mp)
      .filter(f => { try { return fs.statSync(path.join(mp, f)).isDirectory(); } catch { return false; } })
      .sort(naturalCompare);
  } catch { return []; }
}

function getImages(cp) {
  const cached = _imageCache.get(cp);
  if (cached && (Date.now() - cached.t) < IMAGE_CACHE_TTL) return cached.v;
  try {
    const imgs = fs.readdirSync(cp)
      .filter(f => /\.(jpg|jpeg|png|webp|gif)$/i.test(f))
      .sort(naturalCompare);
    _imageCache.set(cp, { v: imgs, t: Date.now() });
    return imgs;
  } catch { return []; }
}

function getCoverUrl(mp, name, chapters) {
  if (fs.existsSync(path.join(mp, 'cover.jpg')))
    return `/api/images/${encodeURIComponent(name)}/__cover__/cover.jpg`;
  if (chapters.length > 0) {
    const imgs = getImages(path.join(mp, chapters[0]));
    if (imgs.length > 0)
      return `/api/images/${encodeURIComponent(name)}/${encodeURIComponent(chapters[0])}/${encodeURIComponent(imgs[0])}`;
  }
  return null;
}

function formatDate(date) {
  const diff  = Date.now() - new Date(date).getTime();
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days  = Math.floor(diff / 86400000);
  const weeks = Math.floor(days / 7);
  const months= Math.floor(days / 30);
  if (mins  < 60) return `Hace ${mins} min.`;
  if (hours < 24) return `Hace ${hours} hora${hours > 1 ? 's' : ''}`;
  if (days  < 7)  return `Hace ${days} día${days > 1 ? 's' : ''}`;
  if (weeks < 4)  return `Hace ${weeks} semana${weeks > 1 ? 's' : ''}`;
  return `Hace ${months} mes${months > 1 ? 'es' : ''}`;
}

function getFolderDate(p) {
  try { const s = fs.statSync(p); return s.birthtime && s.birthtime.getFullYear() > 1970 ? s.birthtime : s.ctime; }
  catch { return new Date(0); }
}

function getEffectiveDate(manga, chapter) {
  const root = findMangaRoot(manga);
  return getDetectedDate(manga, chapter) || getFolderDate(
    chapter ? path.join(root, manga, chapter) : path.join(root, manga)
  );
}

function setCacheHeaders(res, seconds = 30) {
  res.setHeader('Cache-Control', `public, max-age=${seconds}, stale-while-revalidate=${seconds * 2}`);
}

// ── RUTAS ─────────────────────────────────────────────────────────────────────

// GET /api/mangas
router.get('/', (req, res) => {
  const roots = getMangaRoots();
  if (!roots.length) return res.status(404).json({ error: 'No se encontraron carpetas de mangas.' });
  try {
    const now = Date.now();
    if (_listCache && (now - _listCacheTime) < CACHE_TTL) {
      const progress = getProgress(req.user?.userId);
      const fresh = _listCache.map(m => ({ ...m, progress: progress[m.name] || {} }));
      const etag  = `"${_listCache.length}-${_listCacheTime}"`;
      if (req.headers['if-none-match'] === etag) return res.status(304).end();
      res.setHeader('ETag', etag);
      setCacheHeaders(res, 60);
      return res.json(fresh);
    }
    const progress = getProgress(req.user?.userId);
    const seen     = new Set();
    const mangas   = [];
    for (const root of roots) {
      fs.readdirSync(root)
        .filter(f => { try { return fs.statSync(path.join(root, f)).isDirectory(); } catch { return false; } })
        .sort()
        .forEach(name => {
          if (seen.has(name)) return;
          seen.add(name);
          const mp       = path.join(root, name);
          const chapters = getChapters(mp);
          const meta     = getMetadata(mp);
          const cover    = getCoverUrl(mp, name, chapters);
          mangas.push({
            name, cover, chapterCount: chapters.length,
            lastChapter:     chapters[chapters.length - 1] || null,
            lastChapterDate: chapters.length ? getEffectiveDate(name, chapters[chapters.length - 1]) : null,
            addedDate:       getEffectiveDate(name, null),
            metadata: {
              type:    meta.type    || 'Manga',
              status:  meta.status  || 'Activo',
              genres:  Array.isArray(meta.genres) ? meta.genres : [],
              synopsis:meta.synopsis || '',
              ranking: meta.ranking  ?? null,
              adult:   meta.adult    || false
            },
            progress: progress[name] || {}
          });
        });
    }
    _listCache     = mangas.map(m => { const { progress: _, ...rest } = m; return rest; });
    _listCacheTime = now;
    const etag     = `"${_listCache.length}-${_listCacheTime}"`;
    res.setHeader('ETag', etag);
    setCacheHeaders(res, 60);
    res.json(mangas);
  } catch(err) { res.status(500).json({ error: err.message }); }
});

// GET /api/mangas/latest-paged
const _latestCache     = new Map();
const LATEST_CACHE_TTL = 60 * 1000;

router.get('/latest-paged', (req, res) => {
  const page      = Math.max(1, parseInt(req.query.page)  || 1);
  const limit     = Math.min(parseInt(req.query.limit) || 20, 50);
  const showAdult = req.query.adult === 'true';
  const cacheKey  = `${page}-${limit}-${showAdult}`;
  const cached    = _latestCache.get(cacheKey);
  if (cached && (Date.now() - cached.t) < LATEST_CACHE_TTL) {
    const progress   = getProgress(req.user?.userId);
    const freshItems = cached.v.items.map(g => ({
      ...g,
      chapters: g.chapters.map(ch => ({
        ...ch,
        read: progress[g.manga]?.readChapters?.includes(ch.chapter) || false
      }))
    }));
    setCacheHeaders(res, 30);
    return res.json({ ...cached.v, items: freshItems });
  }
  try {
    const progress = getProgress(req.user?.userId);
    const roots    = getMangaRoots();
    const seen     = new Set();
    const groups   = [];
    for (const root of roots) {
      const entries = fs.readdirSync(root).filter(f => {
        try { return fs.statSync(path.join(root, f)).isDirectory(); } catch { return false; }
      });
      for (const name of entries) {
        if (seen.has(name)) continue;
        seen.add(name);
        const mp       = path.join(root, name);
        const chapters = getChapters(mp);
        if (!chapters.length) continue;
        const meta = getMetadata(mp);
        if (!showAdult && meta.adult) continue;
        const cover = getCoverUrl(mp, name, chapters);
        const prog  = progress[name] || {};
        const lastChaps = chapters.slice(-2).reverse().map(ch => {
          const d = getEffectiveDate(name, ch);
          return { chapter: ch, date: d, dateLabel: formatDate(d), read: prog.readChapters?.includes(ch) || false };
        });
        groups.push({
          manga: name, cover,
          status:     meta.status || 'Activo',
          adult:      meta.adult  || false,
          type:       meta.type   || 'Manga',
          latestDate: lastChaps[0]?.date || null,
          chapters:   lastChaps
        });
      }
    }
    groups.sort((a, b) => new Date(b.latestDate) - new Date(a.latestDate));
    const total      = groups.length;
    const totalPages = Math.ceil(total / limit);
    const offset     = (page - 1) * limit;
    const items      = groups.slice(offset, offset + limit);
    const result     = { items, total, page, totalPages, perPage: limit };
    _latestCache.set(cacheKey, { v: result, t: Date.now() });
    setCacheHeaders(res, 30);
    res.json(result);
  } catch(err) { res.status(500).json({ error: err.message }); }
});

// ── ESTADÍSTICAS ──────────────────────────────────────────────────────────────

// GET /api/mangas/stats/summary
router.get('/stats/summary', (req, res) => {
  const roots    = getMangaRoots();
  const progress = getProgress(req.user?.userId);
  const seen     = new Set();
  let totalMangas = 0, totalChapters = 0;
  const mangaList = [];

  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    fs.readdirSync(root).forEach(name => {
      if (seen.has(name)) return;
      try { if (!fs.statSync(path.join(root, name)).isDirectory()) return; } catch { return; }
      seen.add(name);
      const mp       = path.join(root, name);
      const chapters = getChapters(mp);
      const meta     = getMetadata(mp);
      const prog     = progress[name] || {};
      const read     = prog.readChapters?.length || 0;
      totalMangas++;
      totalChapters += chapters.length;
      mangaList.push({ name, total: chapters.length, read, completed: read >= chapters.length && chapters.length > 0, type: meta.type || 'Manga', status: meta.status || 'Activo' });
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

  // Detectar formato del archivo importado:
  // - Formato plano viejo: { "NombreManga": { readChapters:[...] } }
  // - Formato nuevo con usuario: { "userId": { "NombreManga": { readChapters:[...] } } }
  // - Formato exportado desde esta app: { exportedAt, progress: { ... } }
  const keys = Object.keys(progress);
  let mangaMap = progress;

  if (keys.length > 0) {
    const firstVal = progress[keys[0]];
    if (firstVal && typeof firstVal === 'object' && !Array.isArray(firstVal)) {
      if (firstVal.readChapters && Array.isArray(firstVal.readChapters)) {
        // Formato plano: clave → objeto con readChapters
        mangaMap = progress;
      } else if (typeof Object.values(firstVal)[0] === 'object') {
        // Formato anidado (userId → mangas) — tomar primer usuario
        mangaMap = firstVal;
      }
    }
  }

  const current  = merge ? getProgress(req.user?.userId) : {};
  const imported = { ...current };
  let count = 0;

  for (const [manga, data] of Object.entries(mangaMap)) {
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
  invalidateCache();
  res.json({ ok: true, imported: count, total: Object.keys(imported).length });
});

// GET /api/mangas/:manga
router.get('/:manga', (req, res) => {
  const name = decodeURIComponent(req.params.manga);
  const root = findMangaRoot(name);
  const mp   = path.join(root, name);
  if (!fs.existsSync(mp)) return res.status(404).json({ error: 'Manga no encontrado.' });

  const now      = Date.now();
  const progress = getProgress(req.user?.userId);

  if (_detailCache.has(name) && (now - _detailCacheTime.get(name)) < CACHE_TTL) {
    const cached = _detailCache.get(name);
    const prog   = progress[name] || {};
    const etag   = `"${name}-${_detailCacheTime.get(name)}"`;
    if (req.headers['if-none-match'] === etag) return res.status(304).end();
    res.setHeader('ETag', etag);
    setCacheHeaders(res, 60);
    return res.json({
      ...cached,
      chapters: cached.chapters.map(ch => ({ ...ch, read: prog.readChapters?.includes(ch.number) || false })),
      progress: prog
    });
  }

  const chapters = getChapters(mp);
  const meta     = getMetadata(mp);
  const cover    = getCoverUrl(mp, name, chapters);
  const prog     = progress[name] || {};

  const data = {
    name, cover, chapterCount: chapters.length,
    metadata: {
      type:    meta.type    || 'Manga',
      status:  meta.status  || 'Activo',
      genres:  Array.isArray(meta.genres) ? meta.genres : [],
      synopsis:meta.synopsis || '',
      ranking: meta.ranking  ?? null,
      adult:   meta.adult    || false
    },
    chapters: chapters.map(ch => {
      const d = getEffectiveDate(name, ch);
      return { number: ch, imageCount: getImages(path.join(mp, ch)).length, read: prog.readChapters?.includes(ch) || false, date: d, dateLabel: formatDate(d) };
    }),
    progress: prog
  };

  const { progress: _, ...toCache } = data;
  _detailCache.set(name, toCache);
  _detailCacheTime.set(name, now);

  const etag = `"${name}-${now}"`;
  res.setHeader('ETag', etag);
  setCacheHeaders(res, 60);
  res.json(data);
});

// GET /api/mangas/:manga/:chapter/images
router.get('/:manga/:chapter/images', (req, res) => {
  const name = decodeURIComponent(req.params.manga);
  const root = findMangaRoot(name);
  const ch   = decodeURIComponent(req.params.chapter);
  const mp   = path.join(root, name);
  const cp   = path.join(mp, ch);
  if (!fs.existsSync(cp)) return res.status(404).json({ error: 'Capítulo no encontrado.' });

  const chapters = getChapters(mp);
  const cover    = getCoverUrl(mp, name, chapters);
  const idx      = chapters.indexOf(ch);
  const images   = getImages(cp).map(img =>
    `/api/images/${encodeURIComponent(name)}/${encodeURIComponent(ch)}/${encodeURIComponent(img)}`
  );

  setCacheHeaders(res, 300);
  res.json({
    manga: name, chapter: ch, cover, images, total: images.length,
    prevChapter: idx > 0 ? chapters[idx - 1] : null,
    nextChapter: idx < chapters.length - 1 ? chapters[idx + 1] : null,
    allChapters: chapters
  });
});

// POST /api/mangas/unread
router.post('/unread', (req, res) => {
  const { manga, chapter } = req.body;
  if (!manga || !chapter) return res.status(400).json({ error: 'Faltan datos.' });
  const progress = getProgress(req.user?.userId);
  if (progress[manga]?.readChapters) {
    progress[manga].readChapters = progress[manga].readChapters.filter(c => c !== chapter);
    saveProgress(req.user?.userId, progress);
    invalidateMangaCache(manga);
  }
  res.json({ ok: true });
});

// POST /api/mangas/mark-all-read
router.post('/mark-all-read', (req, res) => {
  const { manga } = req.body;
  if (!manga) return res.status(400).json({ error: 'Falta el nombre del manga.' });
  const root = findMangaRoot(manga);
  const mp   = path.join(root, manga);
  if (!fs.existsSync(mp)) return res.status(404).json({ error: 'Manga no encontrado.' });
  const chapters = getChapters(mp);
  const progress = getProgress(req.user?.userId);
  if (!progress[manga]) progress[manga] = { readChapters: [] };
  progress[manga].readChapters = [...new Set([...(progress[manga].readChapters || []), ...chapters])];
  if (chapters.length > 0) progress[manga].lastChapter = chapters[chapters.length - 1];
  saveProgress(req.user?.userId, progress);
  invalidateMangaCache(manga);
  res.json({ ok: true });
});

// POST /api/mangas/unread-all
router.post('/unread-all', (req, res) => {
  const { manga } = req.body;
  if (!manga) return res.status(400).json({ error: 'Falta el nombre del manga.' });
  const progress = getProgress(req.user?.userId);
  if (progress[manga]) { progress[manga].readChapters = []; saveProgress(req.user?.userId, progress); invalidateMangaCache(manga); }
  res.json({ ok: true });
});

// POST /api/mangas/progress
router.post('/progress', (req, res) => {
  const { manga, chapter, page } = req.body;
  if (!manga || !chapter) return res.status(400).json({ error: 'Faltan datos.' });
  const progress = getProgress(req.user?.userId);
  if (!progress[manga]) progress[manga] = { readChapters: [] };
  if (!progress[manga].readChapters.includes(chapter)) progress[manga].readChapters.push(chapter);
  progress[manga].lastChapter = chapter;
  progress[manga].lastPage    = page || 0;
  saveProgress(req.user?.userId, progress);
  invalidateMangaCache(manga);
  res.json({ ok: true });
});

// ── METADATA ──────────────────────────────────────────────────────────────────

// GET /api/mangas/:manga/metadata
router.get('/:manga/metadata', (req, res) => {
  const name = decodeURIComponent(req.params.manga);
  const root = findMangaRoot(name);
  const mp   = path.join(root, name);
  if (!fs.existsSync(mp)) return res.status(404).json({ error: 'Manga no encontrado.' });
  res.json(getMetadata(mp));
});

// PUT /api/mangas/:manga/metadata
router.put('/:manga/metadata', (req, res) => {
  if (req.user?.role !== 'admin') return res.status(403).json({ error: 'Solo administradores.' });
  const name = decodeURIComponent(req.params.manga);
  const root = findMangaRoot(name);
  const mp   = path.join(root, name);
  if (!fs.existsSync(mp)) return res.status(404).json({ error: 'Manga no encontrado.' });
  const allowed = ['type','status','genres','synopsis','ranking','adult'];
  const current = getMetadata(mp);
  const updated = { ...current };
  for (const key of allowed) {
    if (req.body[key] !== undefined) updated[key] = req.body[key];
  }
  const file = path.join(mp, 'metadata.json');
  try {
    fs.writeFileSync(file, JSON.stringify(updated, null, 2));
    invalidateMangaCache(name);
    res.json({ ok: true, metadata: updated });
  } catch(e) { res.status(500).json({ error: e.message }); }
});


module.exports = router;
module.exports.startWatcher = startWatcher;
module.exports.stopWatcher  = stopWatcher;
