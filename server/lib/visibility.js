// ── VISIBILIDAD DE MANGA SEGÚN RESTRICCIONES DE USUARIO ──────────────────────
// Único lugar donde vive la regla de "¿este usuario puede ver este manga?".
// Antes estaba duplicada (con una pequeña diferencia: index.js hacía los tres
// chequeos a mano en vez de llamar a esta función) en server/routes/manga.js
// y en la ruta de imágenes de server/index.js. Cualquier cambio a las reglas
// de restricción — por ejemplo, agregar un tercer nivel de permiso — antes
// requería acordarse de tocar los dos lugares.
function visibleTo(entry, restrictions) {
  const { canViewAdult, canViewNormal, blockedMangas } = restrictions;
  if (blockedMangas.includes(entry.name)) return false;
  return entry.metadata.adult ? canViewAdult : canViewNormal;
}

module.exports = { visibleTo };
