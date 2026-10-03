// ═════════════════════════════════════════════════════════════════════════
// upscaleControl.js — Panel de control de la mejora de calidad con IA
// ═════════════════════════════════════════════════════════════════════════
// Mismo espíritu que scraperControl.js (lock, estado_vivo.json, SSE,
// detección de caída por heartbeat), aplicado a la mejora con IA. Una
// diferencia de fondo con el scraper: acá NO hay un proceso externo único
// para toda la corrida — es esta misma función async, corriendo adentro
// del proceso de Node, la que va spawneando el binario de a una imagen por
// vez (ver server/lib/upscaler.js). Por eso no hace falta rastrear un PID
// externo para la corrida completa, solo para poder cancelar la imagen que
// está en curso en el momento de un "parar ya".
// ═════════════════════════════════════════════════════════════════════════

const express = require('express');
const fs      = require('fs');
const path    = require('path');

const router = express.Router();
const { listDirNames } = require('../lib/fsHelpers');
const upscaler = require('../lib/upscaler');
const motorInstaller = require('../lib/motorInstaller');

// ── Rutas y configuración ───────────────────────────────────────────────────
// Hay varias bibliotecas (MANGA_PATH, MANGA_PATH_2, ...) — se usa el mismo
// catálogo que el resto de la app para saber en cuál vive cada manga.
const { findMangaRoot, getMangaRoots } = require('../data/catalogIndex');

// Ruta completa de un manga por su nombre de carpeta, o null si no existe
// en ninguna biblioteca. Primero le pregunta al catálogo; si todavía no lo
// indexó (o no coincide), revisa cada biblioteca configurada.
function rutaDeManga(nombre) {
  if (!nombre) return null;
  const candidatas = [findMangaRoot(nombre), ...getMangaRoots()];
  for (const raiz of candidatas) {
    const ruta = path.join(raiz, nombre);
    if (fs.existsSync(ruta)) return ruta;
  }
  return null;
}
const ESTADO_DIR         = path.join(__dirname, '..', 'upscale-state');
const ESTADO_VIVO_PATH   = path.join(ESTADO_DIR, 'estado_vivo.json');

// Mismo archivo de lock que ya usa scraperControl.js/scraper.py — mientras
// el scraper esté corriendo, no se deja arrancar la mejora IA (compiten
// por el mismo disco externo). Pendiente (paso aparte, opcional): tocar
// _adquirir_lock() en scraper.py para que también respete ESTE lado —
// hoy la exclusión mutua funciona a medias, solo desde acá.
const SCRAPER_LOCK_PATH = path.join(__dirname, '..', '..', 'scraper', 'scraper.lock');

const UMBRAL_HEARTBEAT_MS = 2 * 60 * 1000; // mismo criterio que el scraper
const COOLDOWN_MS         = 15 * 1000;
const HEARTBEAT_TICK_MS   = 10 * 1000;      // refresco periódico del heartbeat mientras corre
const ESTADO_BROADCAST_MS = 1000;           // el avance sale de memoria (barato), así que se puede difundir seguido

fs.mkdirSync(ESTADO_DIR, { recursive: true });

