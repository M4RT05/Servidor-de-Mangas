// ═════════════════════════════════════════════════════════════════════════
// upscaler.js — Motor de mejora de calidad con IA (fork de Upscayl)
// ═════════════════════════════════════════════════════════════════════════
// Este módulo es SOLO lógica: resolver el binario correcto según el SO,
// decidir si una página hay que mejorarla u omitirla, armar los argumentos
// de línea de comandos, spawnear el proceso de a una imagen por vez, y
// mover el original a un backup antes de pisarlo. No sabe nada de rutas de
// Express ni de SSE — eso vive en server/routes/upscaleControl.js, que usa
// estas funciones como bloques.
//
// Por qué una imagen por spawn y no un directorio entero (modo batch nativo
// del binario): porque cada página puede necesitar (o no) pasar por el
// modelo según su ancho actual contra el ancho objetivo de la corrida, y
// porque procesar de a una es lo que permite que "parar" y "reanudar" sean
// exactos a nivel página en vez de a nivel carpeta completa.
// ═════════════════════════════════════════════════════════════════════════

const fs   = require('fs');
const path = require('path');
const os   = require('os');
const { spawn } = require('child_process');
const sharp = require('sharp');

const { writeJsonAtomic, listImageNames } = require('./fsHelpers');

// ── Configuración (arriba y en mayúsculas, como en el resto del proyecto) ───

// Mismo criterio de plataforma que usa Upscayl (getPlatform() en su código):
// agrupa cualquier variante de Unix no-Mac bajo "linux".
const PLATAFORMA = (() => {
  switch (os.platform()) {
    case 'win32':  return 'win';
    case 'darwin': return 'mac';
    default:       return 'linux';
  }
})();

// Carpeta donde vive el "motor" (binario + modelos de Upscayl). NO se
// comitea al repo — pesa ~217MB entre los 3 binarios y los 7 modelos — la
// llena scripts/instalarMotorIA.js la primera vez que se corre el server.
// Ver .gitignore.
const MOTOR_IA_DIR = path.join(__dirname, '..', 'motor-ia');
const BIN_DIR       = path.join(MOTOR_IA_DIR, PLATAFORMA, 'bin');
// OJO: el binario exige que esta carpeta se llame literalmente "models"
// (en inglés) — lo detecté probándolo de verdad: con "modelos" tira
// "Unknown model dir type", sin importar que el contenido esté bien. Es
// una validación hardcodeada del propio realesrgan-ncnn-vulkan sobre el
// NOMBRE de la carpeta, no negociable.
const MODELOS_DIR   = path.join(MOTOR_IA_DIR, 'models');

// El nombre real del archivo: con .exe en Windows, sin extensión en Linux/Mac.
// (spawn() en Windows aceptaría "upscayl-bin" a secas, pero fs.existsSync NO
// completa el .exe — motorInstalado() habría dicho "no instalado" aunque el
// binario estuviera ahí.)
const NOMBRE_BINARIO = PLATAFORMA === 'win' ? 'upscayl-bin.exe' : 'upscayl-bin';
const RUTA_BINARIO   = path.join(BIN_DIR, NOMBRE_BINARIO);

const MODELOS_DISPONIBLES = [
  { id: 'digital-art-4x',       nombre: 'Digital Art (recomendado para manga / línea)' },
  { id: 'upscayl-standard-4x',  nombre: 'Upscayl Standard' },
  { id: 'upscayl-lite-4x',      nombre: 'Upscayl Lite (más rápido, menos detalle)' },
  { id: 'high-fidelity-4x',     nombre: 'High Fidelity' },
  { id: 'remacri-4x',           nombre: 'Remacri' },
  { id: 'ultramix-balanced-4x', nombre: 'Ultramix Balanced' },
  { id: 'ultrasharp-4x',        nombre: 'Ultrasharp' },
];
const MODELO_DEFAULT = 'digital-art-4x';

