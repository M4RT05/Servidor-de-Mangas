const authMiddleware = require('./auth');
const { resolveUser } = require('./auth');

// Antes vivía como imageAuth() dentro de index.js, solo para servir
// imágenes. Se movió acá y se generalizó el nombre porque el stream en
// vivo del scraper (EventSource) tiene EXACTAMENTE el mismo problema que
// una etiqueta <img>: el navegador no puede mandar el header
// Authorization en ninguno de los dos casos, así que hace falta aceptar
// también una cookie httpOnly o un token en la URL como alternativa.
//
// Orden de intento: header Authorization (clientes API/lector) → cookie
// img_token (imágenes y streams desde el navegador) → query param
// ?token= (compatibilidad con el lector actual).
function cookieOrHeaderAuth(req, res, next) {
  const authHeader = req.headers['authorization'];
  if (authHeader) return authMiddleware(req, res, next);

  const cookieToken = req.cookies?.img_token;
  if (cookieToken) {
    try {
      const user = resolveUser(cookieToken);
      if (user) { req.user = user; return next(); }
    } catch {}
  }

  const qt = req.query.token;
  if (qt) {
    try {
      const user = resolveUser(qt);
      if (user) { req.user = user; return next(); }
    } catch {}
  }

  return res.status(401).send('No autorizado.');
}

module.exports = cookieOrHeaderAuth;