function requireAdmin(req, res, next) {
  if (req.user?.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  next();
}

// ── SSE + buffer de log (mismo patrón que scraperControl.js) ───────────────
const sseClientes = new Set();
const LOG_BUFFER_MAX = 500;
const logBuffer = [];

function difundir(evento, data) {
  const linea = `event: ${evento}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClientes) { try { res.write(linea); } catch {} }
}

function log(mensaje) {
  console.log('[ia]', mensaje);
  logBuffer.push(mensaje);
  if (logBuffer.length > LOG_BUFFER_MAX) logBuffer.shift();
  difundir('log', mensaje);
}

// ── Estado persistido (estado_vivo.json) ────────────────────────────────────
function escribirEstadoAtomico(payload) {
  const tmp = ESTADO_VIVO_PATH + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(payload));
  fs.renameSync(tmp, ESTADO_VIVO_PATH);
}

function leerEstadoCrudo() {
  try { return JSON.parse(fs.readFileSync(ESTADO_VIVO_PATH, 'utf-8')); }
  catch { return { status: 'idle', since: null, heartbeat: null, progreso: null }; }
}

// Detección de caída, igual que scraperControl.js: si el archivo dice
// "corriendo" pero el heartbeat es viejo, el proceso murió sin pasar por su
// limpieza normal (corte de luz, Node cerrado a mano, crash). Se reporta
// como detenido en vez de dejar el panel trabado en "corriendo" para
// siempre — y de paso corrige el capítulo que hubiera quedado a medias.
function leerEstado() {
  // Mientras esta instancia está corriendo, la fuente de verdad del AVANCE es
  // la memoria. El archivo en disco solo se refresca cada 10s (es el latido
  // para detectar caídas, no para mostrar progreso): leer el avance de ahí
  // dejaba las barras del panel clavadas en el estado del inicio del capítulo.
  if (corridaActual) {
    return {
      status: 'corriendo', since: corridaActual.desde, progreso: corridaActual.progreso,
      corriendoEnEstaInstancia: true, instalacion,
    };
  }
  let estado = leerEstadoCrudo();
  if (estado.status === 'corriendo' && estado.heartbeat) {
    const edadMs = Date.now() - new Date(estado.heartbeat).getTime();
    if (edadMs > UMBRAL_HEARTBEAT_MS) {
      corregirCapituloHuerfano(estado.progreso);
      estado = { status: 'idle', since: null, heartbeat: null, progreso: null, cayoInesperadamente: true };
      escribirEstadoAtomico(estado);
      log('⚠️  Se detectó una corrida anterior que se cortó sin avisar (apagón/cierre forzado) — se corrigió el capítulo afectado.');
    }
  }
  return { ...estado, corriendoEnEstaInstancia: !!corridaActual, instalacion };
}

// El único capítulo que puede haber quedado en "en_progreso" de forma
// huérfana es el que estaba activo en el momento del corte — se reclasifica
// a "incompleto" para que la próxima corrida lo detecte y lo reintente solo
// (ver clasificarPaginas: las páginas ya mejoradas antes del corte se van a
// saltear igual, así que "reintentar" no repite trabajo de más).
function corregirCapituloHuerfano(progreso) {
  if (!progreso?.carpeta_manga || !progreso?.capitulo_actual) return;
  try {
    upscaler.actualizarIaDeCapitulo(progreso.carpeta_manga, progreso.capitulo_actual, {
      estado: 'incompleto',
      ultima_actualizacion: new Date().toISOString(),
      nota: 'Interrumpido sin aviso (corte de luz / cierre forzado) — reintentado solo en la próxima corrida.',
    });
  } catch (e) {
    console.warn('[ia] no se pudo corregir el capítulo huérfano:', e.message);
  }
}

// Difusión periódica de 'state' a los clientes SSE, desacoplada de cuándo
// se escribe a disco — mismo patrón que scraperControl.js.
let ultimoEstadoTexto = null;
setInterval(() => {
  const actual = leerEstado();
  const texto = JSON.stringify(actual);
  if (texto !== ultimoEstadoTexto) {
    ultimoEstadoTexto = texto;
    difundir('state', actual);
  }
}, ESTADO_BROADCAST_MS);

// ── Estado en memoria de la corrida activa (solo existe en esta instancia
// de Node — no hay PID externo que rastrear, ver nota de arriba) ───────────
let corridaActual = null;   // { banderaDetener, cancelarImagenActual, progreso }
// Estado de la instalación del motor (botón "Instalar motor ahora" del panel).
// Vive solo en memoria: si se reinicia el server a mitad de la descarga, lo
// bajado queda (cada archivo se renombra recién al terminar) y al volver a
// tocar el botón se retoma salteando lo que ya está.
let instalacion = { status: 'idle', archivo: null, hechos: 0, total: 0, porcentaje_archivo: 0, error: null };
let ultimoStopTs  = 0;
let intervaloHeartbeat = null;

function actualizarProgreso(cambios) {
  if (!corridaActual) return;
  corridaActual.progreso = { ...corridaActual.progreso, ...cambios };
}

function refrescarHeartbeat() {
  if (!corridaActual) return;
  escribirEstadoAtomico({
    status: 'corriendo',
    since: corridaActual.desde,
    heartbeat: new Date().toISOString(),
    progreso: corridaActual.progreso,
  });
}

// ── Resolución del alcance (manga completo / rango / capítulo único) ───────
function resolverAlcance(carpetaManga, alcance) {
  const todos = listDirNames(carpetaManga); // ya viene con orden natural (1, 2, 10, no 1, 10, 2)
  if (!todos.length) throw new Error('Ese manga no tiene capítulos en disco.');

  if (alcance?.tipo === 'capitulo') {
    if (!todos.includes(alcance.capitulo)) throw new Error(`No existe el capítulo "${alcance.capitulo}".`);
    return [alcance.capitulo];
  }
  if (alcance?.tipo === 'rango') {
    const desde = parseFloat(alcance.desde);
    const hasta = parseFloat(alcance.hasta);
    if (Number.isNaN(desde) || Number.isNaN(hasta)) throw new Error('Rango de capítulos inválido.');
    return todos.filter(c => { const n = parseFloat(c); return n >= desde && n <= hasta; });
  }
  if (alcance?.tipo === 'lista') {
    if (!Array.isArray(alcance.capitulos) || !alcance.capitulos.length) throw new Error('La lista de capítulos está vacía.');
    const desconocidos = alcance.capitulos.filter(c => !todos.includes(c));
    if (desconocidos.length) throw new Error(`No existen estos capítulos: ${desconocidos.join(', ')}.`);
    // Se devuelve en el orden natural de "todos" (no en el orden en que se
    // clickearon en el panel) para procesar siempre de menor a mayor.
    return todos.filter(c => alcance.capitulos.includes(c));
  }
  return todos; // 'manga' completo
}

// Camino rápido para no releer ni un solo header de imagen cuando ya
// sabemos, por el propio registro, que este capítulo quedó completo a un
// ancho igual o mayor Y al mismo formato que se está pidiendo ahora. Si
// cambia cualquiera de los dos, no se salta acá — clasificarPaginas se va
// a dar cuenta solo de qué páginas hacen falta.
function yaEstaAlDia(carpetaManga, capitulo, anchoObjetivo, formato) {
  const registro = upscaler.leerRegistro(carpetaManga);
  const ia = registro[capitulo]?.ia;
  return ia?.estado === 'completado' && ia.formato === formato && (ia.ancho_objetivo || 0) >= anchoObjetivo;
}

// ── GET /modelos ─────────────────────────────────────────────────────────
router.get('/modelos', requireAdmin, (req, res) => {
  res.json({
    modelos: upscaler.MODELOS_DISPONIBLES,
    default: upscaler.MODELO_DEFAULT,
    formatos: upscaler.FORMATOS_DISPONIBLES,
    formatoDefault: upscaler.FORMATO_DEFAULT,
    motorInstalado: upscaler.motorInstalado(),
    motorFaltantes: upscaler.motorFaltantes().length,
  });
});

// ── GET /capitulos ───────────────────────────────────────────────────────
// Alimenta la lista visual de capítulos del panel (estado de descarga +
// estado de mejora IA de cada uno) — es lo que le permite a Martin elegir
// el alcance de la corrida tocando capítulos en esa misma lista, en vez de
// un selector aparte.
router.get('/capitulos', requireAdmin, (req, res) => {
  const { manga } = req.query;
  if (!manga) return res.status(400).json({ error: 'Falta indicar el manga.' });
  const rutaManga = rutaDeManga(manga);
  if (!rutaManga) return res.status(404).json({ error: 'No se encontró ese manga en la biblioteca.' });

  let capitulos;
  try { capitulos = listDirNames(rutaManga); }
  catch (e) { return res.status(500).json({ error: e.message }); }

  const registro = upscaler.leerRegistro(rutaManga);
  const resultado = capitulos.map((capitulo) => {
    const entrada = registro[capitulo] || {};
    return {
      capitulo,
      estado: entrada.estado || null,           // estado de DESCARGA (scraper) — eje independiente del de ia
      esperadas: entrada.esperadas ?? null,
      validas: entrada.validas ?? null,
      ia: entrada.ia || null,                    // null = todavía nunca se corrió una mejora sobre este capítulo
    };
  });
  res.json({ capitulos: resultado });
});

// ── GET /status ──────────────────────────────────────────────────────────
router.get('/status', requireAdmin, (req, res) => res.json(leerEstado()));

// ── GET /stream (SSE) ────────────────────────────────────────────────────
router.get('/stream', requireAdmin, (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    // "no-transform" es lo que hace que el middleware de compresión de index.js
    // NO comprima este stream: gzip junta bytes en un buffer antes de mandarlos,
    // y un SSE necesita que cada mensaje llegue al instante (sin esto la consola
    // en vivo se quedaba vacía aunque el servidor sí mandaba los eventos). Así
    // no hace falta tocar el filtro de compresión de index.js.
    'Cache-Control': 'no-cache, no-transform',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.flushHeaders();
  req.socket.setNoDelay(true);

  res.write(`event: state\ndata: ${JSON.stringify(leerEstado())}\n\n`);
  for (const linea of logBuffer) {
    try { res.write(`event: log\ndata: ${JSON.stringify(linea)}\n\n`); } catch {}
  }

  sseClientes.add(res);
  req.on('close', () => sseClientes.delete(res));
});

// ── POST /instalar-motor ─────────────────────────────────────────────────
// Descarga el binario y los modelos desde el repo oficial de Upscayl (solo
// URLs fijas de motorInstaller.js — no se le pasa nada del request). El
// avance viaja por el mismo 'state' y 'log' del SSE que usa el resto del panel.
router.post('/instalar-motor', requireAdmin, (req, res) => {
  if (instalacion.status === 'instalando') return res.status(409).json({ error: 'Ya se está instalando el motor.' });
  if (upscaler.motorInstalado()) return res.status(409).json({ error: 'El motor ya está instalado.' });

  instalacion = { status: 'instalando', archivo: null, hechos: 0, total: 0, porcentaje_archivo: 0, error: null };
  res.json({ ok: true });

  motorInstaller.instalarMotor({
    onLog: log,
    onProgreso: (p) => { instalacion = { ...instalacion, ...p }; },
  })
    .then(() => { instalacion = { ...instalacion, status: 'listo', archivo: null, error: null }; })
    .catch((e) => {
      instalacion = { ...instalacion, status: 'error', error: e.message };
      log(`❌ La instalación del motor falló: ${e.message}`);
    });
});

// ── POST /start ──────────────────────────────────────────────────────────
router.post('/start', requireAdmin, (req, res) => {
  const { carpetaManga, alcance, modelo, anchoObjetivo, formato, tileSize, gpuId } = req.body || {};

  if (!carpetaManga) return res.status(400).json({ error: 'Falta indicar el manga.' });
  const rutaManga = rutaDeManga(carpetaManga);
  if (!rutaManga) return res.status(404).json({ error: 'No se encontró ese manga en la biblioteca.' });

  const ancho = parseInt(anchoObjetivo, 10);
  if (!Number.isInteger(ancho) || ancho <= 0) return res.status(400).json({ error: 'El ancho objetivo tiene que ser un número entero positivo.' });

  const modeloElegido = modelo || upscaler.MODELO_DEFAULT;
  if (!upscaler.MODELOS_DISPONIBLES.some(m => m.id === modeloElegido)) {
    return res.status(400).json({ error: `Modelo desconocido: "${modeloElegido}".` });
  }
  const formatoElegido = formato || upscaler.FORMATO_DEFAULT;
  if (!upscaler.FORMATOS_DISPONIBLES.some(f => f.id === formatoElegido)) {
    return res.status(400).json({ error: `Formato desconocido: "${formatoElegido}".` });
  }

  if (!upscaler.motorInstalado()) {
    return res.status(409).json({ error: 'El motor de IA no está instalado — corré el script de instalación primero.' });
  }
  if (corridaActual) return res.status(409).json({ error: 'Ya hay una mejora con IA corriendo.' });
  if (fs.existsSync(SCRAPER_LOCK_PATH)) {
    return res.status(409).json({ error: 'El scraper está corriendo — esperá a que termine antes de mejorar con IA.' });
  }
  const faltanCooldown = COOLDOWN_MS - (Date.now() - ultimoStopTs);
  if (faltanCooldown > 0) return res.status(429).json({ error: `Esperá ${Math.ceil(faltanCooldown / 1000)}s antes de iniciar de nuevo.` });

  let capitulos;
  try {
    capitulos = resolverAlcance(rutaManga, alcance).filter(c => !yaEstaAlDia(rutaManga, c, ancho, formatoElegido));
  } catch (e) {
    return res.status(400).json({ error: e.message });
  }
  if (!capitulos.length) {
    return res.status(409).json({ error: 'No hay nada para mejorar: todos los capítulos de ese alcance ya están al ancho y formato pedidos (o mejor).' });
  }

  res.json({ ok: true, capitulos: capitulos.length });

  correrCola(rutaManga, capitulos, { modelo: modeloElegido, anchoObjetivo: ancho, formato: formatoElegido, tileSize, gpuId })
    .catch(e => console.error('[ia] la corrida terminó con un error inesperado:', e));
});

// ── POST /stop ───────────────────────────────────────────────────────────
// Dos modos, como se acordó:
//  - 'graceful' (default): termina las páginas que le falten al capítulo
//    ACTUAL con normalidad, y no arranca el siguiente.
//  - 'dura': cancela la página que esté procesándose en ese instante; el
//    capítulo en curso queda "incompleto" y se retoma solo la próxima vez.
router.post('/stop', requireAdmin, (req, res) => {
  if (!corridaActual) return res.status(409).json({ error: 'No hay ninguna mejora corriendo.' });

  ultimoStopTs = Date.now();
  const modo = req.body?.modo === 'dura' ? 'dura' : 'graceful';
  corridaActual.banderaDetener = modo;

  if (modo === 'dura' && corridaActual.cancelarImagenActual) {
    corridaActual.cancelarImagenActual();
  }

  log(modo === 'dura'
    ? '⏹️  Parada solicitada: se corta la página en curso, el capítulo queda incompleto.'
    : '⏳ Parada solicitada: se termina el capítulo actual y no arranca el siguiente.');

  res.json({ ok: true, modo });
});

// ── El worker: recorre capítulos y, dentro de cada uno, páginas ───────────
async function correrCola(carpetaManga, capitulos, opciones) {
  const control = { banderaDetener: null, cancelarImagenActual: null, desde: new Date().toISOString(), progreso: null };
  corridaActual = control;
  intervaloHeartbeat = setInterval(refrescarHeartbeat, HEARTBEAT_TICK_MS);

  // try/finally: si algo revienta a mitad de camino, igual se limpia el estado.
  // Sin esto, la corrida quedaba "corriendo" para siempre en el panel y no se
  // podía volver a iniciar hasta reiniciar el servidor.
  try {
    log(`🚀 Arrancando mejora IA — ${capitulos.length} capítulo(s) · modelo "${opciones.modelo}" · ancho objetivo ${opciones.anchoObjetivo}px · formato ${opciones.formato}`);

    let capitulosHechos = 0;

    for (const capitulo of capitulos) {
      if (control.banderaDetener) {
        log('⏹️  Detenido — no se arranca el siguiente capítulo.');
        break;
      }

      const carpetaCapitulo = path.join(carpetaManga, capitulo);
      const fechaInicioCap  = new Date().toISOString();

      upscaler.actualizarIaDeCapitulo(carpetaManga, capitulo, {
        estado: 'en_progreso',
        modelo: opciones.modelo,
        ancho_objetivo: opciones.anchoObjetivo,
        formato: opciones.formato,
        fecha_inicio: fechaInicioCap,
        ultima_actualizacion: fechaInicioCap,
      });
      // Campos del avance (los usa el panel):
      //  - capítulo: paginas_total / paginas_hechas / pagina_indice (en cuál va)
      //  - general:  capitulos_hechos / capitulos_total
      //  - detalle:  pagina_actual + porcentaje_pagina (% de ESA imagen)
      actualizarProgreso({
        carpeta_manga: carpetaManga, capitulo_actual: capitulo,
        capitulos_hechos: capitulosHechos, capitulos_total: capitulos.length,
        paginas_total: null, paginas_hechas: 0, pagina_indice: null,
        pagina_actual: null, porcentaje_pagina: 0,
      });
      refrescarHeartbeat();

      let clasificacion;
      try {
        clasificacion = await upscaler.clasificarPaginas(carpetaCapitulo, opciones.anchoObjetivo, opciones.formato);
      } catch (e) {
        log(`❌ No se pudo leer el capítulo "${capitulo}": ${e.message}`);
        upscaler.actualizarIaDeCapitulo(carpetaManga, capitulo, {
          estado: 'incompleto', ultima_actualizacion: new Date().toISOString(),
          nota: `No se pudo leer el capítulo: ${e.message}`,
        });
        capitulosHechos++;
        actualizarProgreso({ capitulos_hechos: capitulosHechos });
        continue;
      }

      const total = clasificacion.total;
      log(`📖 Capítulo ${capitulo}: ${total} página(s) — ${clasificacion.pendientes.length} por mejorar, ${clasificacion.omitidas.length} ya al ancho/formato pedido.`);

      let mejoradas = 0;
      let fallidas = 0;
      let interrumpido = false;
      // "hechas" cuenta también las que ya estaban bien (omitidas): la barra del
      // capítulo representa TODAS las páginas del capítulo, no solo las que hay que tocar.
      const hechas = () => clasificacion.omitidas.length + mejoradas + fallidas;
      actualizarProgreso({ paginas_total: total, paginas_hechas: hechas() });

      for (const pagina of clasificacion.pendientes) {
        if (control.banderaDetener === 'dura') { interrumpido = true; break; }

        const etiqueta = `[${pagina.indice}/${total}] ${pagina.nombre}`;
        actualizarProgreso({ pagina_actual: pagina.nombre, pagina_indice: pagina.indice, porcentaje_pagina: 0, paginas_hechas: hechas() });
        log(`🖼️  ${etiqueta} — ${pagina.ancho}px → ${opciones.anchoObjetivo}px ...`);
        const inicioPagina = Date.now();

        const { cancelar, promesa } = upscaler.mejorarImagen({
          rutaOriginal: path.join(carpetaCapitulo, pagina.nombre),
          anchoObjetivo: opciones.anchoObjetivo,
          modelo: opciones.modelo,
          formato: opciones.formato,
          raizBiblioteca: path.dirname(carpetaManga),
          tileSize: opciones.tileSize,
          gpuId: opciones.gpuId,
          onProgreso: (pct) => actualizarProgreso({ porcentaje_pagina: pct }),
        });
        control.cancelarImagenActual = cancelar;

        try {
          await promesa;
          mejoradas++;
          log(`✅ ${etiqueta} lista en ${((Date.now() - inicioPagina) / 1000).toFixed(1)}s`);
        } catch (e) {
          control.cancelarImagenActual = null;
          if (e.cancelado) { interrumpido = true; break; }
          fallidas++;
          log(`⚠️  Falló ${etiqueta} (se sigue con las demás): ${e.message}`);
          actualizarProgreso({ paginas_hechas: hechas() });
          continue;
        }
        control.cancelarImagenActual = null;
        actualizarProgreso({ paginas_hechas: hechas(), porcentaje_pagina: 100 });
      }

      capitulosHechos++;
      actualizarProgreso({ capitulos_hechos: capitulosHechos });
      // Un capítulo con páginas que fallaron NO está completo: queda "incompleto"
      // para que la próxima corrida lo reintente (antes se marcaba "completado").
      const quedoIncompleto = interrumpido || fallidas > 0;
      const ahora = new Date().toISOString();
      upscaler.actualizarIaDeCapitulo(carpetaManga, capitulo, {
        estado: quedoIncompleto ? 'incompleto' : 'completado',
        paginas_totales: total,
        mejoradas,
        omitidas: clasificacion.omitidas.length,
        pendientes: clasificacion.pendientes.length - mejoradas,
        fecha_fin: quedoIncompleto ? null : ahora,
        ultima_actualizacion: ahora,
      });

      if (interrumpido) {
        log(`⏸️  Capítulo ${capitulo} quedó incompleto (${hechas()}/${total} páginas) — se retoma solo en la próxima corrida.`);
        break;
      }
      log(quedoIncompleto
        ? `⚠️  Capítulo ${capitulo} terminó con ${fallidas} página(s) fallidas — queda incompleto y se reintenta en la próxima corrida.`
        : `✅ Capítulo ${capitulo} completo — ${mejoradas} mejorada(s), ${clasificacion.omitidas.length} omitida(s). Van ${capitulosHechos}/${capitulos.length} capítulos.`);
    }
  } finally {
    clearInterval(intervaloHeartbeat);
    corridaActual = null;
    escribirEstadoAtomico({ status: 'idle', since: null, heartbeat: new Date().toISOString(), progreso: null });
    difundir('state', leerEstado());
    log('🏁 Corrida de mejora IA finalizada.');
  }
}

module.exports = router;