// El binario soporta estos 3 formatos de salida (confirmado con su propio
// -h: "output image format (jpg/png/webp, default=ext/png)"). OJO: jpg
// había quedado marcado antes como con un bug confirmado en el binario —
// lo dejo seleccionable porque así se pidió, pero con la advertencia
// puesta también del lado del panel.
const FORMATOS_DISPONIBLES = [
  { id: 'webp', nombre: 'WebP (recomendado)' },
  { id: 'png',  nombre: 'PNG' },
  { id: 'jpg',  nombre: 'JPG (ver advertencia: bug conocido en el binario)' },
];
const FORMATO_DEFAULT = 'webp';

// Mismo default que trae la app oficial de Upscayl (0) — no hay motivo para
// apartarse de lo que ya viene probado.
const COMPRESION_DEFAULT = 0;

// Carpeta de backups de las imágenes ORIGINALES, previas a mejorarlas.
// Espejo manga/capítulo, pero FUERA de la carpeta de la biblioteca (si
// quedara adentro, fsHelpers.listDirNames podría llegar a confundirla con
// un capítulo más). Configurable por si el día de mañana la querés en
// otro disco.
const BACKUP_IA_DIR = process.env.IA_BACKUP_DIR
  ? path.resolve(process.env.IA_BACKUP_DIR)
  : path.join(path.resolve(process.env.MANGA_PATH || './main'), '..', '_backups_ia');

const NOMBRE_ARCHIVO_REGISTRO = 'registro_progreso.json';

// ── Disponibilidad del motor ─────────────────────────────────────────────
// Archivos extra que necesita el binario junto a él. Solo Windows: el .exe
// está linkeado contra el runtime de OpenMP de Visual C++.
const ARCHIVOS_EXTRA_BINARIO = PLATAFORMA === 'win' ? ['vcomp140.dll', 'vcomp140d.dll'] : [];

// Qué archivos faltan para poder usar el motor. Revisa CADA archivo (binario,
// dependencias y los .bin/.param de cada modelo), no solo que exista la
// carpeta: si una descarga se cortó a la mitad, la carpeta "models" ya existe
// pero faltan modelos, y con solo mirar la carpeta el panel diría "instalado"
// y la mejora fallaría recién al llegar a la primera página.
function motorFaltantes() {
  const faltantes = [];
  const revisar = (ruta) => {
    try { if (fs.statSync(ruta).size > 0) return; } catch {}
    faltantes.push(ruta);
  };
  revisar(RUTA_BINARIO);
  for (const extra of ARCHIVOS_EXTRA_BINARIO) revisar(path.join(BIN_DIR, extra));
  for (const { id } of MODELOS_DISPONIBLES) {
    revisar(path.join(MODELOS_DIR, `${id}.bin`));
    revisar(path.join(MODELOS_DIR, `${id}.param`));
  }
  return faltantes;
}

function motorInstalado() {
  return motorFaltantes().length === 0;
}

// ── Ancho real de una imagen (Mecanismo 1: el disco es la fuente de verdad)
//
// IMPORTANTE — no pasarle la RUTA a sharp: sharp/libvips cachea los
// archivos abiertos por ruta, y como acá los archivos se REEMPLAZAN
// mientras el server sigue vivo, una lectura posterior por ruta puede
// devolver el ancho VIEJO (lo comprobé: después de mejorar una página a
// 2000px, sharp(ruta).metadata() seguía diciendo 1600 dentro del mismo
// proceso). Eso haría decidir mal qué páginas faltan. Se lee el archivo a
// un Buffer y se le pasa eso: sin ruta no hay caché que se quede pegada, y
// tampoco hace falta tocar sharp.cache() (que es global y afectaría a las
// miniaturas del resto de la app).
//
// Para no leer capítulos enteros de un disco externo solo por un ancho,
// primero se prueba con los primeros 1MB (alcanza para el header de
// jpg/png/webp); si sharp no puede con ese pedazo, se lee el archivo entero.
// ─────────────────────────────────────────────────────────────────────────
const BYTES_PREFIJO_HEADER = 1024 * 1024;

function leerPrefijo(ruta, bytes) {
  const fd = fs.openSync(ruta, 'r');
  try {
    const buf = Buffer.alloc(bytes);
    const leidos = fs.readSync(fd, buf, 0, bytes, 0);
    return buf.subarray(0, leidos);
  } finally {
    fs.closeSync(fd);
  }
}

