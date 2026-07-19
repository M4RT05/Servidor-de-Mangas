// ── ÍNDICE DE CATÁLOGO ────────────────────────────────────────────────────────
// Antes: /api/mangas, /latest-paged y /:manga volvían a leer TODAS las carpetas
// de manga (readdir + stat por capítulo + metadata.json) en cada cache miss,
// sin importar que solo hubiera cambiado un capítulo de un manga. Con un disco
// externo y una librería que sigue creciendo, eso escala con el tamaño total
// del catálogo en vez de con lo que realmente cambió.
//
// Ahora: este módulo mantiene un índice en memoria (name → entry completa:
// capítulos, imágenes por capítulo, portada, metadata, fechas). Se construye
// una vez al arrancar y de ahí en adelante el watcher actualiza SOLO el manga
// que cambió — nunca vuelve a tocar disco por los demás. Las rutas de manga.js
// leen de este índice, no del filesystem.
//
// Se guarda además un snapshot en disco (catalog_index.json) para que un
// reinicio del servidor no tenga que rehacer el escaneo profundo de cada
// manga: por cada carpeta se compara el mtime contra el snapshot y solo se
// reconstruyen las que cambiaron mientras el servidor estaba apagado.

const fs   = require('fs');
const path = require('path');
const {
  writeJsonAtomic, naturalCompare, listDirNames, listImageNames,
  getFolderDate, getDirMtimeMs, getMetadataSync, IMAGE_EXT_RE
} = require('../lib/fsHelpers');

const DETECTED_FILE = path.join(__dirname, '../detected_dates.json');
const SNAPSHOT_FILE = path.join(__dirname, '../catalog_index.json');

// ── RAÍCES CONFIGURADAS ───────────────────────────────────────────────────────
function getConfiguredMangaRoots() {
  const candidates = [path.resolve(process.env.MANGA_PATH || './main')];
  for (let i = 2; process.env[`MANGA_PATH_${i}`]; i++) {
    candidates.push(path.resolve(process.env[`MANGA_PATH_${i}`]));
  }
  return candidates;
}
function getMangaRoots() {
  const candidates = getConfiguredMangaRoots();
  const existing = candidates.filter(r => { try { return fs.existsSync(r); } catch { return false; } });
  return existing.length > 0 ? existing : candidates.slice(0, 1);
}
function getMangaRoot() { return getMangaRoots()[0]; }

// ── ESTADO ────────────────────────────────────────────────────────────────────
let _index       = new Map();   // name -> entry
let _rootOf       = new Map();  // name -> root path donde vive
let _version      = 0;          // se incrementa en cada cambio, para ETags baratos
let detectedDates = {};

function bumpVersion() { _version++; }
function getIndexVersion() { return _version; }

// ── FECHAS DETECTADAS (cuándo se vio por primera vez cada manga/capítulo) ────
function loadDetectedDates() {
  if (!fs.existsSync(DETECTED_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(DETECTED_FILE, 'utf8')); } catch { return {}; }
}
let _saveDatesTimer = null;
function saveDetectedDatesDebounced() {
  clearTimeout(_saveDatesTimer);
  _saveDatesTimer = setTimeout(() => {
    try { writeJsonAtomic(DETECTED_FILE, detectedDates); }
    catch(e) { console.error('[Catalog] Error guardando fechas:', e.message); }
  }, 500);
}
function getDetectedDate(manga, chapter) {
  const key = chapter ? `${manga}/${chapter}` : manga;
  return detectedDates[key] ? new Date(detectedDates[key]) : null;
}
function getEffectiveDate(manga, chapter, mangaPath) {
  return getDetectedDate(manga, chapter) || getFolderDate(
    chapter ? path.join(mangaPath, chapter) : mangaPath
  );
}

// ── SNAPSHOT EN DISCO (arranque rápido) ───────────────────────────────────────
function loadSnapshot() {
  if (!fs.existsSync(SNAPSHOT_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(SNAPSHOT_FILE, 'utf8')); } catch { return {}; }
}
let _saveSnapshotTimer = null;
function saveSnapshotDebounced() {
  clearTimeout(_saveSnapshotTimer);
  _saveSnapshotTimer = setTimeout(() => {
    try {
      const obj = {};
      for (const [name, entry] of _index) obj[name] = entry;
      writeJsonAtomic(SNAPSHOT_FILE, obj);
    } catch(e) { console.error('[Catalog] Error guardando snapshot:', e.message); }
  }, 1500);
}

