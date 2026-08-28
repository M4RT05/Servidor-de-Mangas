// ── SLUGS PARA URLs LIMPIAS ───────────────────────────────────────────────────
// Convierte nombres reales de carpeta (con tildes, espacios, mayúsculas) en
// slugs aptos para URL. El de manga es de propósito general (cualquier texto).
// El de capítulo reconoce el patrón real de las carpetas del proyecto
// ("Capitulo_129.5") y lo reduce a "cap_129.5" — si no matchea ese patrón
// (extras, especiales con nombre raro, etc.) cae a un slug general con "_"
// como separador, para no romper nunca con un nombre de carpeta inesperado.

// Saca tildes/diacríticos (á->a, é->e, ñ->n, etc.) vía normalización NFD +
// remoción de las marcas combinantes — técnica estándar, sin librerías extra.
function stripDiacritics(str) {
  return str.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

// Slug general: minúsculas, sin tildes, todo lo que no sea [a-z0-9] colapsa a
// UN separador, recortando los separadores de punta.
function toSlug(str, sep = '-') {
  const esc = sep.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return stripDiacritics(String(str))
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, sep)
    .replace(new RegExp(`^${esc}+|${esc}+$`, 'g'), '');
}

// Slug de manga: "Comic Viviendo Como Un Bárbaro En Un Mundo De Fantasía"
// -> "comic-viviendo-como-un-barbaro-en-un-mundo-de-fantasia"
function slugifyManga(name) {
  return toSlug(name, '-');
}

// Slug de capítulo: reconoce "Capitulo_N" / "Capítulo N" / "capitulo.N" (con
// decimales tipo N.M para especiales, ej. "Capitulo_129.5") y lo normaliza a
// "cap_N". Si la carpeta no sigue ese patrón (extras con nombre propio,
// "Omake", "Extra 1", etc.) usa el slug general con "_" como separador.
const CHAPTER_NUM_RE = /^cap[ií]?tulo[\s_.-]*(\d+(?:\.\d+)?)/i;
function slugifyChapter(rawName) {
  const m = String(rawName).match(CHAPTER_NUM_RE);
  if (m) return `cap_${m[1]}`;
  return toSlug(rawName, '_');
}

module.exports = { toSlug, slugifyManga, slugifyChapter, stripDiacritics };
