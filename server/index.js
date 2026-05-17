const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '../.env') });

const express = require('express');
const fs      = require('fs');

if (!process.env.JWT_SECRET) {
  const crypto = require('crypto');
  process.env.JWT_SECRET = crypto.randomBytes(48).toString('hex');
  console.warn('\n  ⚠️  JWT_SECRET no encontrado. Se generó uno temporal.\n');
}

const authRoutes  = require('./routes/auth');
const mangaRoutes = require('./routes/manga');
const authMiddleware = require('./middleware/auth');

const app  = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());
app.use(express.static(path.join(__dirname, '../client')));

// Públicas
app.use('/api', authRoutes);

// Mangas
app.use('/api/mangas', authMiddleware, mangaRoutes);

// Imágenes protegidas
app.get('/api/images/:manga/:chapter/:image', authMiddleware, (req, res) => {
  const mangaRoot = path.resolve(process.env.MANGA_PATH || './main');
  const manga   = decodeURIComponent(req.params.manga);
  const chapter = decodeURIComponent(req.params.chapter);
  const image   = decodeURIComponent(req.params.image);
  const imagePath = chapter === '__cover__'
    ? path.join(mangaRoot, manga, 'cover.jpg')
    : path.join(mangaRoot, manga, chapter, image);
  const resolved = path.resolve(imagePath);
  if (!resolved.startsWith(path.resolve(mangaRoot))) return res.status(403).send('Acceso denegado.');
  if (!fs.existsSync(resolved)) return res.status(404).send('Imagen no encontrada.');
  res.setHeader('Cache-Control', 'public, max-age=86400');
  res.sendFile(resolved);
});

app.get('*', (req, res) => res.sendFile(path.join(__dirname, '../client/index.html')));

app.listen(PORT, '0.0.0.0', () => {
  console.log('\n  📚 MangaServer iniciado');
  console.log(`  💻 Local:     http://localhost:${PORT}`);
  console.log(`  📱 Red local: http://<TU_IP>:${PORT}\n`);
  mangaRoutes.startWatcher();
});

process.on('SIGINT',  () => { mangaRoutes.stopWatcher(); process.exit(0); });
process.on('SIGTERM', () => { mangaRoutes.stopWatcher(); process.exit(0); });
