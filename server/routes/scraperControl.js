const express = require('express');
const fs      = require('fs');
const path    = require('path');
const { spawn, exec } = require('child_process');

const router = express.Router();

// ── Rutas y configuración ───────────────────────────────────────────────────
const SCRAPER_DIR        = path.join(__dirname, '../../scraper');
const SCRAPER_SCRIPT      = 'scraper.py';
const ESTADO_VIVO_PATH    = path.join(SCRAPER_DIR, 'estado_vivo.json');
const DETENER_FLAG_PATH   = path.join(SCRAPER_DIR, 'detener.flag');
const LOG_PATH            = path.join(SCRAPER_DIR, 'scraper.log');
const LOCK_PATH           = path.join(SCRAPER_DIR, 'scraper.lock');
const SEGUIMIENTO_PATH    = path.join(SCRAPER_DIR, 'seguimiento.json');

// Mismo orden y mismos íconos que ORDEN_FUENTES / el mapeo de íconos que usa
// scraper.py en los headers del escaneo — se duplica acá a propósito: es
// una lista fija de 9 fuentes que casi no cambia, y mantenerla en Node
// evita depender de invocar Python solo para listar nombres.
const ORDEN_FUENTES = ['olympus', 'nexus', 'temple', 'dragon', 'ikigai', 'taurus', 'leercapitulo', 'manhwaweb', 'tmo'];
const ICONO_FUENTE  = { olympus:'🏛', nexus:'🔗', temple:'🏯', dragon:'🐉', manhwaweb:'📚', ikigai:'🌸', leercapitulo:'📕', taurus:'🐂', tmo:'📙' };

const PYTHON_BIN          = process.env.PYTHON_BIN || 'python';
const UMBRAL_HEARTBEAT_MS = 2 * 60 * 1000;  // heartbeat más viejo que esto = se asume caído (el scraper lo refresca cada ~30s como mucho, incluso en la espera larga del modo continuo)
const COOLDOWN_MS         = 15 * 1000;       // no dejar iniciar de nuevo tan rápido tras un stop (protege de spam/abuso del botón)
const ESPERA_CHEQUEO_ARRANQUE_MS = 1200;     // cuánto esperar tras spawnear antes de contestar, para pescar un fallo inmediato (lock ocupado, python no encontrado)

// Referencia al proceso, SOLO para saber si lo arrancó ESTA instancia de
// Node — se usa nada más que para distinguir "lo prendí yo" de "lo
// prendieron a mano" en /status. El estado real (¿está corriendo?, ¿qué
// está haciendo?) siempre se lee de estado_vivo.json, nunca de esta
// variable — así, si Martín corre el scraper a mano desde VS Code, /status
// lo refleja igual, y el server puede reiniciarse sin perder de vista un
// escaneo que ya estaba corriendo.
let procesoPropio = null;  // { proc, pid }
let ultimoStopTs  = 0;

// ── Tailing compartido de scraper.log + difusión a los navegadores conectados
// ────────────────────────────────────────────────────────────────────────────
// Antes cada pestaña abierta tenía su propio setInterval tailenado el mismo
// archivo — funcionaba, pero significaba que sin ningún navegador conectado
// no había NINGÚN lugar donde ver actividad (ni la app, ni la terminal,
// desde que se sacó el eco crudo de stdout por el problema de spam de
// tqdm). Ahora hay un único tailer que arranca solo con el server: manda
// cada línea nueva y limpia (la misma que ya recibía scraper.log, sin nada
// de ruido de tqdm) tanto a la terminal de Node como a quien esté
// conectado por SSE en ese momento. Así la terminal vuelve a mostrar qué
// está haciendo el scraper — igual que antes — pero sin el problema
// original, y sirve como respaldo si algo more falla del lado del navegador.
const sseClientes = new Set();
let logOffset  = 0;
let restoLinea = '';

// Buffer en memoria de las últimas líneas ya emitidas en ESTE proceso del
// server (se resetea solo si el server se reinicia — no vale la pena
// persistirlo a disco, scraper.log sigue siendo la fuente real de verdad).
// Se reproduce a cualquier cliente SSE que se conecte, para que salir del
// panel y volver — o entrar desde otro dispositivo — no arranque la consola
// en blanco perdiendo todo lo que ya pasó en el ciclo actual. Mismo tope
// que usa el cliente para las líneas que mantiene en el DOM (ver
// appendLog() en scraper.html) — no tiene sentido guardar más acá de lo
// que el cliente va a terminar mostrando de todos modos.
const LOG_BUFFER_MAX = 500;
const logBuffer = [];

try { logOffset = fs.statSync(LOG_PATH).size; } catch {}