async function obtenerAnchoImagen(rutaImagen) {
  try {
    const meta = await sharp(leerPrefijo(rutaImagen, BYTES_PREFIJO_HEADER)).metadata();
    if (meta.width) return meta.width;
  } catch { /* header más largo que el prefijo -> se lee entero abajo */ }
  const meta = await sharp(fs.readFileSync(rutaImagen)).metadata();
  return meta.width;
}

// Clasifica las páginas de una carpeta de capítulo contra un ancho y un
// formato objetivo, SIN tocar ningún archivo — es lo que permite que
// "reintentar un capítulo incompleto" sea gratis: se vuelve a llamar a
// esto y las páginas que ya están al ancho Y al formato pedidos (porque
// se habían mejorado antes del corte, o en una corrida anterior) salen
// solas como "omitida".
//
// El formato SÍ importa acá, no solo el ancho: si una página ya está en
// 1600px pero en .webp y ahora se pide 1600px en .png, no alcanza con
// mirar el ancho — el archivo en disco no es el que se pidió, así que
// cuenta como pendiente igual (se vuelve a pasar por el binario aunque
// technically no haga falta agrandarla, solo convertirla de formato).
async function clasificarPaginas(carpetaCapitulo, anchoObjetivo, formatoObjetivo) {
  const archivos = listImageNames(carpetaCapitulo);
  const resultado = { total: archivos.length, pendientes: [], omitidas: [] };

  for (let i = 0; i < archivos.length; i++) {
    const nombre = archivos[i];
    const indice = i + 1;   // posición de la página en el capítulo (para mostrar "página 12 de 45")
    const rutaCompleta = path.join(carpetaCapitulo, nombre);
    let ancho;
    try {
      ancho = await obtenerAnchoImagen(rutaCompleta);
    } catch (e) {
      // Imagen corrupta/ilegible: no la contamos como pendiente para no
      // trabarse en un loop reintentándola — el scraper ya tiene su propio
      // mecanismo de validar imágenes íntegras, esto no lo duplica.
      console.warn(`⚠️  No se pudo leer el ancho de "${nombre}": ${e.message}`);
      continue;
    }
    const extensionActual = path.extname(nombre).slice(1).toLowerCase();
    const formatoYaOk = !formatoObjetivo || extensionActual === formatoObjetivo.toLowerCase();
    if (ancho >= anchoObjetivo && formatoYaOk) resultado.omitidas.push({ nombre, ancho, indice });
    else resultado.pendientes.push({ nombre, ancho, indice });
  }
  return resultado;
}

// ── Argumentos de línea de comandos para UNA imagen ──────────────────────
function construirArgumentos({ entrada, salida, modelo, anchoObjetivo, formato, tileSize, gpuId }) {
  const args = [
    '-i', entrada,
    '-o', salida,
    '-m', MODELOS_DIR,
    '-n', modelo || MODELO_DEFAULT,
    '-f', formato || FORMATO_DEFAULT,
    '-w', String(anchoObjetivo),
    '-c', String(COMPRESION_DEFAULT),
  ];
  // tileSize/gpuId: si no se pasan, NO se incluye el flag — mismo criterio
  // que la propia app de Upscayl (dejar que el binario auto-detecte según
  // la VRAM libre de la GPU que tenga la máquina, sea cual sea).
  if (tileSize) args.push('-t', String(tileSize));
  if (gpuId)    args.push('-g', String(gpuId));
  return args;
}

// Ruta espejo dentro de BACKUP_IA_DIR para el original de una imagen dada:
// <BACKUP_IA_DIR>/<manga>/<capítulo>/<archivo>. Se calcula contra la
// carpeta de biblioteca donde VIVE ese manga (hay varias: MANGA_PATH,
// MANGA_PATH_2, ...), no siempre contra MANGA_PATH — si no, un manga de
// otra biblioteca daría una ruta relativa con "..\" y el backup se
// escaparía fuera de la carpeta de backups. Los nombres de manga son
// únicos entre bibliotecas (el catálogo los indexa por nombre), así que
// no hace falta incluir el nombre de la biblioteca en la ruta espejo.
function rutaBackupPara(rutaOriginal, raizBiblioteca) {
  const raiz     = path.resolve(raizBiblioteca || process.env.MANGA_PATH || './main');
  const relativa = path.relative(raiz, rutaOriginal);
  return path.join(BACKUP_IA_DIR, relativa);
}

