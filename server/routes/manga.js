const express = require('express');
const fs = require('fs');
const path = require('path');
const router = express.Router();

const PROGRESS_FILE  = path.join(__dirname, '../progress.json');
const DETECTED_FILE  = path.join(__dirname, '../detected_dates.json');

// ── FECHAS DETECTADAS EN TIEMPO REAL ─────────────────────────────────────────
// Formato: { "NombreManga": "ISO", "NombreManga/5": "ISO" }
let detectedDates = {};

function loadDetectedDates() {
  if (!fs.existsSync(DETECTED_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(DETECTED_FILE, 'utf8')); }
  catch { return {}; }
}

function saveDetectedDates() {
  try { fs.writeFileSync(DETECTED_FILE, JSON.stringify(detectedDates, null, 2)); }
  catch (e) { console.error('[Watcher] Error guardando fechas:', e.message); }
}

function getDetectedDate(manga, chapter) {
  const key = chapter ? `${manga}/${chapter}` : manga;
  return detectedDates[key] ? new Date(detectedDates[key]) : null;
}

// ── WATCHER ───────────────────────────────────────────────────────────────────
let watcher = null;

function startWatcher() {
  detectedDates = loadDetectedDates();
  const root = getMangaRoot();

  if (!fs.existsSync(root)) {
    console.warn(`[Watcher] Carpeta no encontrada: ${root}`);
    return;
  }

  // Registrar lo que ya existe para no marcarlo como "nuevo" al arrancar
  const existing = new Set(Object.keys(detectedDates));
  try {
    fs.readdirSync(root).forEach(manga => {
      const mangaPath = path.join(root, manga);
      if (!fs.statSync(mangaPath).isDirectory()) return;
      if (!existing.has(manga)) {
        // Ya existía antes de correr el servidor por primera vez → fecha del sistema
        detectedDates[manga] = getFolderDate(mangaPath).toISOString();
      }
      getChapters(mangaPath).forEach(ch => {
        const key = `${manga}/${ch}`;
        if (!existing.has(key)) {
          detectedDates[key] = getFolderDate(path.join(mangaPath, ch)).toISOString();
        }
      });
    });
    saveDetectedDates();
  } catch (e) { console.error('[Watcher] Error escaneando inicial:', e.message); }

  // Debounce para evitar múltiples eventos por la misma carpeta
  const pending = new Set();
  let debounceTimer = null;

  try {
    watcher = fs.watch(root, { recursive: true }, (event, filename) => {
      if (!filename) return;
      // Solo nos interesan carpetas de manga o capítulos (no imágenes)
      const parts = filename.split(path.sep);
      // parts[0] = nombre manga, parts[1] = capítulo (si existe)
      if (parts.length > 2) return; // más profundo = imagen, ignorar

      const key = parts.join('/');
      if (pending.has(key)) return;
      pending.add(key);

      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        for (const k of pending) {
          if (!detectedDates[k]) {
            detectedDates[k] = new Date().toISOString();
            const label = parts.length === 1
              ? `📚 Nuevo manga: ${parts[0]}`
              : `📖 Nuevo capítulo: ${parts[0]} → cap. ${parts[1]}`;
            console.log(`[Watcher] ${label}`);
          }
        }
        pending.clear();
        saveDetectedDates();
      }, 800);
    });

    console.log(`[Watcher] Monitoreando: ${root}`);
  } catch (e) {
    console.warn('[Watcher] fs.watch no disponible en este sistema:', e.message);
  }
}

function stopWatcher() {
  if (watcher) { watcher.close(); watcher = null; }
}

// ── HELPERS ───────────────────────────────────────────────────────────────────

function getMangaRoot() {
  return path.resolve(process.env.MANGA_PATH || './main');
}

function getProgress() {
  if (!fs.existsSync(PROGRESS_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(PROGRESS_FILE, 'utf8')); }
  catch { return {}; }
}

function saveProgress(data) {
  fs.writeFileSync(PROGRESS_FILE, JSON.stringify(data, null, 2));
}

function getMetadata(mangaPath) {
  const f = path.join(mangaPath, 'metadata.json');
  if (!fs.existsSync(f)) return {};
  try { return JSON.parse(fs.readFileSync(f, 'utf8')); }
  catch { return {}; }
}

