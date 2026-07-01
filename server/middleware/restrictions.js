const { getUserRestrictions } = require('../data/usersStore');

// Debe ejecutarse DESPUÉS de authMiddleware (necesita req.user.userId/role).
// Adjunta req.userRestrictions = { canViewAdult, blockedMangas } para que
// las rutas de manga.js y la de servir imágenes puedan filtrar/bloquear
// contenido sin volver a leer users.json cada vez.
function restrictionsMiddleware(req, res, next) {
  req.userRestrictions = getUserRestrictions(req.user?.userId, req.user?.role);
  next();
}

module.exports = restrictionsMiddleware;
