// ── ALMACÉN COMPARTIDO DE USUARIOS ───────────────────────────────────────────
// Extraído de auth.js para que otros módulos (manga.js, index.js) puedan
// consultar el rol y las restricciones de contenido de un usuario sin
// duplicar la caché ni crear una dependencia circular con las rutas de auth.
const fs   = require('fs');
const path = require('path');

const USERS_FILE = path.join(__dirname, 'users.json');
const USERS_TTL  = 30 * 1000; // 30 segundos

let _usersCache     = null;
let _usersCacheTime = 0;

function loadUsers() {
  const now = Date.now();
  if (_usersCache && (now - _usersCacheTime) < USERS_TTL) return _usersCache;
  if (!fs.existsSync(USERS_FILE)) { _usersCache = []; _usersCacheTime = now; return []; }
  try {
    _usersCache = JSON.parse(fs.readFileSync(USERS_FILE, 'utf8'));
    _usersCacheTime = now;
    return _usersCache;
  } catch { return []; }
}

function saveUsers(users) {
  _usersCache = users;
  _usersCacheTime = Date.now();
  fs.mkdirSync(path.dirname(USERS_FILE), { recursive: true });
  // Escritura atómica (tmp + rename): evita un users.json corrupto/truncado
  // si el proceso se cae justo a mitad de un guardado.
  try {
    const tmp = USERS_FILE + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(users, null, 2));
    fs.renameSync(tmp, USERS_FILE);
  } catch(e) { console.error('[Users] Error guardando:', e.message); }
}

function getUserById(id) {
  return loadUsers().find(u => u.id === id) || null;
}

// Restricciones de contenido de un usuario. Los administradores nunca están
// restringidos, sin importar lo que tengan guardado en su registro.
function getUserRestrictions(userId, role) {
  if (role === 'admin') return { canViewAdult: true, canViewNormal: true, blockedMangas: [] };
  const user = getUserById(userId);
  // Usuario no encontrado (borrado, id inválido, etc.) → sin acceso, nunca
  // "acceso total". authMiddleware ya debería haber cortado esto antes de
  // llegar acá (ver resolveUser en middleware/auth.js), esto es una segunda
  // capa por si algún día se llama a esta función desde otro lado.
  if (!user) return { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  return {
    canViewAdult:  user.canViewAdult  !== false, // default true si no está definido (no rompe usuarios viejos)
    canViewNormal: user.canViewNormal !== false, // ídem, para el modo "solo +18"
    blockedMangas: Array.isArray(user.blockedMangas) ? user.blockedMangas : []
  };
}

module.exports = { loadUsers, saveUsers, getUserById, getUserRestrictions, USERS_FILE };