// ── CONSTRUCCIÓN DE UNA ENTRADA (el único punto que toca el disco externo) ───
function getCoverUrlFor(mp, name, chapterEntries) {
  if (fs.existsSync(path.join(mp, 'cover.jpg')))
    return `/api/images/${encodeURIComponent(name)}/__cover__/cover.jpg`;
  if (chapterEntries.length > 0 && chapterEntries[0].images.length > 0)
    return `/api/images/${encodeURIComponent(name)}/${encodeURIComponent(chapterEntries[0].number)}/${encodeURIComponent(chapterEntries[0].images[0])}`;
  return null;
}

function buildMangaEntry(root, name) {
  const mp = path.join(root, name);
  const chapterNames = listDirNames(mp);

  // Fijar la fecha de "primera vez visto" para el manga y cada capítulo antes
  // de calcular nada — así, si esta entrada se reconstruye después (por el
  // watcher, sin importar cuántas veces), la fecha queda pisada una sola vez
  // y no depende en vivo del ctime de la carpeta (que puede cambiar si algo
  // vuelve a tocar el archivo más adelante, por ejemplo un reintento del
  // scraper). Antes esto solo se hacía en el escaneo de arranque; un
  // capítulo agregado con el server corriendo nunca quedaba fijado.
  if (!detectedDates[name]) detectedDates[name] = getFolderDate(mp).toISOString();
  for (const ch of chapterNames) {
    const k = `${name}/${ch}`;
    if (!detectedDates[k]) detectedDates[k] = getFolderDate(path.join(mp, ch)).toISOString();
  }

  const chapters = chapterNames.map(ch => ({
    number: ch,
    images: listImageNames(path.join(mp, ch)),
    date:   getEffectiveDate(name, ch, mp).toISOString()
  }));
  const meta = getMetadataSync(mp);
  const entry = {
    name,
    cover: getCoverUrlFor(mp, name, chapters),
    chapters,
    metadata: {
      type:    meta.type    || 'Manga',
      status:  meta.status  || 'Activo',
      genres:  Array.isArray(meta.genres) ? meta.genres : [],
      synopsis:meta.synopsis || '',
      ranking: meta.ranking  ?? null,
      adult:   meta.adult    || false
    },
    addedDate:       getEffectiveDate(name, null, mp).toISOString(),
    lastChapterDate: chapters.length ? chapters[chapters.length - 1].date : null,
    folderMtimeMs:   getDirMtimeMs(mp)
  };
  return entry;
}

// Reconstruye SOLO el manga indicado (o lo agrega si es nuevo). Es la única
// operación que dispara el watcher — nunca se re-escanean los demás.
function rebuildMangaEntry(name) {
  for (const root of getMangaRoots()) {
    const mp = path.join(root, name);
    if (fs.existsSync(mp)) {
      _index.set(name, buildMangaEntry(root, name));
      _rootOf.set(name, root);
      bumpVersion();
      saveDetectedDatesDebounced();
      saveSnapshotDebounced();
      return _index.get(name);
    }
  }
  // Ya no existe en ninguna raíz configurada: sacarlo del índice.
  if (_index.has(name)) {
    _index.delete(name);
    _rootOf.delete(name);
    bumpVersion();
    saveSnapshotDebounced();
  }
  return null;
}

function getMangaEntry(name) { return _index.get(name) || null; }
function getAllEntries()     { return Array.from(_index.values()); }
function findMangaRoot(name) { return _rootOf.get(name) || getMangaRoot(); }
function getMangaNames()     { return Array.from(_index.keys()); }

// El watcher (fs.watch) no garantiza captar el 100% de los eventos, sobre
// todo en un disco externo — si se pierde el evento de un capítulo agregado
// mientras el server corría, esa entrada del índice queda desactualizada
// hasta que otra cosa la toque. Para las rutas de UN manga específico (abrir
// su ficha, abrir un capítulo, marcar todo como leído) — no para los listados
// completos, ahí sí importa evitar tocar disco — conviene pagar el costo de
// UN solo stat() para confirmar que el índice sigue reflejando la realidad,
// y recién si no coincide, reconstruir esa única entrada. Esto es justo lo
// que hacían las rutas originales (leer directo del disco en cada pedido) —
// acá se recupera esa garantía de forma barata, sin volver a escanear todo
// el catálogo en cada request.
function ensureFresh(name) {
  const entry = _index.get(name);
  const root  = entry ? _rootOf.get(name) : null;
  if (entry && root) {
    const mp = path.join(root, name);
    if (getDirMtimeMs(mp) === entry.folderMtimeMs) return entry; // sin cambios, no hace falta reconstruir
  }
  return rebuildMangaEntry(name);
}