// ── Mejora de UNA imagen ──────────────────────────────────────────────────
// Devuelve { proceso, promesa }: `proceso` es el ChildProcess (para poder
// cancelarlo desde afuera con proceso.kill() en el modo "parar ya"),
// `promesa` resuelve a:
//   { accion: 'omitida', anchoOriginal }
//   { accion: 'mejorada', anchoOriginal, anchoFinal }
//   rechaza en caso de error real del binario.
//
// onProgreso(porcentaje) es opcional, se llama con un número 0-100 a
// medida que el binario informa avance de ESTA imagen puntual.
function mejorarImagen({ rutaOriginal, anchoObjetivo, modelo, formato, tileSize, gpuId, raizBiblioteca, onProgreso }) {
  let procesoRef = null;
  const opcionesRaiz = raizBiblioteca;
  const formatoFinal = formato || FORMATO_DEFAULT;

  const promesa = (async () => {
    const anchoOriginal = await obtenerAnchoImagen(rutaOriginal);
    const extensionActual = path.extname(rutaOriginal).slice(1).toLowerCase();
    // Igual que en clasificarPaginas: el formato también cuenta para
    // decidir si hay algo que hacer, no solo el ancho — una imagen puede
    // ya estar en el ancho pedido pero en otro formato.
    if (anchoOriginal >= anchoObjetivo && extensionActual === formatoFinal.toLowerCase()) {
      return { accion: 'omitida', anchoOriginal };
    }
    if (!motorInstalado()) {
      throw new Error(
        'El motor de IA no está instalado (falta el binario o los modelos). ' +
        'Corré el script de instalación (scripts/instalarMotorIA.js) primero.'
      );
    }

    const dir      = path.dirname(rutaOriginal);
    const base     = path.parse(rutaOriginal).name;
    const rutaTmp  = path.join(dir, `.${base}.ia_tmp.${formatoFinal}`);

    // Por las dudas quede un .tmp huérfano de una corrida anterior que
    // murió mal (apagón a mitad de esta misma imagen) — se descarta, se
    // recalcula de cero, es barato.
    try { fs.unlinkSync(rutaTmp); } catch {}

    const args = construirArgumentos({
      entrada: rutaOriginal, salida: rutaTmp, modelo, anchoObjetivo, formato: formatoFinal, tileSize, gpuId,
    });

    await new Promise((resolve, reject) => {
      let proc;
      try {
        proc = spawn(RUTA_BINARIO, args, { windowsHide: true });
      } catch (e) {
        return reject(e);
      }
      procesoRef = proc;

      let stderrCapturado = '';
      proc.stderr.on('data', (d) => {
        const texto = d.toString();
        stderrCapturado += texto;
        // Igual que hace la propia app de Upscayl: el binario imprime la
        // línea de progreso como un número (ej. "42.30%") — parseFloat se
        // queda con la parte numérica del principio.
        const pct = parseFloat(texto);
        if (!Number.isNaN(pct) && pct >= 0 && pct <= 100 && onProgreso) onProgreso(pct);
        if (texto.includes('Error') || texto.includes('failed')) {
          console.warn(`⚠️  [IA] ${texto.trim()}`);
        }
      });

      proc.on('error', reject);
      proc.on('exit', (code, signal) => {
        procesoRef = null;
        if (signal) {
          // Lo matamos nosotros mismos (modo "parar ya") — no es un error,
          // el llamador ya sabe que pidió cancelar.
          return reject(Object.assign(new Error('cancelado'), { cancelado: true }));
        }
        if (code !== 0) {
          return reject(new Error(`El binario de IA terminó con código ${code}: ${stderrCapturado.trim().slice(-300)}`));
        }
        resolve();
      });
    });

    // Validar que el resultado abre bien ANTES de tocar el original —
    // mismo espíritu que la verificación de imágenes íntegras del scraper.
    let anchoFinal;
    try {
      anchoFinal = (await sharp(fs.readFileSync(rutaTmp)).metadata()).width; // Buffer, no ruta (ver nota sobre la caché de sharp)
    } catch (e) {
      try { fs.unlinkSync(rutaTmp); } catch {}
      throw new Error(`La imagen mejorada salió corrupta, se descartó: ${e.message}`);
    }

    // Backup del original (espejo manga/capítulo). Dos reglas para que el
    // original de verdad nunca se pierda:
    //  1) Se COPIA (no se mueve): hasta el último instante el original sigue
    //     en su lugar, así un corte justo acá no deja la página faltando.
    //  2) Si ya existe un backup de esa ruta, NO se pisa: ese es el original
    //     de una pasada anterior, y el archivo que hay ahora en el capítulo
    //     ya es una versión mejorada (pisar el backup con esa versión
    //     destruiría el original verdadero). En ese caso no hace falta
    //     resguardar nada de nuevo.
    const destinoBackup = rutaBackupPara(rutaOriginal, opcionesRaiz);
    if (!fs.existsSync(destinoBackup)) {
      fs.mkdirSync(path.dirname(destinoBackup), { recursive: true });
      fs.copyFileSync(rutaOriginal, destinoBackup);
    }

    // La imagen final puede terminar con otra extensión (.webp) que la
    // original (.jpg, por ejemplo). El rename pisa de forma atómica si el
    // nombre es el mismo; si cambió, después se borra el archivo viejo
    // (que ya está resguardado arriba).
    const rutaFinal = path.join(dir, `${base}.${formatoFinal}`);
    fs.renameSync(rutaTmp, rutaFinal);
    if (path.resolve(rutaFinal) !== path.resolve(rutaOriginal)) {
      try { fs.unlinkSync(rutaOriginal); } catch {}
    }

    return { accion: 'mejorada', anchoOriginal, anchoFinal };
  })();

  return {
    get proceso() { return procesoRef; },
    cancelar() { if (procesoRef) procesoRef.kill(); },
    promesa,
  };
}