function extractNum(str) {
  const m = String(str).match(/(\d+(?:\.\d+)?)/);
  return m ? parseFloat(m[1]) : 0;
}

function getChapters(mangaPath) {
  try {
    return fs.readdirSync(mangaPath)
      .filter(f => {
        try { return fs.statSync(path.join(mangaPath, f)).isDirectory(); }
        catch { return false; }
      })
      .sort((a, b) => extractNum(a) - extractNum(b));
  } catch { return []; }
}

function getImages(chapterPath) {
  try {
    return fs.readdirSync(chapterPath)
      .filter(f => /\.(jpg|jpeg|png|webp|gif)$/i.test(f))
      .sort((a, b) => extractNum(a) - extractNum(b));
  } catch { return []; }
}

function getCoverUrl(mangaPath, mangaName, chapters) {
  if (fs.existsSync(path.join(mangaPath, 'cover.jpg')))
    return `/api/images/${encodeURIComponent(mangaName)}/__cover__/cover.jpg`;
  if (chapters.length > 0) {
    const imgs = getImages(path.join(mangaPath, chapters[0]));
    if (imgs.length > 0)
      return `/api/images/${encodeURIComponent(mangaName)}/${encodeURIComponent(chapters[0])}/${encodeURIComponent(imgs[0])}`;
  }
  return null;
}

function formatDate(date) {
  const diff = Date.now() - new Date(date).getTime();
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days  = Math.floor(diff / 86400000);
  const weeks = Math.floor(days / 7);
  const months = Math.floor(days / 30);
  if (mins < 60)   return `Hace ${mins} min.`;
  if (hours < 24)  return `Hace ${hours} hora${hours > 1 ? 's' : ''}`;
  if (days < 7)    return `Hace ${days} día${days > 1 ? 's' : ''}`;
  if (weeks < 4)   return `Hace ${weeks} semana${weeks > 1 ? 's' : ''}`;
  return `Hace ${months} mes${months > 1 ? 'es' : ''}`;
}

function getFolderDate(folderPath) {
  try {
    const stat = fs.statSync(folderPath);
    return stat.birthtime && stat.birthtime.getFullYear() > 1970
      ? stat.birthtime
      : stat.ctime;
  } catch { return new Date(0); }
}

// Fecha efectiva: usa detected_dates si existe, si no cae a birthtime
function getEffectiveDate(manga, chapter) {
  return getDetectedDate(manga, chapter) || getFolderDate(
    chapter
      ? path.join(getMangaRoot(), manga, chapter)
      : path.join(getMangaRoot(), manga)
  );
}

// ── RUTAS ─────────────────────────────────────────────────────────────────────