// ── ARRANQUE: cargar snapshot + reconciliar contra disco ─────────────────────
// Para cada manga presente en disco: si el snapshot lo tiene con el mismo
// mtime de carpeta, se reusa tal cual (sin reabrir ningún capítulo). Si el
// mtime cambió (se agregó/quitó un capítulo mientras el server estaba
// apagado) o el manga es nuevo, se reconstruye desde cero. Esto evita repetir
// el escaneo profundo completo en cada reinicio sin arriesgar datos viejos.
function start() {
  detectedDates = loadDetectedDates();
  const snapshot = loadSnapshot();

  const configuredRoots = getConfiguredMangaRoots();
  const roots = configuredRoots.filter(r => { try { return fs.existsSync(r); } catch { return false; } });
  if (!roots.length) { console.warn('[Catalog] No se encontraron carpetas de mangas.'); return { validMangas: [], scanComplete: false }; }

  let scanComplete = roots.length === configuredRoots.length;
  if (!scanComplete) {
    const missing = configuredRoots.filter(r => !roots.includes(r));
    console.warn(`[Catalog] Ruta(s) configurada(s) no disponible(s) ahora mismo: ${missing.join(', ')}. La limpieza de historial se omite este arranque.`);
  }

  const seen = new Set();
  let reused = 0, rebuilt = 0;

  for (const root of roots) {
    let names;
    try { names = listDirNames(root); }
    catch(e) { console.error(`[Catalog] Error escaneando ${root}:`, e.message); scanComplete = false; continue; }

    for (const name of names) {
      if (seen.has(name)) continue;
      seen.add(name);
      const mp = path.join(root, name);
      const currentMtime = getDirMtimeMs(mp);
      const cached = snapshot[name];
      if (cached && cached.folderMtimeMs === currentMtime) {
        _index.set(name, cached);
        _rootOf.set(name, root);
        reused++;
      } else {
        _index.set(name, buildMangaEntry(root, name));
        _rootOf.set(name, root);
        rebuilt++;
      }
      // Registrar fechas de capítulos nuevos que no estuvieran ya trackeados
      if (!detectedDates[name]) detectedDates[name] = getFolderDate(mp).toISOString();
      for (const ch of _index.get(name).chapters) {
        const k = `${name}/${ch.number}`;
        if (!detectedDates[k]) detectedDates[k] = ch.date;
      }
    }
  }

  console.log(`[Catalog] Índice listo: ${reused} manga(s) reusados del snapshot, ${rebuilt} reconstruidos desde disco.`);
  saveDetectedDatesDebounced();
  saveSnapshotDebounced();
  bumpVersion();

  return { validMangas: Array.from(seen), scanComplete };
}

// ── WATCHER: actualiza incrementalmente ───────────────────────────────────────
// chokidar en vez de fs.watch nativo — fs.watch con recursive:true no está
// soportado en Linux, y en Windows tiene historial de perder eventos. chokidar
// normaliza esas diferencias entre plataformas.
//
// depth:2 significa que también se escuchan cambios DENTRO de una carpeta de
// capítulo (no solo la creación de la carpeta en sí) — ver el comentario largo
// más abajo sobre por qué esto importa. El límite en profundidad evita que
// chokidar tenga que abrir un watch handle por cada imagen suelta del catálogo,
// que con una biblioteca grande podría acercarse a límites del sistema
// operativo (inotify en Linux, por ejemplo).
//
// require() protegido a propósito: si chokidar no está instalado (falta un
// "npm install", por ejemplo) esto NO debe tirar abajo todo el servidor — que
// el watcher en vivo no funcione es mucho mejor que el servidor entero no
// arranque. El rescan periódico (más abajo) sigue funcionando igual y termina
// reflejando los cambios tarde o temprano, aunque sea con más demora.
let chokidar = null;
try { chokidar = require('chokidar'); }
catch (e) { console.warn('\n  ⚠️  "chokidar" no está instalado — el watcher en vivo no va a funcionar (correr "npm install"). El catálogo se sigue actualizando cada 5 min por el rescan periódico.\n'); }

