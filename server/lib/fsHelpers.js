const fs = require('fs');

// ── ESCRITURA ATÓMICA (evita archivos corruptos/truncados si el proceso
// muere a mitad de una escritura: se escribe a un .tmp y se renombra) ────────
function writeJsonAtomic(filePath, data) {
  const tmp = filePath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data));
  fs.renameSync(tmp, filePath);
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

const IMAGE_EXT_RE = /\.(jpg|jpeg|png|webp|gif)$/i;

// Extensiones válidas para portadas (cover.*) — mismo criterio en todos lados
// que necesitan detectar o validar el archivo de portada: catalogIndex.js
// (detección al construir el índice) e index.js (validación al servir).
// Definidas una sola vez acá para que ningún lado quede desincronizado.
const COVER_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp'];
const COVER_FILENAME_RE = /^cover\.(jpe?g|png|webp)$/i;

// Lista subcarpetas (capítulos) de un directorio. Usa withFileTypes: readdirSync
// devuelve el tipo de cada entrada en la misma llamada, así que evita el
// statSync extra por archivo que tenía la versión anterior (una sola syscall
// en vez de N+1 — importante en discos externos con latencia de acceso real).
function listDirNames(dirPath) {
  try {
    return fs.readdirSync(dirPath, { withFileTypes: true })
      .filter(d => d.isDirectory())
      .map(d => d.name)
      .sort(naturalCompare);
  } catch { return []; }
}

// Lista imágenes de una carpeta de capítulo, mismo criterio withFileTypes.
function listImageNames(dirPath) {
  try {
    return fs.readdirSync(dirPath, { withFileTypes: true })
      .filter(d => d.isFile() && IMAGE_EXT_RE.test(d.name))
      .map(d => d.name)
      .sort(naturalCompare);
  } catch { return []; }
}

function getFolderDate(p) {
  try { const s = fs.statSync(p); return s.birthtime && s.birthtime.getFullYear() > 1970 ? s.birthtime : s.ctime; }
  catch { return new Date(0); }
}

// mtime de la carpeta contenedora — se usa como "huella" barata para saber si
// una carpeta de manga cambió (se agregó/borró un capítulo) sin tener que
// volver a listar todo su contenido. En NTFS y ext4, crear o borrar una
// subcarpeta actualiza el mtime del padre.
function getDirMtimeMs(p) {
  try { return fs.statSync(p).mtimeMs; } catch { return 0; }
}

function getMetadataSync(mangaFolderPath) {
  const f = require('path').join(mangaFolderPath, 'metadata.json');
  if (!fs.existsSync(f)) return {};
  try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return {}; }
}

// Igual criterio que getMetadataSync, para el registro de progreso que
// escribe el scraper (registro_progreso.json, en la misma carpeta del manga
// — ver §5c en scraper.py). Se usa para calcular de qué fuente(s) viene cada
// manga (server/lib/sources.js). Si no existe o está corrupto, {} — mismo
// comportamiento que un manga sin capítulos registrados todavía.
function getRegistroProgresoSync(mangaFolderPath) {
  const f = require('path').join(mangaFolderPath, 'registro_progreso.json');
  if (!fs.existsSync(f)) return {};
  try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return {}; }
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

module.exports = {
  writeJsonAtomic, naturalCompare,
  listDirNames, listImageNames, getFolderDate, getDirMtimeMs,
  getMetadataSync, getRegistroProgresoSync, formatDate, IMAGE_EXT_RE,
  COVER_EXTENSIONS, COVER_FILENAME_RE
};