function difundir(evento, data) {
  if (evento === 'log') {
    logBuffer.push(data);
    if (logBuffer.length > LOG_BUFFER_MAX) logBuffer.shift();
  }
  const linea = `event: ${evento}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClientes) {
    try { res.write(linea); } catch {}
  }
}

setInterval(() => {
  let stat;
  try { stat = fs.statSync(LOG_PATH); } catch { return; }
  if (stat.size < logOffset) logOffset = 0;   // rotó (RotatingFileHandler, tope 10MB)
  if (stat.size === logOffset) return;        // nada nuevo

  const stream = fs.createReadStream(LOG_PATH, { start: logOffset, end: stat.size - 1, encoding: 'utf-8' });
  let buffer = '';
  stream.on('data', chunk => { buffer += chunk; });
  stream.on('end', () => {
    logOffset = stat.size;
    const texto  = restoLinea + buffer;
    const lineas = texto.split('\n');
    restoLinea = lineas.pop() ?? '';
    for (const linea of lineas) {
      const limpia = linea.replace(/\r$/, '');
      if (!limpia.trim()) continue;
      console.log('[scraper]', limpia);
      difundir('log', limpia);
    }
  });
  stream.on('error', () => {});
}, 800);

let ultimoEstadoTexto = null;
setInterval(() => {
  const actual = leerEstado();
  const texto  = JSON.stringify(actual);
  if (texto !== ultimoEstadoTexto) {
    ultimoEstadoTexto = texto;
    difundir('state', actual);
  }
}, 2000);

function requireAdmin(req, res, next) {
  if (req.user?.role !== 'admin') return res.status(403).json({ error: 'Sin permiso.' });
  next();
}

// Mismo patrón atómico que usa Python (guardar_seguimiento, ControlEjecucion):
// escribir a un .tmp y reemplazar, para que un GET /status concurrente o el
// poll del stream nunca lean un JSON a mitad de escribir.
function escribirEstadoAtomico(payload) {
  const tmp = ESTADO_VIVO_PATH + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(payload));
  fs.renameSync(tmp, ESTADO_VIVO_PATH);
}

// Mismo patrón atómico que usa Python (guardar_seguimiento) para
// seguimiento.json: escribir a un .tmp y reemplazar. Se usa SOLO para
// /fuentes, y solo con el scraper detenido (ver POST /fuentes) — evita
// pisar en carrera los guardar_seguimiento(data) que Python hace en
// memoria durante un ciclo activo.
function leerSeguimiento() {
  return JSON.parse(fs.readFileSync(SEGUIMIENTO_PATH, 'utf-8'));
}
function guardarSeguimientoAtomico(data) {
  const tmp = SEGUIMIENTO_PATH + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
  fs.renameSync(tmp, SEGUIMIENTO_PATH);
}

// ── Lectura de estado ────────────────────────────────────────────────────────
function leerEstado() {
  let estado;
  try {
    estado = JSON.parse(fs.readFileSync(ESTADO_VIVO_PATH, 'utf-8'));
  } catch {
    // No existe todavía (nunca corrió) o quedó corrupto a mitad de escritura
    // (muy improbable, del lado Python la escritura es atómica) — se trata
    // igual que "nunca corrió", no como un error para el usuario.
    estado = { status: 'idle', pid: null, since: null, heartbeat: null, progress: null, nextCycleAt: null };
  }

  // Detección de caída: si el archivo dice "corriendo" pero el heartbeat es
  // viejo, el proceso murió sin pasar por ControlEjecucion.finalizar() (corte
  // de luz, terminal cerrada de un hachazo, kill externo). Se reporta como
  // detenido en vez de dejar la UI trabada mostrando "corriendo" para
  // siempre. El umbral (2min) tiene margen de sobra: el scraper refresca el
  // heartbeat como mucho cada ~30s, incluso durante la espera de una hora
  // entre ciclos del modo continuo.
  let caidaDetectada = false;
  if (estado.status !== 'idle' && estado.heartbeat) {
    const edadMs = Date.now() - new Date(estado.heartbeat).getTime();
    if (edadMs > UMBRAL_HEARTBEAT_MS) {
      caidaDetectada = true;
      estado = { ...estado, status: 'idle', progress: null };
    }
  }

  return {
    ...estado,
    startedByExternal: estado.status !== 'idle' && !procesoPropio,
    caidaDetectada,
  };
}

// ── GET /reporte ─────────────────────────────────────────────────────────────
// Sirve el reporte.html que el propio scraper ya genera y acumula solo (ver
// ReporteEscaneo en scraper.py) — no hace falta construir nada nuevo del
// lado de la app, solo entregarlo. Se abre con target="_blank" desde un
// <a href>, no un fetch(), así que llega SIN el header Authorization — por
// eso esta ruta depende de que el router entero esté montado detrás de
// cookieOrHeaderAuth (ver index.js), que sí acepta la cookie que el
// navegador manda solo por estar en una navegación normal.
router.get('/reporte', requireAdmin, (req, res) => {
  const reportePath = path.join(SCRAPER_DIR, 'reporte.html');
  if (!fs.existsSync(reportePath)) {
    return res.status(404).send('Todavía no se generó ningún reporte — corré al menos un escaneo primero.');
  }
  res.sendFile(reportePath);
});

// ── GET /status ────────────────────────────────────────────────────────────
router.get('/status', requireAdmin, (req, res) => {
  res.json(leerEstado());
});

// ── POST /start ────────────────────────────────────────────────────────────
router.post('/start', requireAdmin, (req, res) => {
  const modo = req.body?.mode;
  if (modo !== 'once' && modo !== 'continuous') {
    return res.status(400).json({ error: "mode debe ser 'once' o 'continuous'." });
  }

  const estado = leerEstado();
  if (estado.status !== 'idle') {
    return res.status(409).json({ error: 'Ya hay un escaneo corriendo.' });
  }
  const faltanCooldown = COOLDOWN_MS - (Date.now() - ultimoStopTs);
  if (faltanCooldown > 0) {
    return res.status(429).json({ error: `Esperá ${Math.ceil(faltanCooldown / 1000)}s antes de iniciar de nuevo.` });
  }

  const args = modo === 'once' ? [SCRAPER_SCRIPT, '--una-vez'] : [SCRAPER_SCRIPT];
  let proc;
  try {
    proc = spawn(PYTHON_BIN, args, {
      cwd: SCRAPER_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      // El propio scraper.py ya se reconfigura a UTF-8 al arrancar (ver el
      // inicio del archivo), pero esta es una segunda red de seguridad:
      // fuerza el encoding correcto desde ANTES de que Python llegue a
      // ejecutar esa línea, por si algún intérprete de Python se comporta
      // distinto en el primer instante de arranque.
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
  } catch (e) {
    return res.status(500).json({ error: `No se pudo iniciar: ${e.message}` });
  }

  procesoPropio = { proc, pid: proc.pid };

  let stderrCapturado = '';
  let salioInmediato   = false;
  let yaRespondido      = false;

  // Passthrough CRUDO — nada de console.log/warn acá. console.log agrega
  // su propio salto de línea a cada llamada, así que envolver los chunks
  // ahí adentro rompía justo lo que tqdm necesita: el \r (retorno de
  // carro, sin salto de línea) que usa para PISAR la misma línea y
  // redibujar la barra de progreso en el lugar. Escribiendo el chunk tal
  // cual, byte por byte, es la propia terminal de Windows la que
  // interpreta el \r — mismo resultado que corriendo el scraper a mano.
  // Esto también trae de vuelta la tabla de imágenes (resolución,
  // aceptada/rechazada) que el scraper imprime con tqdm.write() directo,
  // sin pasar por el logger — por eso nunca aparecía ni en scraper.log ni
  // en la consola en vivo de la app, esa es información que solo vive acá.
  proc.stdout.on('data', d => process.stdout.write(d));
  proc.stderr.on('data', d => {
    stderrCapturado += d.toString();  // para el mensaje de /start si falla al toque
    process.stderr.write(d);          // y también se ve en vivo, tal cual
  });

  proc.on('error', (e) => {
    salioInmediato = true;
    stderrCapturado = e.message;
    procesoPropio = null;
  });

  proc.on('exit', (code) => {
    if (procesoPropio?.pid === proc.pid) procesoPropio = null;
    if (code && code !== 0 && !yaRespondido) salioInmediato = true;
  });

  // Esperar un toque antes de contestar — así se pesca un fallo inmediato
  // (lock ocupado por otra corrida, "python" no encontrado en PATH, falta
  // seguimiento.json) en vez de contestar {ok:true} y que el usuario recién
  // se entere mirando la consola en vivo unos segundos después.
  setTimeout(() => {
    yaRespondido = true;
    if (salioInmediato) {
      res.status(500).json({ error: stderrCapturado.trim().slice(-500) || 'El scraper se cerró apenas arrancar — revisá el historial para más detalle.' });
    } else {
      res.json({ ok: true });
    }
  }, ESPERA_CHEQUEO_ARRANQUE_MS);
});

// ── POST /stop ─────────────────────────────────────────────────────────────
router.post('/stop', requireAdmin, (req, res) => {
  const estado = leerEstado();
  if (estado.status === 'idle') {
    return res.status(409).json({ error: 'No hay nada corriendo.' });
  }

  ultimoStopTs = Date.now();

  if (req.body?.force) {
    const pid = estado.pid;
    if (!pid) return res.status(500).json({ error: 'No se encontró el PID para forzar la detención.' });

    // Windows-específico (este server corre en Windows 11): taskkill /T
    // mata también los procesos hijos — Brave/ChromeDriver que Selenium
    // pueda tener abiertos, no solo el intérprete de Python.
    exec(`taskkill /F /T /PID ${pid}`, (err) => {
      if (err) {
        console.warn('[scraper] taskkill falló (¿ya no existía el proceso?):', err.message);
        return;
      }
      // A diferencia de un stop prolijo, acá Python nunca llega a su
      // finally (_liberar_lock / CONTROL.finalizar) porque lo matamos de
      // afuera. Como Node acaba de confirmar la muerte del proceso (no es
      // una sospecha por heartbeat viejo, es un hecho), es seguro limpiar
      // el lock y el estado acá mismo — si no, _adquirir_lock() del lado
      // Python no revisa si el PID sigue vivo, solo la antigüedad del
      // archivo (6h de margen), y quedarías sin poder reiniciar desde la
      // app hasta que pase ese tiempo.
      try { fs.unlinkSync(LOCK_PATH); } catch {}
      try { fs.unlinkSync(DETENER_FLAG_PATH); } catch {}
      try {
        escribirEstadoAtomico({
          status: 'idle', pid: null, since: null,
          heartbeat: new Date().toISOString(),
          progress: null, nextCycleAt: null,
          lastStoppedManually: true,
        });
      } catch (e) { console.warn('[scraper] no se pudo limpiar estado_vivo.json tras forzar:', e.message); }
      procesoPropio = null;
    });
    return res.json({ ok: true, forced: true });
  }

  try {
    fs.writeFileSync(DETENER_FLAG_PATH, String(Date.now()));
  } catch (e) {
    return res.status(500).json({ error: `No se pudo escribir la señal de detener: ${e.message}` });
  }
  res.json({ ok: true, forced: false });
});

// ── GET /stream (SSE) ────────────────────────────────────────────────────────
router.get('/stream', requireAdmin, (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.flushHeaders();
  req.socket.setNoDelay(true);  // sin esto, TCP puede retener escrituras chicas un ratito antes de mandarlas — de más ahora que no hay compresión, pero no cuesta nada

  res.write(`event: state\ndata: ${JSON.stringify(leerEstado())}\n\n`);

  // Reproducir lo que ya se generó en este proceso del server — sin esto,
  // cada conexión nueva (volver a entrar al panel, o entrar desde otro
  // dispositivo) arrancaba la consola en blanco y solo mostraba líneas a
  // partir de ese instante, perdiendo todo lo que ya había pasado en el
  // ciclo en curso.
  if (logBuffer.length) {
    for (const linea of logBuffer) {
      try { res.write(`event: log\ndata: ${JSON.stringify(linea)}\n\n`); } catch {}
    }
    try { res.write(`event: log\ndata: ${JSON.stringify('── hasta acá, lo que ya había pasado — desde acá, en vivo ──')}\n\n`); } catch {}
  }

  sseClientes.add(res);
  req.on('close', () => sseClientes.delete(res));
});

// ── GET /fuentes ──────────────────────────────────────────────────────────
router.get('/fuentes', requireAdmin, (req, res) => {
  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }

  const cfg = data.fuentes_activas || {};
  const fuentes = ORDEN_FUENTES.map(f => ({
    fuente: f,
    icono: ICONO_FUENTE[f] || '📖',
    activa: cfg[f] !== false,   // opt-out: lo que no aparece en el dict se asume activo
  }));
  res.json({ fuentes });
});

// ── POST /fuentes ─────────────────────────────────────────────────────────
// Solo permite guardar con el scraper detenido (idle). Cambiar
// fuentes_activas mientras hay un ciclo corriendo entra en carrera con los
// guardar_seguimiento(data) que Python hace repetidamente EN MEMORIA
// durante ese mismo ciclo (cada capítulo/manga) — Python terminaría
// pisando el cambio de vuelta al valor con el que arrancó el ciclo en su
// próximo guardado de rutina, y el toggle parecería "revertirse solo".
// Bloqueando acá el cambio queda firme y aplica recién al arrancar el
// próximo escaneo — sin ninguna carrera posible.
router.post('/fuentes', requireAdmin, (req, res) => {
  const cambios = req.body?.fuentes;
  if (!cambios || typeof cambios !== 'object' || Array.isArray(cambios)) {
    return res.status(400).json({ error: "Body debe tener 'fuentes': { nombreFuente: boolean, ... }" });
  }
  for (const [f, v] of Object.entries(cambios)) {
    if (!ORDEN_FUENTES.includes(f)) return res.status(400).json({ error: `Fuente desconocida: '${f}'` });
    if (typeof v !== 'boolean') return res.status(400).json({ error: `El valor de '${f}' debe ser true o false.` });
  }

  const estado = leerEstado();
  if (estado.status !== 'idle') {
    return res.status(409).json({ error: 'No se puede cambiar fuentes activas mientras el scraper está corriendo — esperá a que termine o hacé Detener. El cambio aplica desde el próximo escaneo.' });
  }

  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }

  data.fuentes_activas = { ...(data.fuentes_activas || {}), ...cambios };

  try { guardarSeguimientoAtomico(data); }
  catch (e) { return res.status(500).json({ error: `No se pudo guardar seguimiento.json: ${e.message}` }); }

  const cfg = data.fuentes_activas;
  const fuentes = ORDEN_FUENTES.map(f => ({
    fuente: f,
    icono: ICONO_FUENTE[f] || '📖',
    activa: cfg[f] !== false,
  }));
  res.json({ ok: true, fuentes });
});

// ── GET /orden-fuentes ───────────────────────────────────────────────────
// El orden efectivo (personalizado si seguimiento.json trae 'orden_fuentes',
// si no el de fábrica) — mismo criterio de "opt-in" que fuentes_activas:
// si la clave no está, se usa el orden de siempre sin que nadie tenga que
// tocar nada.
router.get('/orden-fuentes', requireAdmin, (req, res) => {
  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }

  const personalizado = Array.isArray(data.orden_fuentes) ? data.orden_fuentes : null;
  const validas    = personalizado ? personalizado.filter(f => ORDEN_FUENTES.includes(f)) : [];
  const faltantes  = ORDEN_FUENTES.filter(f => !validas.includes(f));
  const orden      = personalizado ? [...validas, ...faltantes] : [...ORDEN_FUENTES];

  res.json({
    orden: orden.map(f => ({ fuente: f, icono: ICONO_FUENTE[f] || '📖' })),
    personalizado: !!personalizado,
  });
});

// ── POST /orden-fuentes ──────────────────────────────────────────────────
// Mismo bloqueo que POST /fuentes y el mismo motivo — ver el comentario
// largo ahí: guardar solo con el scraper idle evita entrar en carrera con
// los guardar_seguimiento(data) que Python hace en memoria durante un
// ciclo activo.
router.post('/orden-fuentes', requireAdmin, (req, res) => {
  // Modo 'resetear': el cliente no necesita conocer/duplicar cuál es el
  // orden de fábrica (ya está una sola vez acá arriba, en ORDEN_FUENTES)
  // — solo pide "volvé al de siempre" y el server resuelve el resto.
  const resetear = req.body?.resetear === true;
  const orden = resetear ? [...ORDEN_FUENTES] : req.body?.orden;

  if (!resetear) {
    if (!Array.isArray(orden) || orden.length !== ORDEN_FUENTES.length) {
      return res.status(400).json({ error: `Body debe tener 'orden': array con las ${ORDEN_FUENTES.length} fuentes.` });
    }
    const invalida = orden.find(f => !ORDEN_FUENTES.includes(f));
    if (invalida) return res.status(400).json({ error: `Fuente desconocida: '${invalida}'` });
    if (new Set(orden).size !== orden.length) {
      return res.status(400).json({ error: 'El orden no puede tener una fuente repetida.' });
    }
  }

  const estado = leerEstado();
  if (estado.status !== 'idle') {
    return res.status(409).json({ error: 'No se puede cambiar el orden mientras el scraper está corriendo — esperá a que termine o hacé Detener. El cambio aplica desde el próximo escaneo.' });
  }

  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }

  // Si el orden pedido coincide con el de fábrica, no hace falta guardar
  // nada especial — se saca la clave para que seguimiento.json quede
  // limpio (mismo criterio que "si no está, se usa el default solo").
  const esDefault = orden.every((f, i) => f === ORDEN_FUENTES[i]);
  if (esDefault) delete data.orden_fuentes;
  else data.orden_fuentes = orden;

  try { guardarSeguimientoAtomico(data); }
  catch (e) { return res.status(500).json({ error: `No se pudo guardar seguimiento.json: ${e.message}` }); }

  res.json({
    ok: true,
    orden: orden.map(f => ({ fuente: f, icono: ICONO_FUENTE[f] || '📖' })),
    personalizado: !esDefault,
  });
});

// ── Validación de dominio por fuente (ver POST/PUT /mangas) ────────────────
// A propósito NO vive en seguimiento.json ni tiene endpoint de escritura
// desde la web: si viviera en el mismo JSON que el editor puede tocar,
// cualquiera con acceso al panel podría agregar un dominio peligroso a la
// lista de "permitidos" y esta validación dejaría de servir para algo —
// dejaría de ser una barrera real. Vive en su propio archivo
// (scraper/dominios_fuente.json), que el server solo LEE; para cambiarlo
// hay que editar el archivo directo (o el DOMINIOS_FUENTE_DEFAULT de acá
// abajo, que se usa además como semilla la primera vez que el archivo no
// existe todavía).
//
// Dos casos con más de un valor válido, confirmados leyendo scraper.py Y
// un seguimiento.json real de producción:
//   - ikigai: rota de dominio a propósito (anti-bloqueo/DMCA, ver
//     IkigaiScraper). En los datos reales aparecen TRES dominios en uso
//     simultáneo con dos prefijos distintos ('visualikigai.' y
//     'visorikigai.') — se validan los dos por prefijo, no uno solo.
//   - temple: el scraper re-resuelve el dominio real por su cuenta en cada
//     descarga (consulta a Supabase, ver TempleScraper.FALLBACK_URL), así
//     que aunque acá quede como 'exacto', si el sitio migra de verdad no
//     rompe ninguna descarga en curso — como mucho bloquearía el alta de
//     mangas NUEVOS hasta actualizar el archivo.
const DOMINIOS_FUENTE_PATH = path.join(SCRAPER_DIR, 'dominios_fuente.json');

const DOMINIOS_FUENTE_DEFAULT = {
  olympus:      { tipo: 'exacto',  valores: ['olympusxyz.com'] },
  nexus:        { tipo: 'exacto',  valores: ['nexusscanlation.com'] },
  temple:       { tipo: 'exacto',  valores: ['aedexnox.akan01.com'] },
  dragon:       { tipo: 'exacto',  valores: ['dragontranslation.org'] },
  ikigai:       { tipo: 'prefijo', valores: ['visorikigai.', 'visualikigai.'] },
  taurus:       { tipo: 'exacto',  valores: ['lectortaurus.com'] },
  leercapitulo: { tipo: 'exacto',  valores: ['www.leercapitulo.co'] },
  manhwaweb:    { tipo: 'exacto',  valores: ['manhwaweb.com'] },
  tmo:          { tipo: 'exacto',  valores: ['zonatmo.org'] },
};

function obtenerDominiosFuente() {
  try {
    const disco = JSON.parse(fs.readFileSync(DOMINIOS_FUENTE_PATH, 'utf-8'));
    return { ...DOMINIOS_FUENTE_DEFAULT, ...disco };
  } catch {
    // No existe todavía (primera vez) o está corrupto -> sembrar con el
    // default y seguir. Esto es la ÚNICA escritura de este archivo que
    // hace el server, y siempre con el mismo contenido fijo del código
    // — no depende de nada que venga de una request.
    try {
      fs.writeFileSync(DOMINIOS_FUENTE_PATH, JSON.stringify(DOMINIOS_FUENTE_DEFAULT, null, 2), 'utf-8');
    } catch { /* si tampoco se puede escribir, seguimos igual con el default en memoria */ }
    return { ...DOMINIOS_FUENTE_DEFAULT };
  }
}

// null = URL válida para esa fuente. String = motivo del rechazo.
function validarDominioUrl(fuente, urlStr, dominiosFuente) {
  let hostname;
  try {
    const u = new URL(urlStr);
    if (u.protocol !== 'https:') return `La URL debe empezar con https:// (tiene '${u.protocol}')`;
    hostname = u.hostname.toLowerCase();
  } catch {
    return 'La URL no es válida.';
  }

  const regla = dominiosFuente[fuente];
  if (!regla) return null; // fuente sin regla configurada todavía -> no se bloquea

  const valores = (regla.valores || []).map(v => v.toLowerCase());
  const pasa = regla.tipo === 'prefijo'
    ? valores.some(v => hostname.startsWith(v))
    : valores.some(v => hostname === v);

  if (!pasa) {
    const listado = (regla.valores || []).join(' / ');
    return regla.tipo === 'prefijo'
      ? `El dominio no corresponde a '${fuente}' (debe empezar con alguno de: ${listado} — se recibió '${hostname}')`
      : `El dominio no corresponde a '${fuente}' (esperado: ${listado} — se recibió '${hostname}')`;
  }
  return null;
}

