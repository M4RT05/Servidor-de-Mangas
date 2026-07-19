const express = require('express');
const jwt     = require('jsonwebtoken');
const crypto  = require('crypto');
const fs      = require('fs');
const path    = require('path');
const multer  = require('multer');
const authMiddleware = require('../middleware/auth');
const { loadUsers, saveUsers } = require('../data/usersStore');

const router      = express.Router();
const AVATARS_DIR = path.join(__dirname, '../data/avatars');

// ── RATE LIMITING EN LOGIN (mejora #3) ────────────────────────────────────────
// Map<ip, {count, blockedUntil}>
const loginAttempts = new Map();
const MAX_ATTEMPTS  = 10;
const BLOCK_MS      = 5 * 60 * 1000; // 5 minutos

function checkRateLimit(ip) {
  const now  = Date.now();
  const data = loginAttempts.get(ip) || { count: 0, blockedUntil: 0 };
  if (data.blockedUntil > now) {
    const secs = Math.ceil((data.blockedUntil - now) / 1000);
    return { blocked: true, secs };
  }
  // Limpiar si el bloqueo ya expiró
  if (data.blockedUntil && data.blockedUntil <= now) {
    loginAttempts.delete(ip);
    return { blocked: false };
  }
  return { blocked: false };
}

function recordFailedAttempt(ip) {
  const now  = Date.now();
  const data = loginAttempts.get(ip) || { count: 0, blockedUntil: 0 };
  data.count++;
  if (data.count >= MAX_ATTEMPTS) {
    data.blockedUntil = now + BLOCK_MS;
    console.warn(`[Auth] IP ${ip} bloqueada por ${MAX_ATTEMPTS} intentos fallidos.`);
  }
  loginAttempts.set(ip, data);
}

function clearAttempts(ip) { loginAttempts.delete(ip); }

// Limpiar entradas viejas cada 10 minutos para no acumular en memoria
setInterval(() => {
  const now = Date.now();
  for (const [ip, data] of loginAttempts.entries()) {
    if (data.blockedUntil < now && data.count < MAX_ATTEMPTS) loginAttempts.delete(ip);
    if (data.blockedUntil && data.blockedUntil < now - BLOCK_MS) loginAttempts.delete(ip);
  }
}, 10 * 60 * 1000);

// ── HELPERS ───────────────────────────────────────────────────────────────────
// Hash de contraseñas con scrypt (nativo de Node, sin dependencia nueva).
// A diferencia del HMAC-SHA256 que se usaba antes, scrypt está diseñado a
// propósito para ser LENTO y pesado en memoria — eso es lo que lo hace
// resistente a fuerza bruta si alguna vez se filtra users.json. HMAC-SHA256
// es rapidísimo, que es exactamente lo que no querés para contraseñas.
function hashPasswordScrypt(password, salt) {
  return crypto.scryptSync(password, salt, 64).toString('hex');
}
// Hash viejo (HMAC-SHA256). Se mantiene SOLO para poder seguir verificando
// contraseñas de usuarios que todavía no pasaron por el login desde este
// cambio — ver verifyPassword() y la migración perezosa en /login más abajo.
// No se usa para generar hashes nuevos nunca más.
function hashPasswordLegacy(password, salt) {
  return crypto.createHmac('sha256', salt).update(password).digest('hex');
}
// Punto único de verificación de contraseña. user.hashAlgo indica qué
// función se usó para generar el hash guardado — si no está presente,
// es un usuario de antes de este cambio y se asume el esquema legacy.
function verifyPassword(password, user) {
  if (user.hashAlgo === 'scrypt') return hashPasswordScrypt(password, user.salt) === user.passwordHash;
  return hashPasswordLegacy(password, user.salt) === user.passwordHash;
}

function getClientIP(req) {
  return req.headers['x-forwarded-for']?.split(',')[0]?.trim()
    || req.socket?.remoteAddress
    || 'unknown';
}

function initAdminIfNeeded() {
  const users    = loadUsers();
  if (users.length === 0) {
    const salt     = crypto.randomBytes(16).toString('hex');
    const username = process.env.ADMIN_USERNAME || 'admin';
    const password = process.env.ADMIN_PASSWORD || 'admin';
    users.push({
      id: '1', username, role: 'admin',
      salt, passwordHash: hashPasswordScrypt(password, salt), hashAlgo: 'scrypt',
      createdAt: new Date().toISOString()
    });
    saveUsers(users);
    console.log(`  👤 Usuario admin creado: ${username} / ${password}`);
  }
}
initAdminIfNeeded();

// ── AVATAR (multer) ───────────────────────────────────────────────────────────
const avatarStorage = multer.diskStorage({
  destination: (req, file, cb) => { fs.mkdirSync(AVATARS_DIR, { recursive: true }); cb(null, AVATARS_DIR); },
  // Se usa el id del usuario, no el username: el id siempre lo genera el
  // servidor (nunca viene de un input), así que no hay forma de que un
  // username raro (con "/", "..", etc.) termine escribiendo el archivo
  // fuera de AVATARS_DIR.
  filename:    (req, file, cb) => { const ext = path.extname(file.originalname).toLowerCase() || '.jpg'; cb(null, req.user.userId + ext); }
});
const avatarUpload = multer({
  storage: avatarStorage,
  limits:  { fileSize: 2 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    const allowed = ['image/jpeg','image/png','image/webp'];
    allowed.includes(file.mimetype) ? cb(null, true) : cb(new Error('Solo JPG, PNG o WEBP.'));
  }
});

