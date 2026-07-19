// ── MINIATURAS DE PORTADA ─────────────────────────────────────────────────────
// Las tarjetas de las grillas (home, búsqueda, últimos capítulos, "continuar
// leyendo") muestran la portada a 150-250px de ancho, pero se servía la
// imagen completa tal cual la dejó el scraper — a veces varios MB en un
// celular por WiFi/4G. Esto genera y cachea en disco una versión reducida en
// WebP, una sola vez por portada; de ahí en más se sirve directo del caché.
//
// IMPORTANTE: esto es solo para portadas. Las páginas de los capítulos NUNCA
// pasan por acá — ahí sigue rigiendo la política de cero pérdida de calidad
// (ver imageCache.js / la ruta de imágenes en index.js, que solo llama a
// getThumbnail() cuando chapter === '__cover__').
const fs     = require('fs');
const path   = require('path');
const crypto = require('crypto');

let sharp = null;
let sharpLoadAttempted = false;
// Carga perezosa: sharp es un módulo nativo (binarios por plataforma). Si por
// lo que sea no está instalado o falla en algún entorno puntual, el server
// no se tiene que caer por esto — se loguea una vez y getThumbnail() devuelve
// null de ahí en más, cayendo siempre al fallback de servir la portada
// completa (ver el llamador en index.js).
function loadSharp() {
  if (sharpLoadAttempted) return sharp;
  sharpLoadAttempted = true;
  try { sharp = require('sharp'); }
  catch (e) {
    console.warn('[Thumbnails] "sharp" no está instalado — se sirven portadas completas sin miniatura. (' + e.message + ')');
    sharp = null;
  }
  return sharp;
}

const CACHE_DIR    = path.join(__dirname, '../data/thumb_cache');
// 600px: a 400px se veía nítido en pantallas normales pero se notaba borroso/
// "opaco" en pantallas retina/HiDPI, porque el navegador tiene que agrandar
// la miniatura para llenar una tarjeta de hasta ~280px CSS en una pantalla
// 2x — eso necesita ~560px de píxeles reales, no 400. 600px da margen para
// eso sin perder casi nada del ahorro de peso (sigue siendo ~85% más chica
// que la portada original, contra ~96% con 400px).
const THUMB_WIDTH  = 600;
const THUMB_QUALITY = 80;

function ensureCacheDir() {
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
}

// El nombre de archivo cacheado incluye el mtime del original Y la config de
// la miniatura (ancho/calidad): si la portada cambia, o si el día de mañana
// se ajusta THUMB_WIDTH/THUMB_QUALITY, el hash cambia solo y se genera una
// miniatura nueva con la config vigente — nunca se sigue sirviendo una
// miniatura vieja generada con parámetros distintos a los actuales.
function cacheKeyFor(sourcePath, mtimeMs) {
  const h = crypto.createHash('sha1')
    .update(`${sourcePath}:${mtimeMs}:${THUMB_WIDTH}:${THUMB_QUALITY}`)
    .digest('hex').slice(0, 24);
  return path.join(CACHE_DIR, `${h}.webp`);
}

// Devuelve el Buffer de la miniatura WebP (generándola y cacheándola en disco
// si hace falta), o null si no se pudo — nunca lanza. Quien llama SIEMPRE
// debe tratar null como "seguir con el flujo normal de servir la imagen
// completa", nunca como un error que corta la respuesta.
async function getThumbnail(sourcePath) {
  if (!loadSharp()) return null;

  let mtimeMs;
  try { mtimeMs = fs.statSync(sourcePath).mtimeMs; } catch { return null; }

  const cachePath = cacheKeyFor(sourcePath, mtimeMs);
  try { return await fs.promises.readFile(cachePath); } catch { /* todavía no cacheada, generarla abajo */ }

  try {
    ensureCacheDir();
    const buf = await sharp(sourcePath)
      .resize({ width: THUMB_WIDTH, withoutEnlargement: true })
      // smartSubsample:true = sin submuestreo de color (4:4:4). Sin esto,
      // WebP resigna resolución de color por defecto (4:2:0, igual que
      // JPEG) — se nota poco en fotos comunes, pero en colores muy
      // saturados y con bordes marcados (típico de portadas de manga/anime)
      // puede verse como sangrado/manchado de color en los bordes. Cuesta
      // poco peso extra (~15-20%) y lo evita.
      .webp({ quality: THUMB_QUALITY, smartSubsample: true })
      .toBuffer();
    // Escritura atómica (tmp + rename) — si dos requests piden la misma
    // miniatura nueva al mismo tiempo, no se pisan ni queda un .webp a medio
    // escribir si el proceso se corta en el medio.
    const tmp = `${cachePath}.tmp${process.pid}`;
    await fs.promises.writeFile(tmp, buf);
    await fs.promises.rename(tmp, cachePath);
    return buf;
  } catch (e) {
    console.warn(`[Thumbnails] No se pudo generar miniatura de ${sourcePath}:`, e.message);
    return null;
  }
}

module.exports = { getThumbnail, THUMB_WIDTH };
