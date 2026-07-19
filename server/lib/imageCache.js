// ── CACHÉ LRU DE IMÁGENES EN RAM ──────────────────────────────────────────────
// Dos objetivos:
// 1. Evitar releer del disco externo una imagen que ya se sirvió hace poco
//    (re-lecturas del mismo capítulo, varios dispositivos mirando lo mismo).
// 2. "Precalentar" las primeras páginas de un capítulo apenas el cliente pide
//    la lista de imágenes (mientras todavía está en la pantalla de carga),
//    para que cuando llegue el pedido real de esa imagen ya esté en memoria.
//
// Tamaño acotado por MAX_BYTES (configurable vía IMAGE_CACHE_MB) con
// desalojo del más viejo — un Map de JS preserva orden de inserción, así que
// alcanza con "re-insertar" en cada hit para moverlo al final.

const fs = require('fs');

const MAX_BYTES = (parseInt(process.env.IMAGE_CACHE_MB) || 200) * 1024 * 1024;

// El scraper escribe cada imagen directo a su nombre final (sin archivo
// temporal + rename), y en el "modo recuperación" puede incluso reescribirla
// o borrarla si falla la verificación de integridad justo después de
// guardarla. Si el server llega a leer/cachear una imagen en ese instante
// exacto, quedaría una versión a medio escribir pegada en RAM hasta el
// próximo reinicio (a diferencia de una lectura normal del disco, que se
// autocorrige sola en el siguiente pedido). Por eso: nunca se cachea un
// archivo que se haya tocado hace menos de STABLE_AFTER_MS — se sirve igual
// (como siempre), simplemente no se guarda en memoria hasta que se vea
// estable.
const STABLE_AFTER_MS = 3000;

function isStable(absolutePath) {
  try {
    const st = fs.statSync(absolutePath);
    return (Date.now() - st.mtimeMs) > STABLE_AFTER_MS;
  } catch { return false; }
}

let _cache = new Map(); // absolutePath -> Buffer
let _bytes = 0;

function _evictIfNeeded() {
  while (_bytes > MAX_BYTES && _cache.size > 0) {
    const oldestKey = _cache.keys().next().value;
    const buf = _cache.get(oldestKey);
    _bytes -= buf.length;
    _cache.delete(oldestKey);
  }
}

function get(absolutePath) {
  const buf = _cache.get(absolutePath);
  if (!buf) return null;
  // mover al final (más reciente)
  _cache.delete(absolutePath);
  _cache.set(absolutePath, buf);
  return buf;
}

function set(absolutePath, buf) {
  if (buf.length > MAX_BYTES) return; // una imagen sola no debería superar el caché entero
  if (_cache.has(absolutePath)) _bytes -= _cache.get(absolutePath).length;
  _cache.set(absolutePath, buf);
  _bytes += buf.length;
  _evictIfNeeded();
}

// Lee del disco (async, sin bloquear el event loop) y guarda en caché.
// No hace nada si ya está cacheada o si falla la lectura (el request real
// que llegue después la sirve directo del disco como siempre).
async function warm(absolutePath) {
  if (_cache.has(absolutePath)) return;
  if (!isStable(absolutePath)) return; // podría estar a medio escribir — no arriesgar
  try {
    const buf = await fs.promises.readFile(absolutePath);
    set(absolutePath, buf);
  } catch { /* no pasa nada, se sirve del disco cuando llegue el pedido real */ }
}

// Precalienta las primeras N imágenes de un capítulo en paralelo, en
// background — se llama sin await desde la ruta que devuelve la lista de
// imágenes, así arranca mientras el cliente todavía está armando el pedido
// de la primera página.
function warmChapter(absolutePaths, count = 3) {
  absolutePaths.slice(0, count).forEach(p => { warm(p); });
}

function clear(absolutePath) {
  if (absolutePath) {
    const buf = _cache.get(absolutePath);
    if (buf) { _bytes -= buf.length; _cache.delete(absolutePath); }
    return;
  }
  _cache.clear();
  _bytes = 0;
}

module.exports = { get, set, warm, warmChapter, clear, isStable, get maxBytes() { return MAX_BYTES; } };
