const express = require('express');
const jwt     = require('jsonwebtoken');
const crypto  = require('crypto');
const fs      = require('fs');
const path    = require('path');
const authMiddleware = require('../middleware/auth');

const router    = express.Router();
const USERS_FILE = path.join(__dirname, '../data/users.json');

// ── HELPERS ───────────────────────────────────────────────────────────────────
function hashPassword(password, salt) {
  return crypto.createHmac('sha256', salt).update(password).digest('hex');
}

function loadUsers() {
  if (!fs.existsSync(USERS_FILE)) return [];
  try { return JSON.parse(fs.readFileSync(USERS_FILE, 'utf8')); }
  catch { return []; }
}

function saveUsers(users) {
  fs.mkdirSync(path.dirname(USERS_FILE), { recursive: true });
  fs.writeFileSync(USERS_FILE, JSON.stringify(users, null, 2));
}

function initAdminIfNeeded() {
  let users = loadUsers();
  if (users.length === 0) {
    const salt = crypto.randomBytes(16).toString('hex');
    const password = process.env.PASSWORD || 'admin';
    users.push({
      id: '1', username: 'M4RTO', role: 'admin',
      salt, passwordHash: hashPassword(password, salt),
      createdAt: new Date().toISOString()
    });
    saveUsers(users);
    console.log(`  👤 Usuario admin creado: M4RTO / ${password}`);
  }
}

initAdminIfNeeded();

// ── POST /api/login ────────────────────────────────────────────────────────────
router.post('/login', (req, res) => {
  const { username, password } = req.body;
  if (!username || !password) return res.status(400).json({ error: 'Usuario y contraseña requeridos.' });
  const users = loadUsers();
  const user = users.find(u => u.username.toLowerCase() === username.toLowerCase());
  if (!user) return res.status(401).json({ error: 'Usuario o contraseña incorrectos.' });
  const hash = hashPassword(password, user.salt);
  if (hash !== user.passwordHash) return res.status(401).json({ error: 'Usuario o contraseña incorrectos.' });
  const token = jwt.sign({ userId: user.id, username: user.username, role: user.role }, process.env.JWT_SECRET, { expiresIn: '30d' });
  res.json({ token, username: user.username, role: user.role });
});

// ── GET /api/verify ────────────────────────────────────────────────────────────
router.get('/verify', authMiddleware, (req, res) => {
  res.json({ valid: true, username: req.user.username, role: req.user.role });
});

// ── ADMIN: GET /api/users ──────────────────────────────────────────────────────
router.get('/users', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  const users = loadUsers().map(u => ({ id: u.id, username: u.username, role: u.role, createdAt: u.createdAt }));
  res.json(users);
});

// ── ADMIN: POST /api/users ─────────────────────────────────────────────────────
router.post('/users', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  const { username, password, role } = req.body;
  if (!username || !password) return res.status(400).json({ error: 'Faltan datos.' });
  const users = loadUsers();
  if (users.find(u => u.username.toLowerCase() === username.toLowerCase()))
    return res.status(409).json({ error: 'El usuario ya existe.' });
  const salt = crypto.randomBytes(16).toString('hex');
  const newUser = {
    id: Date.now().toString(), username, role: role || 'reader',
    salt, passwordHash: hashPassword(password, salt),
    createdAt: new Date().toISOString()
  };
  users.push(newUser);
  saveUsers(users);
  res.json({ ok: true, id: newUser.id, username, role: newUser.role });
});

// ── ADMIN: PUT /api/users/:id ──────────────────────────────────────────────────
router.put('/users/:id', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  const users = loadUsers();
  const idx = users.findIndex(u => u.id === req.params.id);
  if (idx < 0) return res.status(404).json({ error: 'Usuario no encontrado.' });
  const { password, role } = req.body;
  if (password) {
    const salt = crypto.randomBytes(16).toString('hex');
    users[idx].salt = salt;
    users[idx].passwordHash = hashPassword(password, salt);
  }
  if (role) users[idx].role = role;
  saveUsers(users);
  res.json({ ok: true });
});

// ── ADMIN: DELETE /api/users/:id ──────────────────────────────────────────────
router.delete('/users/:id', authMiddleware, (req, res) => {
  if (req.user.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  if (req.user.userId === req.params.id) return res.status(400).json({ error: 'No puedes eliminarte a ti mismo.' });
  let users = loadUsers();
  users = users.filter(u => u.id !== req.params.id);
  saveUsers(users);
  res.json({ ok: true });
});

module.exports = router;