// ── Detección de duplicados en nombre_carpeta ───────────────────────────────
// Mismo criterio que ya se usa del lado Python para lo mismo: normalización
// Unicode (NFD + sacar tildes + sacar puntuación) porque un toLowerCase()
// simple no pesca variantes como "Solo Leveling" con o sin tildes/guiones.
function normalizarNombre(s) {
  return (s || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^\w\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// Similaridad por coeficiente de Dice sobre bigramas de caracteres — sin
// dependencias externas, misma idea que el difflib.SequenceMatcher que ya
// usa el scraper Python para esto (umbral ~0.72 de ese lado también).
function bigramas(s) {
  const out = [];
  for (let i = 0; i < s.length - 1; i++) out.push(s.slice(i, i + 2));
  return out;
}
function similaridad(a, b) {
  const A = bigramas(a), B = bigramas(b);
  if (!A.length || !B.length) return a === b ? 1 : 0;
  const conteoB = new Map();
  for (const bg of B) conteoB.set(bg, (conteoB.get(bg) || 0) + 1);
  let interseccion = 0;
  for (const bg of A) {
    const c = conteoB.get(bg) || 0;
    if (c > 0) { interseccion++; conteoB.set(bg, c - 1); }
  }
  return (2 * interseccion) / (A.length + B.length);
}
const UMBRAL_SIMILARIDAD = 0.72;

function buscarPosiblesDuplicados(nombreCarpeta, mangas, excluirNombre) {
  const norm = normalizarNombre(nombreCarpeta);
  const resultado = [];
  for (const m of mangas) {
    if (excluirNombre !== undefined && m.nombre_carpeta === excluirNombre) continue;
    const normExistente = normalizarNombre(m.nombre_carpeta);
    if (normExistente === norm) {
      resultado.push({ nombre_carpeta: m.nombre_carpeta, tipo: 'exacto' });
      continue;
    }
    const sim = similaridad(norm, normExistente);
    if (sim >= UMBRAL_SIMILARIDAD) {
      resultado.push({ nombre_carpeta: m.nombre_carpeta, tipo: 'similar', similaridad: Math.round(sim * 100) });
    }
  }
  return resultado;
}

// ── Derivar 'slug' desde 'url_manga' ────────────────────────────────────────
// Port 1:1 de normalizar_url_manga() en scraper.py (misma función, mismos
// patrones por fuente) — validado línea por línea contra los 125 mangas
// reales de un seguimiento.json de producción (125/125 coincidencias
// exactas contra el slug que Python ya había calculado). Python vuelve a
// correr esta misma lógica en cada escaneo igual, así que aunque acá haya
// algún caso raro que no se pueda derivar bien, se autocorrige solo en el
// próximo ciclo — pero para que el manga quede andando DESDE que se agrega
// (sin depender de esperar al próximo escaneo), esto se calcula ya mismo
// al guardar.
function derivarSlugDesdePath(fuente, path) {
  if (fuente === 'olympus') {
    const m = path.match(/series\/(comic-)?(.+)$/);
    return m ? 'comic-' + m[2] : null;
  }
  if (fuente === 'manhwaweb') {
    const m = path.match(/manhwa\/(.+)$/);
    return m ? m[1] : null;
  }
  if (fuente === 'nexus' || fuente === 'ikigai') {
    const m = path.match(/series\/(.+)$/);
    return m ? m[1].split('/')[0] : null;
  }
  if (fuente === 'temple' || fuente === 'dragon' || fuente === 'taurus') {
    const partes = path.split('/').filter(Boolean);
    if (!partes.length) return null;
    const prefijosConocidos = ['manga', 'serie', 'manhwa', 'comic', 'webtoon'];
    if (prefijosConocidos.includes(partes[0]) && partes.length > 1) return partes[1];
    return partes[partes.length - 1];
  }
  if (fuente === 'leercapitulo') {
    const partes = path.split('/').filter(Boolean);
    return (partes.length > 1 && partes[0] === 'manga') ? partes.slice(1).join('/') : null;
  }
  if (fuente === 'tmo') {
    const partes = path.split('/').filter(Boolean);
    // library/{tipo}/{id}/{slug} — {tipo} es la categoría de contenido de
    // TMO (manga, manhua, manhwa, y probablemente novela/one_shot/etc
    // también) y varía, NO es siempre literal "manga". Confirmado con 3
    // ejemplos reales (manga, manhua, manhwa) que Martin pasó — antes acá
    // se exigía partes[1]==='manga' a secas, heredado tal cual del mismo
    // chequeo (con el mismo bug) que tiene normalizar_url_manga() en
    // scraper.py, y por eso todo lo que no fuera "manga" fallaba.
    return (partes.length >= 4 && partes[0] === 'library') ? partes.slice(2).join('/') : null;
  }
  return null;
}

function derivarSlugDesdeUrl(fuente, urlStr) {
  let path;
  try {
    path = new URL(urlStr).pathname.replace(/^\/+|\/+$/g, '');
  } catch {
    return null;
  }
  return derivarSlugDesdePath(fuente, path) || null;
}

// ── Validación + normalización de un manga entrante (alta o edición) ───────
// ── Resolver manga_id automáticamente para Olympus ──────────────────────────
// Mismo mecanismo que ya usa scraper.py (OlympusScraper): pide la página
// pública de la serie y saca el ID numérico de la URL de la portada
// (patrón /comics/covers/{id}/). Es best-effort a propósito — si Olympus
// no responde o cambió el formato de la página, NO bloquea el guardado:
// Python igual lo termina de resolver (o reintenta) en el próximo
// escaneo, tal como ya hace hoy usando el manga_id persistido como
// respaldo cuando la portada no responde esa vez puntual.
async function resolverMangaIdOlympus(slugSinPrefijo) {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    const r = await fetch(`https://olympusxyz.com/series/comic-${slugSinPrefijo}`, {
      signal: controller.signal,
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; MangaServer/1.0)' },
    });
    clearTimeout(timeout);
    if (!r.ok) return null;
    const html = await r.text();
    const m = html.match(/\/comics\/covers\/(\d+)\//);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

async function validarYNormalizarManga(input, dominiosFuente, { esNuevo }) {
  const errores = [];
  const m = {};

  const nombre = String(input.nombre_carpeta || '').trim();
  if (!nombre) errores.push('nombre_carpeta es obligatorio.');
  m.nombre_carpeta = nombre;

  const fuente = String(input.fuente || '').trim();
  if (!ORDEN_FUENTES.includes(fuente)) errores.push(`fuente inválida: '${fuente}'`);
  m.fuente = fuente;

  const url = String(input.url_manga || '').trim();
  if (!url) {
    errores.push('url_manga es obligatorio.');
  } else if (ORDEN_FUENTES.includes(fuente)) {
    const err = validarDominioUrl(fuente, url, dominiosFuente);
    if (err) errores.push(err);
  }
  m.url_manga = url;

  // slug: si vino a mano en el body, se respeta tal cual (override manual,
  // para el campo "avanzado" del form de edición). Si no, se deriva de
  // url_manga con la misma lógica que scraper.py — así el manga queda
  // funcional desde que se agrega, sin depender de esperar al próximo
  // escaneo para que Python se lo calcule.
  const slugManual = input.slug !== undefined ? String(input.slug || '').trim() : '';
  if (slugManual) {
    m.slug = slugManual;
  } else if (m.url_manga && ORDEN_FUENTES.includes(fuente)) {
    const derivado = derivarSlugDesdeUrl(fuente, m.url_manga);
    if (derivado) {
      m.slug = derivado;
    } else {
      errores.push(`No se pudo derivar el slug automáticamente desde la URL para '${fuente}' — revisá el formato de la URL, o completá el campo slug a mano.`);
    }
  }

  if (input.ultimo_capitulo !== undefined) {
    const n = Number(input.ultimo_capitulo);
    if (Number.isNaN(n) || n < 0) errores.push('ultimo_capitulo debe ser un número >= 0.');
    else m.ultimo_capitulo = n;
  } else if (esNuevo) {
    m.ultimo_capitulo = 0;
  }

  // capitulos_con_error: lista de números o null — NUNCA [] (el resto del
  // código, scraper y server, asume que "sin errores" es null).
  if (input.capitulos_con_error !== undefined) {
    const arr = input.capitulos_con_error;
    if (arr === null) {
      m.capitulos_con_error = null;
    } else if (Array.isArray(arr)) {
      const nums = arr.map(Number).filter(n => !Number.isNaN(n));
      m.capitulos_con_error = nums.length ? nums : null;
    } else {
      errores.push('capitulos_con_error debe ser una lista de números o null.');
    }
  } else if (esNuevo) {
    m.capitulos_con_error = null;
  }

  if (input.activo !== undefined) m.activo = !!input.activo;
  else if (esNuevo) m.activo = true;

  // prioridad_fuente: entero opcional (menor = mejor), solo tiene efecto
  // cuando el mismo manga se sigue desde 2+ fuentes a la vez (ver
  // scraper/README.md sección 6) — si no se manda, el sistema usa la
  // prioridad automática de ORDEN_FUENTES sin problema. 'null' es la
  // señal explícita de "borrar este campo" (la manda el botón de
  // basurero del editor) — se distingue de "no vino en el body", que en
  // una edición significa "no tocar, dejalo como está". Si no viene
  // nada en un manga nuevo, la clave directamente no se agrega.
  if (input.prioridad_fuente !== undefined) {
    if (input.prioridad_fuente === null) {
      m.prioridad_fuente = null;
    } else {
      const n = Number(input.prioridad_fuente);
      if (!Number.isInteger(n)) errores.push('prioridad_fuente debe ser un número entero.');
      else m.prioridad_fuente = n;
    }
  }

  if (fuente === 'olympus') {
    const manualId = input.manga_id !== undefined ? String(input.manga_id || '').trim() : '';
    if (manualId) {
      // No hay ningún campo en la interfaz para escribirlo a mano — esto
      // es solo una válvula de escape a nivel API por si alguna vez hace
      // falta forzarlo manualmente.
      m.manga_id = manualId;
    } else if (m.slug) {
      // m.slug siempre trae el prefijo "comic-" para olympus (ver
      // derivarSlugDesdePath) — Python arma la URL de resolución
      // agregando ese prefijo por su cuenta, así que hay que sacarlo acá
      // antes de pasarlo para no terminar con "comic-comic-...".
      const resuelto = await resolverMangaIdOlympus(m.slug.replace(/^comic-/, ''));
      if (resuelto) m.manga_id = resuelto;
      // Si falla (Olympus no respondió, cambió el HTML, etc.) no se
      // bloquea el guardado — Python lo termina de resolver en el
      // próximo escaneo, igual que ya hace hoy.
    }
  }
  if (fuente === 'tmo' && input.grupo_preferido !== undefined) {
    m.grupo_preferido = String(input.grupo_preferido || '').trim();
  }

  return { errores, manga: m };
}

// ── Identidad de una entrada de seguimiento ─────────────────────────────
// Una entrada se identifica por (nombre_carpeta + fuente), NO solo por
// nombre_carpeta: el sistema multi-fuente permite el mismo manga desde
// varias fuentes (misma carpeta, fuentes distintas). Buscar solo por
// nombre hacía que editar/borrar la entrada de una fuente afectara a la
// de otra. Devuelve los índices que coinciden; si 'fuente' no viene
// (cliente viejo con la página en caché) matchea solo por nombre.
function localizarManga(mangas, nombre, fuente) {
  const idxs = [];
  mangas.forEach((m, i) => {
    if (m.nombre_carpeta !== nombre) return;
    if (fuente && m.fuente !== fuente) return;
    idxs.push(i);
  });
  return idxs;
}

// ── GET /mangas ──────────────────────────────────────────────────────────
router.get('/mangas', requireAdmin, (req, res) => {
  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }
  res.json({ mangas: data.mangas || [] });
});

// ── POST /mangas (alta) ─────────────────────────────────────────────────
// Igual que /fuentes: solo se puede guardar con el scraper idle, para no
// entrar en carrera con los guardar_seguimiento(data) que Python hace en
// memoria durante un ciclo activo (ver nota larga en POST /fuentes).
router.post('/mangas', requireAdmin, async (req, res) => {
  const estado = leerEstado();
  if (estado.status !== 'idle') {
    return res.status(409).json({ error: 'No se puede agregar mientras el scraper está corriendo — esperá a que termine o hacé Detener. Aplica desde el próximo escaneo.' });
  }

  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }

  const dominiosFuente = obtenerDominiosFuente();
  const { errores, manga } = await validarYNormalizarManga(req.body || {}, dominiosFuente, { esNuevo: true });
  if (errores.length) return res.status(400).json({ error: errores.join(' ') });

  const mangas = data.mangas || [];

  // Duplicado EXACTO: misma nombre_carpeta Y misma fuente -> bloqueo duro.
  // No puede haber dos entradas de la MISMA fuente apuntando a la misma
  // carpeta (¿cuál de las dos usaría el scraper?). Nombre repetido con
  // FUENTE DISTINTA es el sistema multi-fuente funcionando como se espera
  // (ver scraper/README.md sección 6) — no se bloquea, se resuelve más abajo.
  if (mangas.some(m => m.nombre_carpeta === manga.nombre_carpeta && m.fuente === manga.fuente)) {
    return res.status(409).json({ error: `Ya existe un manga con nombre_carpeta '${manga.nombre_carpeta}' y fuente '${manga.fuente}' — no puede repetirse la misma fuente para el mismo manga.` });
  }

  // Mismo nombre_carpeta pero fuente distinta -> multi-fuente intencional.
  // Se guarda directo, sin pedir confirmación (a diferencia del caso de
  // nombre PARECIDO más abajo) — pero se devuelve un aviso para que el
  // panel le muestre al admin que quedó sumado como fuente adicional del
  // mismo manga, no como uno nuevo sin relación.
  const fuentesExistentes = mangas.filter(m => m.nombre_carpeta === manga.nombre_carpeta).map(m => m.fuente);
  const esMultiFuente = fuentesExistentes.length > 0;

  // Duplicado por NOMBRE PARECIDO (posible variante de tildes/puntuación,
  // o directamente el mismo manga cargado dos veces) -> se avisa, no se
  // bloquea; el cliente puede reenviar con forzar:true para confirmar.
  // Se excluyen los 'exacto' de acá porque ya se resolvieron arriba (o se
  // bloquearon por fuente repetida, o ya se van a avisar como multi-fuente)
  // — mostrar el aviso genérico de "nombre parecido" además sería redundante
  // y confuso para un caso que en realidad es válido.
  if (!esMultiFuente && !req.body?.forzar) {
    const posibles = buscarPosiblesDuplicados(manga.nombre_carpeta, mangas).filter(p => p.tipo !== 'exacto');
    if (posibles.length) {
      return res.status(409).json({
        posiblesDuplicados: posibles,
        error: 'Encontré nombres parecidos ya en seguimiento — revisá si no es el mismo manga antes de confirmar.',
      });
    }
  }

  // Un alta nueva no tiene nada que "borrar" — si prioridad_fuente llegó
  // como null (ej. se tocó el botón de basurero sin haber escrito nada
  // antes), se saca la clave en vez de guardar 'null': la regla es que
  // el campo no aparezca en absoluto cuando no se usa, no que aparezca
  // vacío.
  if (manga.prioridad_fuente === null) delete manga.prioridad_fuente;

  data.mangas = [...mangas, manga];
  try { guardarSeguimientoAtomico(data); }
  catch (e) { return res.status(500).json({ error: `No se pudo guardar: ${e.message}` }); }

  res.json({
    ok: true,
    manga,
    ...(esMultiFuente ? { avisoMultiFuente: `Ya se seguía este manga desde: ${fuentesExistentes.join(', ')}. Se agregó también desde '${manga.fuente}' (multi-fuente, misma carpeta).` } : {}),
  });
});

// ── PUT /mangas (edición) ────────────────────────────────────────────────
// Se identifica el manga a editar por su nombre_carpeta ACTUAL (viaja en
// el body como 'original_nombre_carpeta', no en la URL — evita problemas
// de encoding con nombres que tienen tildes, espacios, etc.)
router.put('/mangas', requireAdmin, async (req, res) => {
  const estado = leerEstado();
  if (estado.status !== 'idle') {
    return res.status(409).json({ error: 'No se puede editar mientras el scraper está corriendo — esperá a que termine o hacé Detener. Aplica desde el próximo escaneo.' });
  }

  const original = req.body?.original_nombre_carpeta;
  const originalFuente = req.body?.original_fuente;
  if (!original) return res.status(400).json({ error: "Falta 'original_nombre_carpeta' para saber cuál editar." });

  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }

  const mangas = data.mangas || [];
  const coincidencias = localizarManga(mangas, original, originalFuente);
  if (coincidencias.length === 0) return res.status(404).json({ error: `No se encontró ningún manga con nombre_carpeta '${original}'${originalFuente ? ` y fuente '${originalFuente}'` : ''}.` });
  if (coincidencias.length > 1) return res.status(400).json({ error: `Hay ${coincidencias.length} entradas que coinciden con '${original}'${originalFuente ? ` / '${originalFuente}'` : ' (de distintas fuentes)'} — falta 'original_fuente' para saber cuál editar.` });
  const idx = coincidencias[0];

  const dominiosFuente = obtenerDominiosFuente();
  const { errores, manga } = await validarYNormalizarManga(req.body?.manga || {}, dominiosFuente, { esNuevo: false });
  if (errores.length) return res.status(400).json({ error: errores.join(' ') });

  // Cambio de FUENTE sin cambio de nombre: el bloque de más abajo solo corre
  // si cambia el nombre, así que este caso quedaba sin chequear — se podía
  // dejar dos entradas idénticas (mismo nombre + misma fuente).
  if (manga.nombre_carpeta === original && manga.fuente !== mangas[idx].fuente &&
      mangas.some((m, i) => i !== idx && m.nombre_carpeta === manga.nombre_carpeta && m.fuente === manga.fuente)) {
    return res.status(409).json({ error: `Ya existe otro manga con nombre_carpeta '${manga.nombre_carpeta}' y fuente '${manga.fuente}'.` });
  }

  let avisoMultiFuente;
  if (manga.nombre_carpeta !== original) {
    // Duplicado EXACTO: misma nombre_carpeta Y misma fuente -> bloqueo duro.
    // Mismo criterio que en el alta (POST /mangas) — ver el comentario ahí.
    if (mangas.some((m, i) => i !== idx && m.nombre_carpeta === manga.nombre_carpeta && m.fuente === manga.fuente)) {
      return res.status(409).json({ error: `Ya existe otro manga con nombre_carpeta '${manga.nombre_carpeta}' y fuente '${manga.fuente}'.` });
    }

    const fuentesExistentes = mangas.filter((m, i) => i !== idx && m.nombre_carpeta === manga.nombre_carpeta).map(m => m.fuente);
    const esMultiFuente = fuentesExistentes.length > 0;
    if (esMultiFuente) {
      avisoMultiFuente = `Ya se seguía este manga desde: ${fuentesExistentes.join(', ')}. Quedó también desde '${manga.fuente}' (multi-fuente, misma carpeta).`;
    }

    if (!esMultiFuente && !req.body?.forzar) {
      const posibles = buscarPosiblesDuplicados(manga.nombre_carpeta, mangas, original).filter(p => p.tipo !== 'exacto');
      if (posibles.length) {
        return res.status(409).json({
          posiblesDuplicados: posibles,
          error: 'Encontré nombres parecidos ya en seguimiento — revisá si no es el mismo manga antes de confirmar.',
        });
      }
    }
  }

  // Merge sobre la entrada existente: conserva campos que no vinieron en
  // el body (ej. manga_id si la fuente actual no es olympus y el form no
  // lo mandó, no lo queremos perder por si vuelve a cambiar de fuente).
  mangas[idx] = { ...mangas[idx], ...manga };
  // El botón de basurero manda prioridad_fuente:null como señal de
  // "borrar" (ver validarYNormalizarManga) — el spread de arriba solo
  // pisa el valor CON null, no saca la clave, así que hay que borrarla
  // acá aparte para que no quede "prioridad_fuente": null en el JSON.
  if (mangas[idx].prioridad_fuente === null) delete mangas[idx].prioridad_fuente;
  data.mangas = mangas;

  try { guardarSeguimientoAtomico(data); }
  catch (e) { return res.status(500).json({ error: `No se pudo guardar: ${e.message}` }); }

  res.json({ ok: true, manga: mangas[idx], ...(avisoMultiFuente ? { avisoMultiFuente } : {}) });
});

