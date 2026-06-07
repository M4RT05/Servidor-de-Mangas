const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '../.env') });

const compression  = require('compression');
const cookieParser = require('cookie-parser');
const express     = require('express');
const fs          = require('fs');

if (!process.env.JWT_SECRET) {
  const crypto = require('crypto');
  process.env.JWT_SECRET = crypto.randomBytes(48).toString('hex');
  console.warn('\n  ⚠️  JWT_SECRET no encontrado. Se generó uno temporal.\n');
}

const authRoutes     = require('./routes/auth');
const mangaRoutes    = require('./routes/manga');
const authMiddleware = require('./middleware/auth');

const app  = express();
const PORT = process.env.PORT || 3000;

app.use(cookieParser());

// ── COMPRESIÓN GZIP/BROTLI ────────────────────────────────────────────────────
app.use(compression({
  level: 6,
  threshold: 512, // comprimir desde 512 bytes
  filter: (req, res) => {
    // No comprimir imágenes (ya están comprimidas)
    if (/\.(jpg|jpeg|png|webp|gif)$/i.test(req.path)) return false;
    return compression.filter(req, res);
  }
}));

app.use(express.json());

// ── ARCHIVOS ESTÁTICOS CON CACHÉ AGRESIVO ────────────────────────────────────
// JS y CSS: caché 1 día (el contenido no cambia sin reiniciar el servidor)
app.use(express.static(path.join(__dirname, '../client'), {
  maxAge: '1d',
  etag: true,
  lastModified: true,
  setHeaders: (res, filePath) => {
    // HTML: no cachear (siempre fresco)
    if (filePath.endsWith('.html')) {
      res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
    }
    // JS/CSS: sin caché → browser siempre verifica, servidor responde 304 si no cambió
    else if (/\.(js|css)$/.test(filePath)) {
      res.setHeader('Cache-Control', 'no-cache');
    }
    // Fuentes/iconos: caché 7 días
    else if (/\.(woff2?|ttf|eot|svg)$/.test(filePath)) {
      res.setHeader('Cache-Control', 'public, max-age=604800');
    }
  }
}));

app.use('/avatars', express.static(path.join(__dirname, 'data/avatars'), {
  maxAge: '1h',
  etag: true
}));

// ── RUTAS API ─────────────────────────────────────────────────────────────────
// Inyectar cookie img_token al verificar token válido (para imágenes)
app.post('/api/set-img-cookie', (req, res) => {
  const token = req.body?.token || req.headers['authorization']?.split(' ')[1];
  if (!token) return res.status(400).json({ error: 'Token requerido.' });
  try {
    require('jsonwebtoken').verify(token, process.env.JWT_SECRET);
    res.cookie('img_token', token, {
      httpOnly: true,
      sameSite: 'Lax',
      maxAge:   30 * 24 * 60 * 60 * 1000 // 30 días
    });
    res.json({ ok: true });
  } catch { res.status(403).json({ error: 'Token inválido.' }); }
});

app.use('/api', authRoutes);
app.use('/api/mangas', authMiddleware, mangaRoutes);

// ── IMÁGENES PROTEGIDAS CON CACHÉ LARGO ──────────────────────────────────────
const mangaRootsResolved = () => {
  const roots = [path.resolve(process.env.MANGA_PATH || './main')];
  if (process.env.MANGA_PATH_2) roots.push(path.resolve(process.env.MANGA_PATH_2));
  return roots;
};

// Middleware especial para imágenes: acepta token en cookie además de header
function imageAuth(req, res, next) {
  // Primero intenta header Authorization (lector, API)
  const authHeader = req.headers['authorization'];
  if (authHeader) return require('./middleware/auth')(req, res, next);
  // Luego intenta cookie img_token (imágenes desde el browser)
  const cookieToken = req.cookies?.img_token;
  if (cookieToken) {
    try {
      req.user = require('jsonwebtoken').verify(cookieToken, process.env.JWT_SECRET);
      return next();
    } catch {}
  }
  // Finalmente query param (compatibilidad con lector actual)
  const qt = req.query.token;
  if (qt) {
    try {
      req.user = require('jsonwebtoken').verify(qt, process.env.JWT_SECRET);
      return next();
    } catch {}
  }
  return res.status(401).send('No autorizado.');
}

app.get('/api/images/:manga/:chapter/:image', imageAuth, (req, res) => {
  const manga   = decodeURIComponent(req.params.manga);
  const chapter = decodeURIComponent(req.params.chapter);
  const image   = decodeURIComponent(req.params.image);
  for (const mangaRoot of mangaRootsResolved()) {
    const imagePath = chapter === '__cover__'
      ? path.join(mangaRoot, manga, 'cover.jpg')
      : path.join(mangaRoot, manga, chapter, image);
    const resolved = path.resolve(imagePath);
    if (!resolved.startsWith(path.resolve(mangaRoot))) continue;
    if (fs.existsSync(resolved)) {
      // Imágenes de manga nunca cambian → caché 7 días
      res.setHeader('Cache-Control', 'public, max-age=604800, immutable');
      res.setHeader('Vary', 'Accept-Encoding');
      return res.sendFile(resolved);
    }
  }
  res.status(404).send('Imagen no encontrada.');
});

// ── SPA FALLBACK ──────────────────────────────────────────────────────────────
app.get('*', (req, res) => {
  res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
  res.sendFile(path.join(__dirname, '../client/index.html'));
});

// ── INICIO ────────────────────────────────────────────────────────────────────
function getLocalIP() {
  const { networkInterfaces } = require('os');
  const nets = networkInterfaces();
  const wifiKeywords  = ['wi-fi', 'wifi', 'wlan', 'wireless', 'inalambric'];
  const etherKeywords = ['ethernet', 'eth', 'lan'];
  let wifiIP = null, ethIP = null, anyIP = null;
  for (const [name, addrs] of Object.entries(nets)) {
    const lower = name.toLowerCase();
    for (const net of addrs) {
      if (net.family !== 'IPv4' || net.internal) continue;
      if (!anyIP) anyIP = net.address;
      if (wifiKeywords.some(k => lower.includes(k)) && !wifiIP) wifiIP = net.address;
      if (etherKeywords.some(k => lower.includes(k)) && !ethIP)  ethIP = net.address;
    }
  }
  return wifiIP || ethIP || anyIP || '<TU_IP>';
}

app.listen(PORT, '0.0.0.0', () => {
  console.log('\n  📚 MangaServer iniciado');
  console.log(`  💻 Local:     http://localhost:${PORT}`);
  console.log(`  📱 Red local: http://${getLocalIP()}:${PORT}\n`);
  mangaRoutes.startWatcher();
});

process.on('SIGINT',  () => { mangaRoutes.stopWatcher(); process.exit(0); });
process.on('SIGTERM', () => { mangaRoutes.stopWatcher(); process.exit(0); });
