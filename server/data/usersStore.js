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
  // Escritura asíncrona igual que progress
  fs.writeFile(USERS_FILE, JSON.stringify(users, null, 2), err => {
    if (err) console.error('[Users] Error guardando:', err.message);
  });
}

function getUserById(id) {
  return loadUsers().find(u => u.id === id) || null;
}

// Restricciones de contenido de un usuario. Los administradores nunca están
// restringidos, sin importar lo que tengan guardado en su registro.
function getUserRestrictions(userId, role) {
  if (role === 'admin') return { canViewAdult: true, blockedMangas: [] };
  const user = getUserById(userId);
  if (!user) return { canViewAdult: true, blockedMangas: [] };
  return {
    canViewAdult:  user.canViewAdult !== false, // default true si no está definido (no rompe usuarios viejos)
    blockedMangas: Array.isArray(user.blockedMangas) ? user.blockedMangas : []
  };
}

module.exports = { loadUsers, saveUsers, getUserById, getUserRestrictions, USERS_FILE };