// ── DELETE /mangas ───────────────────────────────────────────────────────
router.delete('/mangas', requireAdmin, (req, res) => {
  const estado = leerEstado();
  if (estado.status !== 'idle') {
    return res.status(409).json({ error: 'No se puede borrar mientras el scraper está corriendo — esperá a que termine o hacé Detener.' });
  }

  const nombre = req.body?.nombre_carpeta;
  const fuente = req.body?.fuente;
  if (!nombre) return res.status(400).json({ error: "Falta 'nombre_carpeta'." });

  let data;
  try { data = leerSeguimiento(); }
  catch (e) { return res.status(500).json({ error: `No se pudo leer seguimiento.json: ${e.message}` }); }

  const mangas = data.mangas || [];
  const coincidencias = localizarManga(mangas, nombre, fuente);
  if (coincidencias.length === 0) {
    return res.status(404).json({ error: `No se encontró ningún manga con nombre_carpeta '${nombre}'${fuente ? ` y fuente '${fuente}'` : ''}.` });
  }
  // Nunca borrar "a ciegas" más de una entrada: si hay varias y no vino la
  // fuente, es ambiguo y se rechaza en vez de llevarse las de otras fuentes.
  if (coincidencias.length > 1) {
    return res.status(400).json({ error: `Hay ${coincidencias.length} entradas que coinciden con '${nombre}'${fuente ? ` / '${fuente}'` : ' (de distintas fuentes)'} — falta 'fuente' para saber cuál borrar.` });
  }

  data.mangas = mangas.filter((_, i) => i !== coincidencias[0]);
  try { guardarSeguimientoAtomico(data); }
  catch (e) { return res.status(500).json({ error: `No se pudo guardar: ${e.message}` }); }

  res.json({ ok: true });
});

// ── GET /dominios ────────────────────────────────────────────────────────
// Sin POST a propósito — ver el comentario largo arriba de
// DOMINIOS_FUENTE_PATH. Esto es de solo lectura desde la web; el archivo
// solo se edita a mano (o se pisa el código fuente y se reinicia el server).
router.get('/dominios', requireAdmin, (req, res) => {
  res.json({ dominios: obtenerDominiosFuente() });
});

module.exports = router;