// ── Lectura/escritura atómica del registro (reusa el mismo patrón y el
// mismo archivo que ya usa el scraper — solo agrega/actualiza el
// sub-objeto `ia` de cada capítulo, nunca toca los campos del scraper) ────
function rutaRegistro(carpetaManga) {
  return path.join(carpetaManga, NOMBRE_ARCHIVO_REGISTRO);
}

function leerRegistro(carpetaManga) {
  try {
    return JSON.parse(fs.readFileSync(rutaRegistro(carpetaManga), 'utf-8'));
  } catch {
    return {};
  }
}

function guardarRegistroAtomico(carpetaManga, registro) {
  writeJsonAtomic(rutaRegistro(carpetaManga), registro);
}

// Actualiza SOLO el sub-objeto `ia` de un capítulo puntual dentro del
// registro, preservando todo lo demás que ya haya escrito el scraper
// (estado, esperadas, validas, fecha, fuente, prioridad_fuente...).
function actualizarIaDeCapitulo(carpetaManga, numeroCapitulo, cambiosIa) {
  const registro = leerRegistro(carpetaManga);
  const capitulo = registro[numeroCapitulo] || {};
  registro[numeroCapitulo] = {
    ...capitulo,
    ia: { ...(capitulo.ia || {}), ...cambiosIa },
  };
  guardarRegistroAtomico(carpetaManga, registro);
  return registro[numeroCapitulo].ia;
}

module.exports = {
  PLATAFORMA, RUTA_BINARIO, NOMBRE_BINARIO, BIN_DIR, MODELOS_DIR, BACKUP_IA_DIR,
  ARCHIVOS_EXTRA_BINARIO, motorFaltantes,
  MODELOS_DISPONIBLES, MODELO_DEFAULT,
  FORMATOS_DISPONIBLES, FORMATO_DEFAULT,
  motorInstalado, obtenerAnchoImagen, clasificarPaginas,
  construirArgumentos, mejorarImagen,
  leerRegistro, guardarRegistroAtomico, actualizarIaDeCapitulo,
};
