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
const restrictionsMiddleware = require('./middleware/restrictions');
const catalogIndex   = require('./data/catalogIndex');
const imageCache     = require('./lib/imageCache');
const { visibleTo }  = require('./lib/visibility');
const thumbnails     = require('./lib/thumbnails');

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
    // JS/CSS: no-store → el browser nunca guarda en caché, siempre pide la versión nueva
    else if (/\.(js|css)$/.test(filePath)) {
      res.setHeader('Cache-Control', 'no-store');
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
app.use('/api/mangas', authMiddleware, restrictionsMiddleware, mangaRoutes);

// ── IMÁGENES PROTEGIDAS CON CACHÉ LARGO ──────────────────────────────────────
// Middleware especial para imágenes: acepta token en cookie además de header
function imageAuth(req, res, next) {
  const { resolveUser } = require('./middleware/auth');
  // Primero intenta header Authorization (lector, API)
  const authHeader = req.headers['authorization'];
  if (authHeader) return require('./middleware/auth')(req, res, next);
  // Luego intenta cookie img_token (imágenes desde el browser)
  const cookieToken = req.cookies?.img_token;
  if (cookieToken) {
    try {
      const user = resolveUser(cookieToken);
      if (user) { req.user = user; return next(); }
    } catch {}
  }
  // Finalmente query param (compatibilidad con lector actual)
  const qt = req.query.token;
  if (qt) {
    try {
      const user = resolveUser(qt);
      if (user) { req.user = user; return next(); }
    } catch {}
  }
  return res.status(401).send('No autorizado.');
}

app.get('/api/images/:manga/:chapter/:image', imageAuth, restrictionsMiddleware, async (req, res) => {
  const manga   = decodeURIComponent(req.params.manga);
  const chapter = decodeURIComponent(req.params.chapter);
  const image   = decodeURIComponent(req.params.image);

  // Mismo criterio que en manga.js — literalmente la misma función ahora
  // (server/lib/visibility.js), ya no una reimplementación aparte.
  const restrictions = req.userRestrictions || { canViewAdult: false, canViewNormal: false, blockedMangas: [] };
  // Antes: findMangaRoot() recorría cada root con fs.existsSync y getMetadata()
  // releía metadata.json — ambos en CADA pedido de imagen (hasta 30+ veces por
  // capítulo, siempre el mismo resultado). Ahora sale del índice en memoria.
  const entry = catalogIndex.getMangaEntry(manga);
  if (!visibleTo({ name: manga, metadata: entry?.metadata || {} }, restrictions)) {
    return res.status(404).send('Imagen no encontrada.');
  }

  const mangaRoot = catalogIndex.findMangaRoot(manga);
  const imagePath = chapter === '__cover__'
    ? path.join(mangaRoot, manga, 'cover.jpg')
    : path.join(mangaRoot, manga, chapter, image);
  const resolved = path.resolve(imagePath);
  if (!resolved.startsWith(path.resolve(mangaRoot))) return res.status(404).send('Imagen no encontrada.');

  // Imágenes de manga nunca cambian → caché 7 días en el navegador
  res.setHeader('Cache-Control', 'public, max-age=604800, immutable');
  res.setHeader('Vary', 'Accept-Encoding');

  // Miniatura: SOLO para portadas (chapter === '__cover__'), nunca para
  // páginas de capítulo — ahí sigue rigiendo la política de cero pérdida de
  // calidad sin excepciones. Si sharp no está disponible o falla, se cae de
  // forma transparente a servir la portada completa más abajo.
  if (chapter === '__cover__' && req.query.thumb === '1') {
    const thumb = await thumbnails.getThumbnail(resolved);
    if (thumb) {
      res.setHeader('Content-Type', 'image/webp');
      return res.send(thumb);
    }
  }

  // Si ya está precalentada en RAM (ver /:manga/:chapter/images), se sirve
  // directo de memoria sin tocar el disco externo.
  const cached = imageCache.get(resolved);
  if (cached) {
    res.setHeader('Content-Type', mimeForImage(resolved));
    return res.send(cached);
  }
  try {
    const buf = await fs.promises.readFile(resolved);
    if (imageCache.isStable(resolved)) imageCache.set(resolved, buf);
    res.setHeader('Content-Type', mimeForImage(resolved));
    res.send(buf);
  } catch {
    res.status(404).send('Imagen no encontrada.');
  }
});

function mimeForImage(p) {
  const ext = path.extname(p).toLowerCase();
  return { '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.png':'image/png', '.webp':'image/webp', '.gif':'image/gif' }[ext] || 'application/octet-stream';
}

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
