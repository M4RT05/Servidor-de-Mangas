// ── FUENTES DE DESCARGA ────────────────────────────────────────────────────
// Espejo de las claves de FUENTE_A_CLASE en scraper/scraper.py (línea ~6121)
// — si el scraper agrega un sitio nuevo, agregar su clave acá también, y el
// nombre/color correspondiente en client/js/app.js (SOURCE_INFO) + la clase
// .src-xxx en client/css/app.css.
const KNOWN_SOURCES = [
  'olympus', 'temple', 'dragon', 'manhwaweb',
  'nexus', 'ikigai', 'leercapitulo', 'taurus', 'tmo'
];

// Valor que usa el propio scraper para capítulos sin una fuente de scraping
// real detrás — ver FUENTE_EXTERNA en scraper.py (regenerar_registro_completo,
// para capítulos que ya estaban en disco cuando no había registro_progreso.json).
const EXTERNAL_SOURCE = 'externa';

// Todo lo que el editor de metadata puede guardar en el campo 'forcedSource'.
const ALLOWED_FORCED_SOURCES = [...KNOWN_SOURCES, EXTERNAL_SOURCE];

// Cuenta ocurrencias de 'fuente' en un registro_progreso.json ya cargado
// (ver getRegistroProgresoSync en fsHelpers.js).
//
// Dos casos se agrupan bajo EXTERNAL_SOURCE porque para quien lee el conteo
// significan lo mismo ("no sé de qué sitio salió este capítulo"):
//   - fuente: null/ausente  → capitulo_esta_completo() lo completó por
//     fallback (manga bajado con una versión vieja del scraper, sin tracking
//     de fuente todavía).
//   - fuente: "externa"     → regenerar_registro_completo() lo armó desde
//     cero a partir de lo que ya había en disco.
//
// Cualquier OTRO string (typo, sitio viejo renombrado, etc.) se deja tal
// cual — no se descarta ni se agrupa con nada — para que se note en el badge
// como fuente "desconocida" en vez de desaparecer silenciosamente.
function computeSourceCounts(registro) {
  const counts = {};
  for (const info of Object.values(registro || {})) {
    const f = (info && info.fuente) || EXTERNAL_SOURCE;
    counts[f] = (counts[f] || 0) + 1;
  }
  return counts;
}

// Ordena las fuentes de mayor a menor frecuencia. Empate → alfabético, para
// que el resultado sea el mismo entre reconstrucciones del índice (no debe
// depender del orden de iteración de Object.values).
function rankSources(counts) {
  return Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
}

// Combina el conteo automático con una fuente forzada a mano en metadata.json
// (campo 'forcedSource', opcional). Si hay forzada, PISA el resultado en los
// dos lugares donde se muestra — últimos capítulos Y ficha del manga — no
// solo uno: si se está forzando es porque el conteo real no refleja lo que
// se quiere mostrar, así que no tiene sentido que el automático se cuele en
// ningún lado.
//
// 'autoRanked' se devuelve siempre (aunque haya forzada) para que el editor
// de metadata pueda mostrar "detectado automáticamente: X, Y" como referencia
// sin perder ese dato por tener una fuente forzada activa.
function resolveSources(counts, forcedSource) {
  const autoRanked = rankSources(counts);
  const forced = Boolean(forcedSource);
  return {
    counts,
    autoRanked,
    ranked: forced ? [forcedSource] : autoRanked,
    forced
  };
}

module.exports = {
  KNOWN_SOURCES, EXTERNAL_SOURCE, ALLOWED_FORCED_SOURCES,
  computeSourceCounts, rankSources, resolveSources
};