const RESCAN_INTERVAL_MS = 5 * 60 * 1000; // 5 minutos
let watchers = [];
let _periodicRescanTimer = null;
const _debounceTimers = new Map(); // manga -> timeout handle (uno por manga, no uno global)

function scheduleRebuild(manga) {
  clearTimeout(_debounceTimers.get(manga));
  _debounceTimers.set(manga, setTimeout(() => {
    _debounceTimers.delete(manga);
    const existed = _index.has(manga);
    rebuildMangaEntry(manga);
    console.log(`[Watcher] ${existed ? 'Actualizado' : 'Nuevo manga'}: ${manga}`);
    saveDetectedDatesDebounced();
  }, 800));
}

// Red de seguridad ante fallos silenciosos del watcher — el caso típico acá
// es un HDD USB que se desconecta y reconecta: chokidar (como cualquier
// watcher basado en eventos del SO) no tiene garantía de "reengancharse"
// solo en ese escenario. Cada RESCAN_INTERVAL_MS se recorren las carpetas de
// nivel superior de cada raíz y se compara el mtime contra el índice — el
// mismo chequeo barato que ya hace ensureFresh() para un manga puntual, acá
// corrido periódicamente para todo el catálogo. Es liviano: un stat() por
// manga, nada de leer capítulos ni imágenes salvo que el mtime cambió.
function periodicRescan() {
  const seenNow = new Set();
  for (const root of getMangaRoots()) {
    if (!fs.existsSync(root)) continue;
    let names;
    try { names = listDirNames(root); }
    catch(e) { console.warn(`[Watcher] Rescan: error leyendo ${root}:`, e.message); continue; }
    for (const name of names) {
      seenNow.add(name);
      const entry = _index.get(name);
      if (!entry) { rebuildMangaEntry(name); console.log(`[Watcher] Rescan detectó manga nuevo: ${name}`); continue; }
      const mp = path.join(root, name);
      if (getDirMtimeMs(mp) !== entry.folderMtimeMs) {
        rebuildMangaEntry(name);
        console.log(`[Watcher] Rescan detectó un cambio que el watcher no había capturado: ${name}`);
      }
    }
  }
  // Mangas que estaban en el índice pero ya no aparecen en ninguna raíz -> se borraron
  for (const name of _index.keys()) {
    if (!seenNow.has(name)) {
      rebuildMangaEntry(name); // internamente detecta que no existe más y lo saca del índice
      console.log(`[Watcher] Rescan detectó un manga eliminado: ${name}`);
    }
  }
  saveDetectedDatesDebounced();
}

// Vigila de cerca UNA carpeta de capítulo puntual mientras se está
// descargando (scraper.py crea la carpeta vacía antes de empezar a bajar
// las imágenes — ver el hallazgo original de este cambio), y se cierra sola
// a los pocos minutos. El costo de "mirar adentro de un capítulo" queda
// proporcional a cuántos capítulos se están bajando AHORA MISMO (típicamente
// 1, tal vez unos pocos en paralelo si el scraper procesa varias fuentes a
// la vez) — no a los miles de capítulos que ya están completos y quietos.
const CHAPTER_WATCH_TIMEOUT_MS = 3 * 60 * 1000; // 3 min: de sobra para bajar un capítulo entero
const _chapterWatchers = new Map(); // ruta absoluta de la carpeta de capítulo -> FSWatcher temporal

function watchChapterFolderTemporarily(manga, chapterPath) {
  if (!chokidar || _chapterWatchers.has(chapterPath)) return;
  try {
    const cw = chokidar.watch(chapterPath, {
      ignoreInitial: true,
      depth: 0,
      awaitWriteFinish: { stabilityThreshold: 300, pollInterval: 100 }
    });
    cw.on('all', () => scheduleRebuild(manga));
    cw.on('error', () => {}); // la carpeta puede desaparecer sola si el capítulo se borra/renombra — no es un error real
    const timer = setTimeout(() => {
      cw.close();
      _chapterWatchers.delete(chapterPath);
    }, CHAPTER_WATCH_TIMEOUT_MS);
    _chapterWatchers.set(chapterPath, { cw, timer });
  } catch (e) { console.warn(`[Watcher] No se pudo vigilar de cerca ${chapterPath}:`, e.message); }
}