// GET /api/mangas
router.get('/', (req, res) => {
  const root = getMangaRoot();
  if (!fs.existsSync(root)) return res.status(404).json({ error: `Carpeta no encontrada: ${root}` });
  try {
    const progress = getProgress();
    const mangas = fs.readdirSync(root)
      .filter(f => { try { return fs.statSync(path.join(root, f)).isDirectory(); } catch { return false; } })
      .sort()
      .map(name => {
        const mangaPath = path.join(root, name);
        const chapters  = getChapters(mangaPath);
        const meta      = getMetadata(mangaPath);
        const cover     = getCoverUrl(mangaPath, name, chapters);
        const mangaProgress = progress[name] || {};
        const addedDate = getEffectiveDate(name, null);
        let lastChapterDate = null;
        if (chapters.length > 0) {
          lastChapterDate = getEffectiveDate(name, chapters[chapters.length - 1]);
        }
        return {
          name, cover,
          chapterCount: chapters.length,
          lastChapter: chapters[chapters.length - 1] || null,
          lastChapterDate,
          addedDate,
          metadata: {
            type: meta.type || 'Manga',
            status: meta.status || 'Activo',
            genres: Array.isArray(meta.genres) ? meta.genres : [],
            synopsis: meta.synopsis || '',
            ranking: meta.ranking ?? null,
            adult: meta.adult || false
          },
          progress: mangaProgress
        };
      });
    res.json(mangas);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// GET /api/mangas/latest
router.get('/latest', (req, res) => {
  const root  = getMangaRoot();
  const limit = parseInt(req.query.limit) || 40;
  try {
    const progress = getProgress();
    const entries  = fs.readdirSync(root)
      .filter(f => { try { return fs.statSync(path.join(root, f)).isDirectory(); } catch { return false; } });
    const all = [];
    for (const name of entries) {
      const mangaPath = path.join(root, name);
      const chapters  = getChapters(mangaPath);
      const meta      = getMetadata(mangaPath);
      const cover     = getCoverUrl(mangaPath, name, chapters);
      const mangaProgress = progress[name] || {};
      const recent = chapters.slice(-2).reverse();
      for (const ch of recent) {
        // Usa la fecha detectada por el watcher si existe
        const chDate = getEffectiveDate(name, ch);
        all.push({
          manga: name, chapter: ch, cover,
          date: chDate, dateLabel: formatDate(chDate),
          read: mangaProgress.readChapters?.includes(ch) || false,
          adult: meta.adult || false,
          type: meta.type || 'Manga',
          status: meta.status || 'Activo'
        });
      }
    }
    all.sort((a, b) => new Date(b.date) - new Date(a.date));
    res.json(all.slice(0, limit));
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// GET /api/mangas/:manga
router.get('/:manga', (req, res) => {
  const root      = getMangaRoot();
  const mangaName = decodeURIComponent(req.params.manga);
  const mangaPath = path.join(root, mangaName);
  if (!fs.existsSync(mangaPath)) return res.status(404).json({ error: 'Manga no encontrado.' });
  const progress  = getProgress();
  const chapters  = getChapters(mangaPath);
  const meta      = getMetadata(mangaPath);
  const cover     = getCoverUrl(mangaPath, mangaName, chapters);
  const mangaProgress = progress[mangaName] || {};
  res.json({
    name: mangaName, cover, chapterCount: chapters.length,
    metadata: {
      type: meta.type || 'Manga', status: meta.status || 'Activo',
      genres: Array.isArray(meta.genres) ? meta.genres : [],
      synopsis: meta.synopsis || '', ranking: meta.ranking ?? null, adult: meta.adult || false
    },
    chapters: chapters.map(ch => {
      const chDate = getEffectiveDate(mangaName, ch);
      return {
        number: ch,
        imageCount: getImages(path.join(mangaPath, ch)).length,
        read: mangaProgress.readChapters?.includes(ch) || false,
        date: chDate,
        dateLabel: formatDate(chDate)
      };
    }),
    progress: mangaProgress
  });
});

// GET /api/mangas/:manga/:chapter/images
router.get('/:manga/:chapter/images', (req, res) => {
  const root      = getMangaRoot();
  const mangaName = decodeURIComponent(req.params.manga);
  const chapter   = decodeURIComponent(req.params.chapter);
  const mangaPath = path.join(root, mangaName);
  const chapterPath = path.join(mangaPath, chapter);
  if (!fs.existsSync(chapterPath)) return res.status(404).json({ error: 'Capítulo no encontrado.' });
  const chapters = getChapters(mangaPath);
  const cover    = getCoverUrl(mangaPath, mangaName, chapters);
  const currentIndex = chapters.indexOf(chapter);
  const images = getImages(chapterPath).map(img =>
    `/api/images/${encodeURIComponent(mangaName)}/${encodeURIComponent(chapter)}/${encodeURIComponent(img)}`
  );
  res.json({
    manga: mangaName, chapter, cover, images, total: images.length,
    prevChapter: currentIndex > 0 ? chapters[currentIndex - 1] : null,
    nextChapter: currentIndex < chapters.length - 1 ? chapters[currentIndex + 1] : null,
    allChapters: chapters
  });
});

// POST /api/mangas/progress
router.post('/progress', (req, res) => {
  const { manga, chapter, page } = req.body;
  if (!manga || !chapter) return res.status(400).json({ error: 'Faltan datos.' });
  const progress = getProgress();
  if (!progress[manga]) progress[manga] = { readChapters: [] };
  if (!progress[manga].readChapters.includes(chapter)) progress[manga].readChapters.push(chapter);
  progress[manga].lastChapter = chapter;
  progress[manga].lastPage    = page || 0;
  saveProgress(progress);
  res.json({ ok: true });
});

// Exportar también startWatcher y stopWatcher para usarlos desde index.js
module.exports = router;
module.exports.startWatcher = startWatcher;
module.exports.stopWatcher  = stopWatcher;
