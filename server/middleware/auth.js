const jwt = require('jsonwebtoken');
const { getUserById } = require('../data/usersStore');

// Verifica la firma/expiración del JWT y ADEMÁS confirma que el usuario
// sigue existiendo en users.json, devolviendo su rol y restricciones
// ACTUALES (no lo que quedó grabado en el token al loguearse).
//
// Por qué: un JWT dura 30 días. Sin este chequeo, si borrás un usuario o le
// bajás el rol de admin a reader, su token viejo sigue funcionando con los
// permisos de ANTES hasta que expire solo — no hay forma de cortarlo antes
// salvo rotar JWT_SECRET, que desloguea a todo el mundo, no solo a esa
// cuenta. Con esto, el cambio se aplica en el próximo request.
//
// loadUsers() (usado por getUserById) ya cachea en memoria 30s, así que
// este chequeo extra no implica leer users.json en cada pedido.
function resolveUser(token) {
  const payload = jwt.verify(token, process.env.JWT_SECRET); // lanza si es inválido/expiró
  const user = getUserById(payload.userId);
  if (!user) return null; // el usuario fue borrado después de emitido el token
  return { userId: user.id, username: user.username, role: user.role };
}

function authMiddleware(req, res, next) {
  const authHeader = req.headers['authorization'];
  let token = authHeader && authHeader.split(' ')[1];
  if (!token && req.query.token) token = req.query.token;
  if (!token) return res.status(401).json({ error: 'Token requerido.' });
  try {
    const user = resolveUser(token);
    if (!user) return res.status(401).json({ error: 'Sesión inválida — el usuario ya no existe.' });
    req.user = user;
    next();
  } catch {
    return res.status(403).json({ error: 'Token inválido o expirado.' });
  }
}

module.exports = authMiddleware;
module.exports.resolveUser = resolveUser;
