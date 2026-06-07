const express = require('express');
const jwt     = require('jsonwebtoken');
const crypto  = require('crypto');
const fs      = require('fs');
const path    = require('path');
const multer  = require('multer');
const authMiddleware = require('../middleware/auth');

const router      = express.Router();
const USERS_FILE  = path.join(__dirname, '../data/users.json');
const AVATARS_DIR = path.join(__dirname, '../data/avatars');

// ── CACHÉ EN MEMORIA PARA USUARIOS (mejora #4) ────────────────────────────────
let _usersCache     = null;
let _usersCacheTime = 0;
const USERS_TTL     = 30 * 1000; // 30 segundos

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
function hashPassword(password, salt) {
  return crypto.createHmac('sha256', salt).update(password).digest('hex');
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
    const username = process.env.USERNAME || 'admin';
    const password = process.env.PASSWORD || 'admin';
    users.push({
      id: '1', username, role: 'admin',
      salt, passwordHash: hashPassword(password, salt),
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
  filename:    (req, file, cb) => { const ext = path.extname(file.originalname).toLowerCase() || '.jpg'; cb(null, req.user.username + ext); }
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
  if (!user || hashPassword(password, user.salt) !== user.passwordHash) {
    recordFailedAttempt(ip);
    return res.status(401).json({ error: 'Usuario o contraseña incorrectos.' });
  }

  clearAttempts(ip);
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
  res.json(loadUsers().map(u => ({ id: u.id, username: u.username, role: u.role, createdAt: u.createdAt })));
});

router.post('/users', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  const { username, password, role } = req.body;
  if (!username || !password) return res.status(400).json({ error: 'Faltan datos.' });
  const users = loadUsers();
  if (users.find(u => u.username.toLowerCase() === username.toLowerCase()))
    return res.status(409).json({ error: 'El usuario ya existe.' });
  const salt    = crypto.randomBytes(16).toString('hex');
  const newUser = { id: Date.now().toString(), username, role: role || 'reader', salt, passwordHash: hashPassword(password, salt), createdAt: new Date().toISOString() };
  users.push(newUser);
  saveUsers(users);
  res.json({ ok: true, id: newUser.id, username, role: newUser.role });
});

router.put('/users/:id', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  const users = loadUsers();
  const idx   = users.findIndex(u => u.id === req.params.id);
  if (idx < 0) return res.status(404).json({ error: 'Usuario no encontrado.' });
  const { password, role } = req.body;
  if (password) { const salt = crypto.randomBytes(16).toString('hex'); users[idx].salt = salt; users[idx].passwordHash = hashPassword(password, salt); }
  if (role) users[idx].role = role;
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
