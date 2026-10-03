// ═════════════════════════════════════════════════════════════════════════
// motorInstaller.js — Descarga el motor de mejora IA (binario de Upscayl para
// el SO de ESTA máquina + los modelos) desde el repo oficial de Upscayl.
//
// Lo usan dos lugares, con exactamente el mismo código:
//   - el botón "Instalar motor ahora" del panel (routes/upscaleControl.js)
//   - el comando de terminal (scripts/instalarMotorIA.js)
//
// No se comitea nada de esto al repo de Servidor-de-Mangas (~170-200MB de
// modelos + binario): esto es lo que lo repone.
// ═════════════════════════════════════════════════════════════════════════

const https = require('https');
const http  = require('http');
const fs    = require('fs');
const path  = require('path');

const upscaler = require('./upscaler');

// ── Configuración ────────────────────────────────────────────────────────
const REPO_OWNER = 'upscayl';
const REPO_NAME  = 'upscayl';

// Fijo a un tag, no a "main" — así la descarga es siempre la misma aunque el
// proyecto de Upscayl siga cambiando. Para actualizar el motor más adelante
// (un modelo nuevo, un fix del binario), alcanza con subir este número.
// Verificado a mano: los archivos de este tag son idénticos byte a byte a los
// del .zip de Upscayl que revisamos.
const VERSION_REF = 'v2.15.1';

// Se puede apuntar a otro origen (un espejo propio, o un servidor de prueba)
// con MOTOR_IA_BASE_URL en el .env; por defecto, el repo oficial en GitHub.
const BASE_URL = process.env.MOTOR_IA_BASE_URL
  || `https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${VERSION_REF}/resources`;

const INTENTOS_POR_ARCHIVO = 3;
const TIMEOUT_SIN_DATOS_MS = 30 * 1000;   // si la conexión queda muda tanto tiempo, se corta y se reintenta
const MAX_REDIRECCIONES    = 5;
const PROGRESO_CADA_MS     = 300;

// ── Qué archivos hay que bajar (para esta plataforma) ────────────────────
function listaArchivos() {
  const plat = upscaler.PLATAFORMA;
  const archivos = [
    { remoto: `${plat}/bin/${upscaler.NOMBRE_BINARIO}`, destino: upscaler.RUTA_BINARIO, ejecutable: true },
  ];
  for (const extra of upscaler.ARCHIVOS_EXTRA_BINARIO) {
    archivos.push({ remoto: `${plat}/bin/${extra}`, destino: path.join(upscaler.BIN_DIR, extra) });
  }
  for (const { id } of upscaler.MODELOS_DISPONIBLES) {
    archivos.push({ remoto: `models/${id}.bin`,   destino: path.join(upscaler.MODELOS_DIR, `${id}.bin`) });
    archivos.push({ remoto: `models/${id}.param`, destino: path.join(upscaler.MODELOS_DIR, `${id}.param`) });
  }
  return archivos;
}

// ── Descarga de UN archivo: redirecciones, timeout, verificación de tamaño ─
function descargarUnaVez(url, destinoTmp, onBytes, redireccionesRestantes = MAX_REDIRECCIONES) {
  return new Promise((resolve, reject) => {
    const cliente = url.startsWith('http://') ? http : https;
    const req = cliente.get(url, (res) => {
      if ([301, 302, 307, 308].includes(res.statusCode) && res.headers.location) {
        res.resume();
        if (redireccionesRestantes <= 0) return reject(new Error('Demasiadas redirecciones.'));
        const siguiente = new URL(res.headers.location, url).toString();
        return resolve(descargarUnaVez(siguiente, destinoTmp, onBytes, redireccionesRestantes - 1));
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error(`HTTP ${res.statusCode}`));
      }

      const esperado = parseInt(res.headers['content-length'], 10) || null;
      let recibido = 0;
      const archivo = fs.createWriteStream(destinoTmp);
      const fallar = (e) => { archivo.destroy(); reject(e); };

      res.on('data', (chunk) => { recibido += chunk.length; onBytes(recibido, esperado); });
      res.on('error', fallar);
      // Sin esto, una conexión cortada a la mitad deja la promesa colgada
      // para siempre (pipe no propaga el corte al archivo de destino).
      res.on('aborted', () => fallar(new Error('La conexión se interrumpió a mitad de la descarga.')));
      archivo.on('error', fallar);
      archivo.on('finish', () => {
        if (esperado !== null && recibido !== esperado) {
          return reject(new Error(`Descarga incompleta (${recibido} de ${esperado} bytes).`));
        }
        resolve();
      });
      res.pipe(archivo);
    });
    req.setTimeout(TIMEOUT_SIN_DATOS_MS, () => req.destroy(new Error('Se quedó sin recibir datos (timeout).')));
    req.on('error', reject);
  });
}