// ── POST /api/login ───────────────────────────────────────────────────────────
router.post('/login', (req, res) => {
  const ip = getClientIP(req);
  const rl = checkRateLimit(ip);
  if (rl.blocked) return res.status(429).json({ error: `Demasiados intentos. Espera ${rl.secs} segundos.` });

  const { username, password } = req.body;
  if (!username || !password) return res.status(400).json({ error: 'Usuario y contraseña requeridos.' });

  const users = loadUsers();
  const user  = users.find(u => u.username.toLowerCase() === username.toLowerCase());
  if (!user || !verifyPassword(password, user)) {
    recordFailedAttempt(ip);
    return res.status(401).json({ error: 'Usuario o contraseña incorrectos.' });
  }

  clearAttempts(ip);

  // Migración perezosa: si esta cuenta todavía tiene el hash viejo
  // (HMAC-SHA256), este es el único momento en que tenemos la contraseña
  // en texto plano disponible — se re-hashea con scrypt y se guarda, sin
  // que el usuario tenga que hacer nada ni cambiar su contraseña.
  if (user.hashAlgo !== 'scrypt') {
    const newSalt = crypto.randomBytes(16).toString('hex');
    user.salt         = newSalt;
    user.passwordHash = hashPasswordScrypt(password, newSalt);
    user.hashAlgo      = 'scrypt';
    saveUsers(users);
  }

  const token = jwt.sign(
    { userId: user.id, username: user.username, role: user.role },
    process.env.JWT_SECRET,
    { expiresIn: '30d' }
  );
  res.json({ token, username: user.username, role: user.role });
});

// ── GET /api/verify ───────────────────────────────────────────────────────────
router.get('/verify', authMiddleware, (req, res) => {
  const users = loadUsers();
  const user  = users.find(u => u.id === req.user.userId);
  res.json({ valid: true, username: req.user.username, role: req.user.role, avatar: user?.avatar || null });
});

// ── POST /api/avatar ──────────────────────────────────────────────────────────
router.post('/avatar', authMiddleware, (req, res) => {
  avatarUpload.single('avatar')(req, res, (err) => {
    if (err) return res.status(400).json({ error: err.message });
    if (!req.file) return res.status(400).json({ error: 'No se recibió ninguna imagen.' });
    const users = loadUsers();
    const idx   = users.findIndex(u => u.id === req.user.userId);
    if (idx >= 0) {
      if (users[idx].avatar) {
        const oldPath = path.join(__dirname, '..', users[idx].avatar.replace(/^\//, ''));
        if (fs.existsSync(oldPath) && oldPath !== path.join(AVATARS_DIR, req.file.filename)) {
          try { fs.unlinkSync(oldPath); } catch {}
        }
      }
      users[idx].avatar = '/avatars/' + req.file.filename;
      saveUsers(users);
    }
    res.json({ ok: true, avatar: '/avatars/' + req.file.filename });
  });
});

// ── ADMIN: usuarios ───────────────────────────────────────────────────────────
router.get('/users', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  res.json(loadUsers().map(u => ({
    id: u.id, username: u.username, role: u.role, createdAt: u.createdAt,
    canViewAdult:  u.canViewAdult  !== false,
    canViewNormal: u.canViewNormal !== false,
    blockedMangas: Array.isArray(u.blockedMangas) ? u.blockedMangas : []
  })));
});

router.post('/users', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  const { username, password, role, canViewAdult, canViewNormal, blockedMangas } = req.body;
  if (!username || !password) return res.status(400).json({ error: 'Faltan datos.' });
  if (!/^[\p{L}\p{N}_.-]{1,32}$/u.test(username)) {
    return res.status(400).json({ error: 'El usuario solo puede tener letras, números, "_", "." o "-" (máx. 32 caracteres).' });
  }
  const users = loadUsers();
  if (users.find(u => u.username.toLowerCase() === username.toLowerCase()))
    return res.status(409).json({ error: 'El usuario ya existe.' });
  const salt    = crypto.randomBytes(16).toString('hex');
  const newUser = {
    id: Date.now().toString(), username, role: role || 'reader', salt,
    passwordHash: hashPasswordScrypt(password, salt), hashAlgo: 'scrypt', createdAt: new Date().toISOString(),
    canViewAdult:  canViewAdult  !== false,
    canViewNormal: canViewNormal !== false,
    blockedMangas: Array.isArray(blockedMangas) ? blockedMangas.filter(m => typeof m === 'string') : []
  };
  users.push(newUser);
  saveUsers(users);
  res.json({ ok: true, id: newUser.id, username, role: newUser.role, canViewAdult: newUser.canViewAdult, canViewNormal: newUser.canViewNormal, blockedMangas: newUser.blockedMangas });
});

router.put('/users/:id', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  const users = loadUsers();
  const idx   = users.findIndex(u => u.id === req.params.id);
  if (idx < 0) return res.status(404).json({ error: 'Usuario no encontrado.' });
  const { password, role, canViewAdult, canViewNormal, blockedMangas } = req.body;
  if (password) { const salt = crypto.randomBytes(16).toString('hex'); users[idx].salt = salt; users[idx].passwordHash = hashPasswordScrypt(password, salt); users[idx].hashAlgo = 'scrypt'; }
  if (role) users[idx].role = role;
  if (canViewAdult  !== undefined) users[idx].canViewAdult  = canViewAdult  !== false;
  if (canViewNormal !== undefined) users[idx].canViewNormal = canViewNormal !== false;
  if (blockedMangas !== undefined) users[idx].blockedMangas = Array.isArray(blockedMangas) ? blockedMangas.filter(m => typeof m === 'string') : [];
  saveUsers(users);
  res.json({ ok: true });
});

router.delete('/users/:id', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  if (req.user.userId === req.params.id) return res.status(400).json({ error: 'No puedes eliminarte a ti mismo.' });
  saveUsers(loadUsers().filter(u => u.id !== req.params.id));
  res.json({ ok: true });
});

module.exports = router;