function startWatcher(onScanDone) {
  const { validMangas, scanComplete } = start();
  if (typeof onScanDone === 'function') onScanDone(validMangas, scanComplete);

  const roots = getMangaRoots();
  const usePolling = process.env.MANGA_WATCH_POLLING === 'true';

  if (!chokidar) {
    // Ya se avisó una vez al fallar el require() más arriba — acá directo
    // saltamos al rescan periódico, que es el único mecanismo de
    // actualización disponible sin chokidar (con más demora: hasta 5 min en
    // vez de ~1 segundo, pero el catálogo no se queda desactualizado del
    // todo ni el servidor deja de arrancar).
    clearInterval(_periodicRescanTimer);
    _periodicRescanTimer = setInterval(periodicRescan, RESCAN_INTERVAL_MS);
    return;
  }

  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    try {
      const w = chokidar.watch(root, {
        ignoreInitial: true, // el escaneo inicial ya lo hizo start(), no repetirlo
        depth: 1,             // root -> manga -> capítulo (creación/borrado de CARPETAS).
                               // A propósito NO desciende a mirar el contenido de cada
                               // capítulo uno por uno — con una biblioteca de cientos de
                               // manga y miles de capítulos, eso significaría montar un
                               // watch handle por cada carpeta de capítulo de TODA la
                               // biblioteca desde el arranque, lo cual en una biblioteca
                               // grande (probado con ~200 manga / ~4000 carpetas de
                               // capítulo en 3 raíces) hace que el servidor tarde varios
                               // segundos en volverse realmente responsivo — compite con
                               // las requests HTTP reales por turno en el event loop.
                               // En vez de eso, ver watchChapterFolderTemporarily() más
                               // abajo: solo se vigila de cerca una carpeta de capítulo
                               // mientras se está descargando, no las miles que ya están
                               // completas y quietas en el disco.
        awaitWriteFinish: { stabilityThreshold: 300, pollInterval: 100 },
        // Por si el HDD USB no dispara eventos nativos de forma confiable
        // (más común en discos de red que en USB, pero queda como escape
        // hatch sin tener que tocar código): MANGA_WATCH_POLLING=true en .env
        usePolling
      });
      w.on('all', (event, changedPath) => {
        const rel   = path.relative(root, changedPath);
        const parts = rel.split(path.sep);
        // manga/          -> 1 parte
        // manga/capitulo/ -> 2 partes (creación/borrado de la carpeta en sí)
        if (!parts[0] || parts.length > 2) return;
        scheduleRebuild(parts[0]);
        // Carpeta de capítulo recién creada -> vigilarla de cerca un rato,
        // ver el comentario largo en watchChapterFolderTemporarily().
        if (parts.length === 2 && (event === 'addDir')) {
          watchChapterFolderTemporarily(parts[0], changedPath);
        }
      });
      w.on('error', (err) => console.warn(`[Watcher] Error en ${root}:`, err.message));
      watchers.push(w);
      console.log(`[Watcher] Monitoreando (chokidar${usePolling ? ', polling' : ''}): ${root}`);
    } catch(e) { console.warn(`[Watcher] No se pudo iniciar en ${root}:`, e.message); }
  }

  clearInterval(_periodicRescanTimer);
  _periodicRescanTimer = setInterval(periodicRescan, RESCAN_INTERVAL_MS);
}
function stopWatcher() {
  watchers.forEach(w => w.close());
  watchers = [];
  for (const { cw, timer } of _chapterWatchers.values()) { clearTimeout(timer); cw.close(); }
  _chapterWatchers.clear();
  for (const t of _debounceTimers.values()) clearTimeout(t);
  _debounceTimers.clear();
  clearInterval(_periodicRescanTimer);
  _periodicRescanTimer = null;
}

// Usado por la ruta PUT de metadata: tras escribir metadata.json a mano, se
// reconstruye esa única entrada en vez de esperar al debounce del watcher.
function touchManga(name) { return rebuildMangaEntry(name); }

module.exports = {
  getConfiguredMangaRoots, getMangaRoots, getMangaRoot, findMangaRoot,
  getMangaEntry, getAllEntries, getMangaNames, getIndexVersion, ensureFresh,
  getDetectedDate, startWatcher, stopWatcher, touchManga,
  // expuesto para runProgressCleanup / stats en manga.js
  get detectedDatesRef() { return detectedDates; },
  saveDetectedDatesDebounced
};