async function descargarArchivo(archivo, { onLog, onProgresoArchivo }) {
  const nombre = path.basename(archivo.destino);

  // Existe y no está vacío = ya se bajó completo en una corrida anterior
  // (se descarga a un .descargando y recién al final se renombra, así que un
  // archivo con su nombre final nunca es una descarga a medias).
  try {
    if (fs.statSync(archivo.destino).size > 0) { onLog(`⏭️  Ya estaba: ${nombre}`); return; }
  } catch {}

  fs.mkdirSync(path.dirname(archivo.destino), { recursive: true });
  const url = `${BASE_URL}/${archivo.remoto}`;
  const tmp = archivo.destino + '.descargando';

  for (let intento = 1; intento <= INTENTOS_POR_ARCHIVO; intento++) {
    let ultimoAviso = 0;
    try {
      await descargarUnaVez(url, tmp, (recibido, esperado) => {
        const ahora = Date.now();
        if (ahora - ultimoAviso < PROGRESO_CADA_MS) return;
        ultimoAviso = ahora;
        onProgresoArchivo(esperado ? Math.round((recibido / esperado) * 100) : 0);
      });
      fs.renameSync(tmp, archivo.destino);
      // El bit de ejecución no sobrevive la descarga en Linux/Mac — sin esto,
      // spawn() explota con EACCES la primera vez que se intenta usar.
      if (archivo.ejecutable && process.platform !== 'win32') fs.chmodSync(archivo.destino, 0o755);
      onProgresoArchivo(100);
      onLog(`✅ ${archivo.remoto}`);
      return;
    } catch (e) {
      try { fs.unlinkSync(tmp); } catch {}
      onLog(`⚠️  Intento ${intento}/${INTENTOS_POR_ARCHIVO} falló para "${archivo.remoto}": ${e.message}`);
      if (intento === INTENTOS_POR_ARCHIVO) throw new Error(`No se pudo descargar "${archivo.remoto}": ${e.message}`);
    }
  }
}

// ── Instalación completa ──────────────────────────────────────────────────
let enCurso = false;

// onLog(texto)         -> una línea de log (con emoji)
// onProgreso({archivo, hechos, total, porcentaje_archivo}) -> para la barra del panel
async function instalarMotor({ onLog = () => {}, onProgreso = () => {} } = {}) {
  if (enCurso) throw new Error('Ya hay una instalación del motor en curso.');
  enCurso = true;
  try {
    const archivos = listaArchivos();
    onLog(`🧩 Instalando el motor de mejora IA para "${upscaler.PLATAFORMA}" (Upscayl ${VERSION_REF}) — ${archivos.length} archivos, ~170-200MB en total.`);

    let hechos = 0;
    for (const archivo of archivos) {
      const nombre = path.basename(archivo.destino);
      onProgreso({ archivo: nombre, hechos, total: archivos.length, porcentaje_archivo: 0 });
      await descargarArchivo(archivo, {
        onLog,
        onProgresoArchivo: (pct) => onProgreso({ archivo: nombre, hechos, total: archivos.length, porcentaje_archivo: pct }),
      });
      hechos++;
    }
    onProgreso({ archivo: null, hechos, total: archivos.length, porcentaje_archivo: 100 });

    const faltantes = upscaler.motorFaltantes();
    if (faltantes.length) {
      throw new Error(`Terminó la descarga pero todavía faltan ${faltantes.length} archivo(s): ${faltantes.map(f => path.basename(f)).join(', ')}.`);
    }
    onLog('🎉 Motor de IA instalado y listo para usar.');
  } finally {
    enCurso = false;
  }
}

module.exports = { instalarMotor, listaArchivos, VERSION_REF, BASE_URL };
