"""
╔══════════════════════════════════════════════════════════════════════╗
║       📚  M4RTO SCRAPER  v1.6                                        ║
║                                                                      ║
║  Sitios soportados:                                                  ║
║    • Olympus Scanlation   (API JSON)                                 ║
║    • Temple Scan          (WordPress/Madara)                         ║
║    • Dragon Translation   (WordPress/Madara)                         ║
║    • ManhwasWEB           (API JSON)                                 ║
║    • Nexus Scanlation     (API JSON, imágenes con descramble)        ║
║    • Ikigai Mangas        (SSR Qwik, dominios rotativos)             ║
║                                                                      ║
║  Instalar deps: pip install requests beautifulsoup4 Pillow           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import logging
import logging.handlers
import threading
import subprocess
import importlib.util
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin

# ══════════════════════════════════════════════════════════════════════
# §0  AUTO-INSTALADOR
# ══════════════════════════════════════════════════════════════════════

_PAQUETES = {
    "requests":  "requests",
    "bs4":       "beautifulsoup4",
    "PIL":       "Pillow",
    "tqdm":      "tqdm",
    "Crypto":    "pycryptodome",  # usado por ManhwasWeb para capítulos cifrados
}

def _auto_instalar():
    faltantes = [(m, p) for m, p in _PAQUETES.items()
                 if importlib.util.find_spec(m) is None]
    if not faltantes:
        return
    print("\n  ⚠️  Instalando dependencias faltantes...")
    for _, p in faltantes:
        print(f"     • {p}", end=" ", flush=True)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", p, "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print("✅")
        except Exception as e:
            print(f"❌ ({e})")
    print()

_auto_instalar()

import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from tqdm import tqdm
    TQDM_OK = True
except ImportError:
    TQDM_OK = False

# Handler que redirige el logging a través de tqdm.write
# para que no se mezclen con las barras de progreso
class _TqdmLogHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            if TQDM_OK:
                tqdm.write(msg)
            else:
                print(msg)
        except Exception:
            self.handleError(record)

# ══════════════════════════════════════════════════════════════════════
# §1  CONFIGURACIÓN — TODO LO AJUSTABLE ESTÁ ACÁ ARRIBA
# ══════════════════════════════════════════════════════════════════════
#
# Esta sección agrupa todas las constantes que se pueden tocar para
# cambiar el comportamiento del scraper, sin tener que buscarlas
# repartidas por el archivo. Cada una indica qué hace y qué pasa si
# la subís o la bajás.

SCRIPT_DIR        = Path(__file__).parent
SEGUIMIENTO_PATH  = SCRIPT_DIR / "seguimiento.json"
LOG_PATH          = SCRIPT_DIR / "scraper.log"
LOCK_PATH         = SCRIPT_DIR / "scraper.lock"
LOCK_MAX_HORAS    = 6  # más viejo que esto = se asume colgado de una corrida anterior

# ── Reporte de escaneos ───────────────────────────────────────────────
# Un solo archivo: el historial completo de escaneos vive embebido
# DENTRO de reporte.html (no hay un .json aparte). Se sigue pudiendo
# regenerar la página completa en cada ciclo porque el historial se
# lee de ese mismo HTML al arrancar.
REPORTE_PATH          = SCRIPT_DIR / "reporte.html"
HISTORIAL_PATH_LEGACY = SCRIPT_DIR / "historial_reportes.json"
# ^ Archivo de versiones anteriores (antes de unificar todo en
#   reporte.html). Ya no se escribe más: si todavía existe en disco,
#   se lee una sola vez para no perder el historial acumulado y se
#   borra después de migrarlo. Ver _cargar_historial().
NOTIF_PATH_LEGACY = SCRIPT_DIR / "notificaciones.json"
# ^ Sistema de notificaciones eliminado (nada en la web lo leía). Si
#   existe de una versión anterior, se borra una sola vez al arrancar
#   — ver main().

# ── Ikigai: caché de dominios rotativos ──────────────────────────────
# Ikigai cambia de dominio seguido (anti-bloqueo/DMCA), y el de listado
# (series) y el del lector (capítulos) rotan de forma independiente.
# Este archivo guarda el último dominio vigente conocido de cada uno,
# para no depender de ningún dominio fijo hardcodeado en el código —
# ver IkigaiScraper para el mecanismo de auto-actualización.
IKIGAI_ESTADO_PATH = SCRIPT_DIR / "ikigai_estado.json"

# ── Frecuencia de escaneo ────────────────────────────────────────────
# Cada cuántas horas se repite el ciclo completo de búsqueda de
# capítulos nuevos. El scraper corre indefinidamente: escanea, espera
# este intervalo, vuelve a escanear. Para correr UNA sola vez y salir,
# usar el flag --una-vez al ejecutar el script.
INTERVALO_HORAS   = 3

# ── Reintentos de red genéricos (páginas, APIs) ──────────────────────
# Aplica a hacer_get()/hacer_post(): páginas de manga, APIs de
# capítulos, etc. (NO a la descarga de imágenes individuales, que
# tiene su propio control más abajo).
MAX_REINTENTOS    = 3   # cuántas veces reintentar antes de rendirse
DELAY_REINTENTO   = 5   # segundos base del backoff (5s, 10s, 20s...)
TIMEOUT           = 20  # segundos de espera por cada request individual

# ── Descarga de imágenes ─────────────────────────────────────────────
DELAY_ENTRE_IMGS  = 0.3  # pausa antes de cada descarga individual de imagen
                          # (se aplica en cada hilo del pool, no frena la
                          # descarga total porque sigue siendo en paralelo,
                          # pero suaviza la carga sobre el servidor)

# ── Selenium (solo se usa para Temple Scan / sitios con redirección JS) ──
# Poner en False para ver el navegador real durante una corrida —
# útil para diagnosticar visualmente si Temple cambia su flujo.
# En uso normal dejar en True (headless, sin ventana visible).
SELENIUM_HEADLESS = True

# Carpeta del perfil de Brave dedicado al scraper (no es tu perfil
# personal — así no interfiere con sesiones/cookies que tengas
# abiertas mientras el scraper corre en background).
BRAVE_PROFILE_DIR = r"C:\brave-scraper"

# Versión de ChromeDriver a usar si no se pudo detectar la versión de
# Brave instalada (caso raro, normalmente se detecta sola). Conviene
# revisar/subir este número de tanto en tanto si Brave se actualiza
# solo y este fallback queda muy atrás.
CHROMEDRIVER_VERSION_FALLBACK = "149"

# Si Selenium no encuentra ninguna imagen en un capítulo (o solo las
# encuentra por el selector de respaldo genérico, señal de que algo
# está raro), vuelca el HTML de la página acá para poder revisarlo a
# mano. No forma parte del reporte de escaneos porque es un volcado
# crudo de depuración, no un resumen — se sobrescribe en cada caso
# nuevo, no se acumula.
DEBUG_SELENIUM_PATH = SCRIPT_DIR / "debug_selenium.html"

# ── Límite de tiempo por manga ───────────────────────────────────────
# Tiempo máximo total que se le da a UN manga (todos sus capítulos
# pendientes) dentro de un mismo ciclo de escaneo. Si se supera, se
# corta ese manga y se sigue con el siguiente — para que un manga
# colgado (ej: Selenium esperando una redirección que nunca llega)
# no estire un escaneo de 3 horas mucho más de lo esperado. Los
# capítulos que quedaron pendientes se reintentan en el próximo ciclo.
TIMEOUT_MANGA_SEG = 20 * 60  # 20 minutos

# ── Prioridad multi-fuente ────────────────────────────────────────────
# Único lugar que define qué tan confiable es cada sitio. Se usa para
# DOS cosas relacionadas (antes vivían separadas y se podían
# desincronizar):
#   1) el orden en que se escanean los sitios (de más a menos confiable)
#   2) la prioridad automática de cada manga para el sistema multi-
#      fuente, si no se fuerza 'prioridad_fuente' a mano en
#      seguimiento.json (que sigue siendo 100% opcional)
# Cualquier fuente nueva que no esté listada acá simplemente cae al
# final — no rompe nada, ni en el orden de escaneo ni en la prioridad.
ORDEN_FUENTES = ["olympus", "nexus", "temple", "manhwaweb", "dragon", "ikigai"]

# Perfiles de filtro de imagen por sitio (dimensiones mínimas, ratio
# máximo, umbral de "ícono cuadrado", tolerancia de ancho respecto al
# dominante del capítulo, mínimo de páginas antes de activar el modo
# relajado). Antes vivía como dict local dentro de descargar_capitulo,
# reconstruido en cada llamada — subido a nivel de módulo para que sea
# más fácil encontrarlo y ajustarlo sin tener que buscarlo adentro de
# un método de 200 líneas.
FILTROS = {
    "olympus":   {"ancho_min": 400, "alto_min": 400, "ratio_max": 3.5, "cuadrado_max": 500, "tol_pct": 15, "fallback_min": 2},
    "temple":    {"ancho_min": 150, "alto_min": 150, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 30, "fallback_min": 2},
    "dragon":    {"ancho_min": 150, "alto_min": 150, "ratio_max": 4.0, "cuadrado_max": 500, "tol_pct": 20, "fallback_min": 2},
    "manhwaweb": {"ancho_min": 300, "alto_min": 300, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 30, "fallback_min": 2},
    "nexus":     {"ancho_min": 300, "alto_min": 300, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 30, "fallback_min": 2},
    # Ikigai ya descarta los banners de publicidad por ruta de URL
    # exacta en obtener_imagenes (más confiable, ver esa función) —
    # este perfil es solo una red de seguridad secundaria.
    "ikigai":    {"ancho_min": 300, "alto_min": 300, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 25, "fallback_min": 2},
}

# Prioridad reservada para contenido que apareció en la carpeta de un
# manga SIN que ningún scraper lo haya descargado (ej: lo agregaste a
# mano, o es de antes de tener este sistema y no quedó registrado).
# Es mejor que CUALQUIER fuente real — nada de lo que bajen los
# scrapers puede reemplazarlo nunca.
FUENTE_EXTERNA    = "externa"
PRIORIDAD_EXTERNA = 0

def prioridad_automatica(fuente: str) -> int:
    """Prioridad por defecto según la fuente (1 = mejor), derivada de
    ORDEN_FUENTES. Una fuente no listada (nueva/futura) cae al final,
    nunca rompe nada."""
    if fuente in ORDEN_FUENTES:
        return ORDEN_FUENTES.index(fuente) + 1
    return len(ORDEN_FUENTES) + 1

def prioridad_efectiva(manga_cfg: dict) -> int:
    """
    Prioridad real a usar para el cruce multi-fuente de este manga_cfg:
    el valor manual de 'prioridad_fuente' si está puesto en
    seguimiento.json, si no la automática según su 'fuente'.

    SIEMPRE devuelve un número (nunca None) — por eso
    'prioridad_fuente' sigue siendo 100% opcional: si no lo escribís,
    el manga participa igual con la prioridad automática de su sitio,
    y si el manga es de una sola fuente esto no cambia nada en la
    práctica (nunca aparece una entrada rival con la que comparar).
    """
    manual = manga_cfg.get("prioridad_fuente")
    if manual is not None:
        return manual
    return prioridad_automatica(manga_cfg.get("fuente", ""))

def validar_multi_fuente(mangas: list[dict]):
    """
    Chequeos de cordura al arrancar un escaneo. No frenan nada — solo
    avisan de configuraciones de seguimiento.json que probablemente
    sean un error humano (copy-paste sin terminar de editar, etc.).
    """
    por_carpeta: dict[str, list[dict]] = {}
    for m in mangas:
        if not m.get("activo", True):
            continue
        por_carpeta.setdefault(m.get("nombre_carpeta", "?"), []).append(m)

    for carpeta, entradas in por_carpeta.items():
        if len(entradas) < 2:
            continue

        # Misma fuente repetida para el mismo manga (probable
        # copy-paste de una entrada sin cambiar la fuente/slug).
        fuentes = [e.get("fuente") for e in entradas]
        repetidas = sorted({f for f in fuentes if f and fuentes.count(f) > 1})
        if repetidas:
            log.warning(f"  ⚠  '{carpeta}' tiene más de una entrada con la misma "
                       f"fuente ({', '.join(repetidas)}) en seguimiento.json "
                       f"— revisar si es un copy-paste sin terminar de editar.")

        # Dos fuentes DISTINTAS con la misma prioridad efectiva: en un
        # empate gana la que escaneó primero, sin avisar nada — mejor
        # detectarlo acá y sugerir desambiguar a mano.
        por_prioridad: dict[int, set] = {}
        for e in entradas:
            p = prioridad_efectiva(e)
            por_prioridad.setdefault(p, set()).add(e.get("fuente", "?"))
        for p, fs in por_prioridad.items():
            if len(fs) > 1:
                log.warning(f"  ⚠  '{carpeta}' tiene fuentes distintas "
                           f"({', '.join(sorted(fs))}) con la MISMA prioridad "
                           f"({p}) — conviene desambiguar con 'prioridad_fuente' "
                           f"manual en seguimiento.json para que no dependa del "
                           f"orden de escaneo.")

# ── Olympus: páginas de novedades a escanear para recuperar slugs ───
# Solo se usa para auto-recuperar el slug de un manga si el guardado
# en seguimiento.json ya venció (la URL da 404). El listado de QUÉ
# descargar de cada manga viene siempre de su página de serie
# completa, no de estas páginas de novedades.
OLYMPUS_MAX_PAGINAS_NOVEDADES = 20

# ── Detector de bloqueo (BlockDetector) ──────────────────────────────
# Si un dominio específico acumula este número de errores CONSECUTIVOS
# (403/410/429/503, sin ningún éxito en medio), se asume que puede
# estar rate-limitando y se pausan las descargas de ESE dominio
# durante BLOQUEO_PAUSA_SEG antes de reintentar.
BLOQUEO_MAX_ERRORES_CONSECUTIVOS = 8
BLOQUEO_PAUSA_SEG                = 30

# ── Historial del reporte HTML ───────────────────────────────────────
# El reporte.html acumula TODOS los escaneos pasados (no se borra).
# Estos topes evitan que ese historial crezca sin límite con el tiempo:
# se aplica el que se alcance primero.
MAX_ESCANEOS_HISTORIAL = 200             # cantidad máxima de escaneos guardados
MAX_BYTES_HISTORIAL    = 5 * 1024*1024   # tamaño máximo en disco (5 MB)
MAX_LARGO_MENSAJE      = 500             # un solo mensaje de error/advertencia
                                          # no puede tener más de esto (se trunca)

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# ══════════════════════════════════════════════════════════════════════
# §2  LOGGING
# ══════════════════════════════════════════════════════════════════════

# Configurar logging con handler tqdm para no mezclar con barras de progreso
_fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
# RotatingFileHandler en vez de FileHandler plano: corriendo cada pocas
# horas durante meses (vía el scheduler), un archivo sin límite de
# tamaño eventualmente se vuelve gigante. 10 MB por archivo x 3
# backups = 40 MB como tope total, más que suficiente para ver el
# historial reciente sin que crezca para siempre.
_file_handler = logging.handlers.RotatingFileHandler(
    LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)
_tqdm_handler = _TqdmLogHandler()
_tqdm_handler.setFormatter(_fmt)

log = logging.getLogger("m4rto")
log.setLevel(logging.INFO)
log.addHandler(_file_handler)
log.addHandler(_tqdm_handler)

# ══════════════════════════════════════════════════════════════════════
# §4  SEGUIMIENTO
# ══════════════════════════════════════════════════════════════════════

def cargar_seguimiento() -> dict | None:
    """
    Carga seguimiento.json. Retorna None si no existe o está mal
    formado — NUNCA mata el proceso con sys.exit() acá, porque esta
    función se llama en cada ciclo de escaneo (no solo al arrancar):
    si el archivo queda mal formado un instante porque alguien lo
    está editando a mano mientras el scraper corre en background,
    eso no debería terminar el programa entero, sino simplemente
    saltar ese ciclo y reintentar en el próximo. Quien llama decide
    qué hacer con un None (ver main() para el caso de arranque inicial).
    """
    if not SEGUIMIENTO_PATH.exists():
        log.error(f"No se encontró {SEGUIMIENTO_PATH}")
        return None
    try:
        return json.loads(SEGUIMIENTO_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.error(f"Error en seguimiento.json: {e} — "
                 f"se reintentará en el próximo escaneo")
        return None

def guardar_seguimiento(data: dict):
    """Guarda seguimiento.json de forma atómica (escribe a .tmp y
    reemplaza) — evita dejarlo truncado/corrupto si el proceso se
    interrumpe justo a mitad de la escritura."""
    tmp = SEGUIMIENTO_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    tmp.replace(SEGUIMIENTO_PATH)

def normalizar_url_manga(manga_cfg: dict) -> bool:
    """
    Si el manga tiene un campo opcional 'url_manga' con la URL completa
    copiada del navegador (ej: pegada directo desde la barra de
    direcciones), extrae el slug automáticamente y lo guarda en el
    campo 'slug' — así no hace falta pensar si la URL lleva o no el
    prefijo 'comic-', ni separar manualmente el slug de la URL.

    Es retrocompatible: si 'url_manga' no está presente, no hace nada
    y el manga sigue funcionando con el campo 'slug' de siempre.

    Soporta las URLs típicas de cada fuente:
      Olympus:    https://olympusxyz.com/series/comic-{slug}
      Temple:     https://{dominio}/manga/{slug}/  (o variantes con prefijo)
      Dragon:     https://dragontranslation.org/manga/{slug}/
      ManhwasWEB: https://manhwaweb.com/manhwa/{slug}
      Nexus:      https://nexusscanlation.com/series/{slug}

    Retorna True si modificó manga_cfg (para saber si hay que guardar
    seguimiento.json), False si no había nada que normalizar.
    """
    url = manga_cfg.get("url_manga", "").strip()
    if not url:
        return False

    fuente = manga_cfg.get("fuente", "")
    slug_extraido = None

    try:
        path = urlparse(url).path.strip("/")
    except Exception:
        return False

    if fuente == "olympus":
        # .../series/comic-{slug}  o  .../series/{slug}
        m = re.search(r"series/(comic-)?(.+)$", path)
        if m:
            slug_extraido = "comic-" + m.group(2) if not m.group(1) else f"comic-{m.group(2)}"
            # Asegurar el prefijo "comic-" una sola vez
            slug_extraido = "comic-" + slug_extraido.removeprefix("comic-")
    elif fuente == "manhwaweb":
        # .../manhwa/{slug}
        m = re.search(r"manhwa/(.+)$", path)
        if m:
            slug_extraido = m.group(1)
    elif fuente == "nexus":
        # .../series/{slug}  (sin prefijo "comic-", a diferencia de Olympus)
        m = re.search(r"series/(.+)$", path)
        if m:
            slug_extraido = m.group(1).split("/")[0]  # por si viene con /chapter/... colgando
    elif fuente == "ikigai":
        # .../series/{slug}/  (también puede venir con ?pagina=N colgando,
        # pero eso ya se descarta al hacer urlparse().path)
        m = re.search(r"series/(.+)$", path)
        if m:
            slug_extraido = m.group(1).split("/")[0]
    elif fuente in ("temple", "dragon"):
        # .../manga/{slug}/  o  .../serie/{slug}/  etc — tomar el
        # último segmento no vacío del path como slug.
        partes = [p for p in path.split("/") if p]
        if partes:
            # Si el primer segmento es un prefijo conocido, lo descartamos
            if partes[0] in ("manga", "serie", "manhwa", "comic", "webtoon") and len(partes) > 1:
                slug_extraido = partes[1]
            else:
                slug_extraido = partes[-1]

    if not slug_extraido:
        log.warning(f"  ⚠  No se pudo extraer el slug de url_manga para "
                   f"'{manga_cfg.get('nombre_carpeta','?')}' "
                   f"(fuente={fuente}, url={url})")
        return False

    if manga_cfg.get("slug") != slug_extraido:
        manga_cfg["slug"] = slug_extraido
        log.info(f"  🔗 Slug actualizado desde url_manga para "
                 f"'{manga_cfg.get('nombre_carpeta','?')}': {slug_extraido}")
        return True
    return False

# ══════════════════════════════════════════════════════════════════════
# §5  UTILIDADES HTTP
# ══════════════════════════════════════════════════════════════════════

def hacer_get(url: str, session: requests.Session, **kwargs) -> requests.Response | None:
    """GET con reintentos automáticos y backoff exponencial."""
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            r = session.get(url, timeout=TIMEOUT, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if intento < MAX_REINTENTOS:
                espera = DELAY_REINTENTO * (2 ** (intento - 1))  # 5s, 10s, 20s...
                log.warning(f"  Intento {intento}/{MAX_REINTENTOS} fallido: {e} "
                           f"(reintentando en {espera}s)")
                time.sleep(espera)
            else:
                log.error(f"  Falló después de {MAX_REINTENTOS} intentos: {url}")
                return None

def hacer_post(url: str, session: requests.Session, data: dict, **kwargs) -> requests.Response | None:
    """POST con reintentos automáticos y backoff exponencial."""
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            r = session.post(url, data=data, timeout=TIMEOUT, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if intento < MAX_REINTENTOS:
                espera = DELAY_REINTENTO * (2 ** (intento - 1))
                log.warning(f"  Intento {intento}/{MAX_REINTENTOS} fallido: {e} "
                           f"(reintentando en {espera}s)")
                time.sleep(espera)
            else:
                log.error(f"  Falló después de {MAX_REINTENTOS} intentos: {url}")
                return None

def nombre_capitulo(numero) -> str:
    """Formatea el número de capítulo como 'Capitulo_X' o 'Capitulo_X.Y'."""
    try:
        n = float(numero)
        if n == int(n):
            return f"Capitulo_{int(n)}"
        else:
            return f"Capitulo_{n}"
    except (ValueError, TypeError):
        return f"Capitulo_{numero}"

def capitulo_ya_existe(carpeta_manga: Path, num_cap) -> bool:
    """
    Verifica si el capítulo ya está descargado y COMPLETO (no solo
    que la carpeta tenga alguna imagen). Delega en capitulo_esta_completo,
    que usa el registro de progreso + verificación de integridad real.
    """
    return capitulo_esta_completo(carpeta_manga, num_cap)

def contar_capitulos_en_disco(carpeta_manga: Path) -> int:
    """
    Cuenta cuántos capítulos están realmente COMPLETOS en disco
    (según el registro de progreso / verificación de integridad),
    no solo "tienen alguna imagen adentro".
    Se usa para detectar manga "vacíos" (carpeta borrada manualmente,
    nunca descargado, o solo con descargas parciales) aunque
    seguimiento.json diga ultimo_capitulo > 0.
    """
    if not carpeta_manga.exists():
        return 0
    total = 0
    for sub in carpeta_manga.iterdir():
        if not sub.is_dir() or not sub.name.startswith("Capitulo_"):
            continue
        m = re.search(r"Capitulo_(\d+(?:\.\d+)?)", sub.name)
        if not m:
            continue
        numero = numero_a_float(m.group(1))
        if capitulo_esta_completo(carpeta_manga, numero):
            total += 1
    return total

# ══════════════════════════════════════════════════════════════════════
# §5c  REGISTRO DE PROGRESO (completado / parcial)
# ══════════════════════════════════════════════════════════════════════
#
# Cada carpeta de manga tiene su propio "registro_progreso.json" con
# el detalle de cada capítulo: cuántas imágenes se esperaban y cuántas
# se bajaron con éxito y pasaron la verificación de integridad.
#
# Esto resuelve un problema que contar_capitulos_en_disco no puede ver:
# una carpeta con 10 de 17 imágenes (por un bloqueo a mitad de descarga)
# se ve "con contenido" para el conteo simple, pero en realidad está
# incompleta y debería reintentarse en el próximo escaneo.

NOMBRE_REGISTRO = "registro_progreso.json"

def _archivo_registro(carpeta_manga: Path) -> Path:
    return carpeta_manga / NOMBRE_REGISTRO

def cargar_registro_progreso(carpeta_manga: Path) -> dict:
    """Carga el registro de progreso del manga, o {} si no existe."""
    archivo = _archivo_registro(carpeta_manga)
    if not archivo.exists():
        return {}
    try:
        return json.loads(archivo.read_text(encoding="utf-8"))
    except Exception:
        return {}

def guardar_registro_progreso(carpeta_manga: Path, reg: dict):
    """Guarda el registro de forma atómica (escribe a .tmp y reemplaza)."""
    archivo = _archivo_registro(carpeta_manga)
    tmp     = archivo.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(archivo)

def verificar_imagenes_integras(carpeta_cap: Path) -> int:
    """
    Cuenta cuántas imágenes de la carpeta del capítulo son archivos
    realmente abribles (no 0 bytes, no HTML de error guardado con
    extensión de imagen, no archivo truncado a mitad de descarga).
    """
    if not carpeta_cap.exists():
        return 0
    validas = 0
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif"):
        for f in carpeta_cap.glob(ext):
            try:
                if f.stat().st_size == 0:
                    continue
                with Image.open(f) as img:
                    img.verify()
                validas += 1
            except Exception:
                continue
    return validas

def registrar_capitulo(carpeta_manga: Path, numero, esperadas: int, validas: int,
                        fuente: str = None, prioridad_fuente=None):
    """
    Guarda en el registro si el capítulo quedó 'completado' (todas las
    imágenes esperadas están íntegras) o 'parcial' (faltan algunas).

    'fuente' y 'prioridad_fuente' son opcionales y existen para el
    sistema multi-fuente (ver §5d): permiten que, cuando el mismo manga
    está trackeado desde 2+ sitios distintos apuntando a la misma
    carpeta, una fuente de mejor prioridad sepa si ya hay una versión
    de igual o mejor calidad antes de volver a descargar algo.
    Si no se pasan (caso normal, manga de una sola fuente), quedan en
    None y no cambian nada del comportamiento existente.
    """
    reg   = cargar_registro_progreso(carpeta_manga)
    clave = str(numero)
    estado = "completado" if validas >= esperadas and esperadas > 0 else "parcial"
    reg[clave] = {
        "estado":           estado,
        "esperadas":        esperadas,
        "validas":          validas,
        "fecha":            datetime.now().isoformat(),
        "fuente":           fuente,
        "prioridad_fuente": prioridad_fuente,
    }
    guardar_registro_progreso(carpeta_manga, reg)
    return estado

def capitulo_esta_completo(carpeta_manga: Path, numero) -> bool:
    """
    True solo si el registro dice 'completado' para este capítulo.
    Si no hay registro (manga descargado con una versión anterior del
    scraper, antes de este sistema), se hace una verificación de
    integridad directa sobre disco como fallback, y se crea el
    registro a partir de ese resultado para no repetir el trabajo
    en cada escaneo.
    """
    reg    = cargar_registro_progreso(carpeta_manga)
    clave  = str(numero)
    info   = reg.get(clave)

    carpeta_cap = carpeta_manga / nombre_capitulo(numero)
    if not carpeta_cap.exists():
        return False

    if info and info.get("estado") == "completado":
        return True
    if info and info.get("estado") == "parcial":
        return False

    # Sin registro previo — fallback: verificar integridad directa
    # y completar el registro con lo que encontremos (asumimos que
    # lo que hay en disco es lo "esperado" porque no tenemos otro dato).
    validas = verificar_imagenes_integras(carpeta_cap)
    if validas == 0:
        return False
    registrar_capitulo(carpeta_manga, numero, esperadas=validas, validas=validas)
    return True

def regenerar_registro_completo(carpeta_manga: Path) -> int:
    """
    Recorre TODAS las carpetas 'Capitulo_X' de un manga de una sola vez
    y construye registro_progreso.json desde cero, basándose en lo que
    hay realmente en disco (verificación de integridad por imagen).

    Pensado para el caso de "descargué este manga aparte y lo copié a
    la carpeta del scraper": no hay registro_progreso.json todavía, y
    sin esta función el sistema lo iría completando de a un capítulo
    por vez recién cuando cada uno se necesita (lo cual funciona pero
    es más lento de ver progresar y no deja un registro consistente
    de una sola pasada).

    Se asume que las imágenes que ya están en disco son las "esperadas"
    para ese capítulo (no hay otro dato con el que compararlas) — así
    que todo capítulo con al menos una imagen íntegra queda marcado
    'completado'. Si más adelante el scraper detecta que en realidad
    le faltan páginas (porque la fuente original tenía más), ese
    capítulo se reintenta normalmente y el registro se corrige solo.

    Retorna la cantidad de capítulos que quedaron registrados.
    """
    if not carpeta_manga.exists():
        return 0

    reg = cargar_registro_progreso(carpeta_manga)
    if reg:
        # Ya hay un registro — no hace falta regenerar nada.
        return len(reg)

    capitulos_encontrados = []
    for sub in carpeta_manga.iterdir():
        if not sub.is_dir() or not sub.name.startswith("Capitulo_"):
            continue
        m = re.search(r"Capitulo_(\d+(?:\.\d+)?)", sub.name)
        if not m:
            continue
        capitulos_encontrados.append(numero_a_float(m.group(1)))

    if not capitulos_encontrados:
        return 0

    log.info(f"  📋 Sin registro_progreso.json en '{carpeta_manga.name}' — "
             f"generando uno a partir de {len(capitulos_encontrados)} "
             f"capítulo(s) ya en disco...")

    nuevo_reg = {}
    for numero in sorted(capitulos_encontrados):
        carpeta_cap = carpeta_manga / nombre_capitulo(numero)
        validas = verificar_imagenes_integras(carpeta_cap)
        estado  = "completado" if validas > 0 else "parcial"
        nuevo_reg[str(numero)] = {
            "estado":           estado,
            "esperadas":        validas,
            "validas":          validas,
            "fecha":            datetime.now().isoformat(),
            "fuente":           FUENTE_EXTERNA,
            "prioridad_fuente": PRIORIDAD_EXTERNA,
        }

    guardar_registro_progreso(carpeta_manga, nuevo_reg)
    n_completos = sum(1 for v in nuevo_reg.values() if v["estado"] == "completado")
    log.info(f"  📋 Registro generado: {n_completos}/{len(nuevo_reg)} "
             f"capítulo(s) marcados como completos")
    return len(nuevo_reg)

def numero_a_float(s) -> float:
    """Convierte '12', '12.5', 'Capitulo 12' etc. a float."""
    try:
        return float(s)
    except (ValueError, TypeError):
        m = re.search(r"(\d+(?:\.\d+)?)", str(s))
        return float(m.group(1)) if m else 0.0

def marcar_capitulo_con_error(manga_cfg: dict, numero) -> None:
    """
    Agrega un número de capítulo a manga_cfg['capitulos_con_error'].

    Existe porque registro_progreso.json (en la carpeta del manga) NO
    cubre el caso de fallo total: si un capítulo no logra guardar NI
    UNA imagen, descargar_capitulo() borra la carpeta entera y nunca
    llega a escribir nada en el registro — ese capítulo desaparece sin
    dejar rastro. Sin esta lista en seguimiento.json, un capítulo que
    falla por debajo de ultimo_capitulo (porque otros capítulos
    posteriores sí bajaron bien) queda perdido para siempre, ya que el
    filtro normal solo mira "número > ultimo_capitulo".

    El campo se guarda como lista de floats, o ausente/None si está
    vacío — nunca como lista vacía [] ni como 0, para no confundirlo
    con un capítulo 0 real que algunos mangas sí tienen.
    """
    numero = float(numero)
    lista = manga_cfg.get("capitulos_con_error") or []
    if numero not in lista:
        lista.append(numero)
        lista.sort()
    manga_cfg["capitulos_con_error"] = lista if lista else None

def desmarcar_capitulo_con_error(manga_cfg: dict, numero) -> None:
    """Quita un capítulo de la lista de errores (ya se descargó bien)."""
    numero = float(numero)
    lista = manga_cfg.get("capitulos_con_error") or []
    if numero in lista:
        lista.remove(numero)
    manga_cfg["capitulos_con_error"] = lista if lista else None

def capitulos_pendientes_por_error(manga_cfg: dict) -> list[float]:
    """Lista de números marcados con error, vacía si no hay ninguno."""
    return list(manga_cfg.get("capitulos_con_error") or [])

# ══════════════════════════════════════════════════════════════════════
# §5a  SPEED TRACKER
# ══════════════════════════════════════════════════════════════════════

class SpeedTracker:
    """
    Mide bytes descargados y velocidad (MB/s) de forma segura entre hilos.
    Reemplaza el cálculo improvisado con speed_bytes[0]/speed_lock que
    tenía descargar_capitulo, centralizado y reutilizable.
    """
    def __init__(self):
        self._lock   = threading.Lock()
        self._bytes  = 0
        self._inicio = time.time()

    def add(self, n: int):
        with self._lock:
            self._bytes += n

    @property
    def mbps(self) -> float:
        elapsed = max(time.time() - self._inicio, 0.01)
        with self._lock:
            return round(self._bytes / (1024 * 1024) / elapsed, 2)

    @property
    def total_mb(self) -> float:
        with self._lock:
            return round(self._bytes / (1024 * 1024), 2)


# ══════════════════════════════════════════════════════════════════════
# §5b  BLOCK DETECTOR
# ══════════════════════════════════════════════════════════════════════

class BlockDetector:
    """
    Detecta bloqueos del servidor contando errores *consecutivos* a nivel
    de petición individual (no de hilo), de forma INDEPENDIENTE por
    dominio. Así, si un CDN específico (ej: img2mw.xyz) empieza a fallar
    pero otro (ej: imageshack.com) sigue andando bien, solo se pausa
    el dominio problemático — el resto de las descargas no se frena.

    - El contador de cada dominio se resetea al iniciar cada capítulo.
    - Como la descarga es paralela (varios hilos), N imágenes que fallan
      simultáneamente no deben contar como N reintentos consecutivos
      reales — usamos un umbral y solo disparamos pausa si se supera
      sin que haya habido ningún éxito en medio para ESE dominio.
    - El sleep de pausa ocurre fuera del lock para no bloquear a otros
      hilos que están esperando en esperar_si_pausado() de OTRO dominio.
    """
    def __init__(self, max_errs: int = 8, pausa_seg: int = 30):
        self._lock     = threading.Lock()
        self._estado: dict[str, dict] = {}  # dominio -> {n_err, event, pausas}
        self.max_errs  = max_errs
        self.pausa_seg = pausa_seg

    def _get(self, dominio: str) -> dict:
        with self._lock:
            if dominio not in self._estado:
                ev = threading.Event()
                ev.set()
                self._estado[dominio] = {"n_err": 0, "event": ev, "pausas": 0}
            return self._estado[dominio]

    def reset(self):
        """Llamar al inicio de cada capítulo para limpiar todos los contadores."""
        with self._lock:
            for info in self._estado.values():
                info["n_err"]  = 0
                info["pausas"] = 0
                info["event"].set()

    def esperar_si_pausado(self, dominio: str = "_global"):
        self._get(dominio)["event"].wait()

    def registrar_error(self, dominio: str = "_global"):
        info = self._get(dominio)
        with self._lock:
            info["n_err"] += 1
            lanzar = (info["n_err"] >= self.max_errs) and info["event"].is_set()
            if lanzar:
                info["event"].clear()
                info["pausas"] += 1

        if lanzar:
            log.warning(f"  🚫 Posible bloqueo en '{dominio}' "
                       f"({info['n_err']} errores consec.) "
                       f"— pausando {self.pausa_seg}s...")
            time.sleep(self.pausa_seg)
            with self._lock:
                info["n_err"] = 0
            log.info(f"  ▶️  Reanudando descarga de '{dominio}'...")
            info["event"].set()

    def registrar_exito(self, dominio: str = "_global"):
        info = self._get(dominio)
        with self._lock:
            info["n_err"] = 0

    @property
    def veces_pausado(self) -> int:
        with self._lock:
            return sum(info["pausas"] for info in self._estado.values())


class SitioRotoError(Exception):
    """
    Se lanza cuando un scraper detecta que el sitio probablemente cambió
    su estructura HTML (selectores que antes funcionaban ya no encuentran
    nada), en vez de que el manga genuinamente no tenga capítulos nuevos.

    Es importante diferenciarla de "0 capítulos nuevos" porque si se
    confunden, el scraper puede dejar de actualizar TODO un sitio en
    silencio sin que el reporte.html lo distinga de un día normal.
    """
    pass


# Códigos de error típicos de almacenamiento compatible con S3 (AWS S3,
# Cloudflare R2, MinIO, etc.) que indican un problema PERMANENTE del
# lado del servidor (bucket privado, credenciales/firma faltante o
# vencida, objeto inexistente) — no un bloqueo temporal. Reintentar o
# pausar no cambia nada acá, así que conviene rendirse de inmediato en
# vez de gastar los reintentos/pausas pensados para bloqueos transitorios
# (429/503). Genérico a propósito: no depende de qué dominio lo sirva,
# para que sirva igual si aparece en cualquier sitio, actual o futuro.
_CODIGOS_ALMACENAMIENTO_FATAL = (
    "AccessDenied", "InvalidArgument", "InvalidAccessKeyId",
    "SignatureDoesNotMatch", "ExpiredToken", "NoSuchKey",
    "AllAccessDisabled",
)

def _es_error_almacenamiento_privado(r) -> bool:
    """True si la respuesta es un XML de error estilo S3/R2 indicando
    que el objeto/bucket no es accesible públicamente (permanente)."""
    if r.status_code not in (400, 401, 403, 404):
        return False
    cuerpo = r.content[:500].decode("utf-8", errors="ignore")
    if "<Error>" not in cuerpo:
        return False
    return any(codigo in cuerpo for codigo in _CODIGOS_ALMACENAMIENTO_FATAL)


# ══════════════════════════════════════════════════════════════════════
# §6  CLASE BASE
# ══════════════════════════════════════════════════════════════════════

class ScraperBase(ABC):
    """Clase base para todos los scrapers de sitios."""

    def __init__(self):
        self.detector_bloqueo = BlockDetector(
            max_errs=BLOQUEO_MAX_ERRORES_CONSECUTIVOS,
            pausa_seg=BLOQUEO_PAUSA_SEG,
        )
        self.session = requests.Session()
        self.session.headers.update(HEADERS_BASE)

    @property
    @abstractmethod
    def nombre(self) -> str:
        ...

    @abstractmethod
    def obtener_capitulos(self, slug: str) -> list[dict]:
        """
        Retorna lista de capítulos disponibles.
        Cada elemento: {"numero": float, "url": str, "titulo": str}
        Ordenados de menor a mayor.
        """
        ...

    @abstractmethod
    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        """
        Retorna lista de URLs de imágenes del capítulo.
        """
        ...

    def descargar_capitulo(self, cap: dict, carpeta_manga: Path) -> bool:
        """
        Descarga un capítulo con:
        - Descarga paralela (4 hilos)
        - Filtros adaptativos por sitio (ancho dominante, ratio, tamaño)
        - Tabla de resoluciones estilo script original
        - Velocidad en MB/s
        - tqdm coordinado con logging (sin mezcla)
        """
        num            = cap["numero"]
        url            = cap["url"]
        slug           = cap.get("slug", "")
        fuente         = cap.get("fuente", "")
        # tipo_contenido: "manga" activa modo flexible (solo filtra <100px).
        # Cualquier otra fuente puede usarlo — no es exclusivo de manhwaweb.
        es_manga_flex  = cap.get("tipo_contenido") == "manga"
        nombre  = nombre_capitulo(num)
        num_str = str(int(num)) if num == int(num) else str(num)
        destino = carpeta_manga / nombre

        # Filtros por sitio (definidos a nivel de módulo, junto a ORDEN_FUENTES)
        if fuente not in FILTROS:
            log.warning(f"  ⚠  '{fuente}' no tiene perfil propio en FILTROS — "
                       f"usando el de 'dragon' por defecto. Convendría agregarle "
                       f"uno específico una vez que se vea qué resoluciones trae.")
        F = FILTROS.get(fuente, FILTROS["dragon"])

        tqdm.write(f"\n{'═'*65}")
        tqdm.write(f"  📖  CAPÍTULO {num_str}  [{self.nombre}]")
        tqdm.write(f"{'─'*65}")
        tqdm.write(f"  🔗 {url}")

        # Obtener URLs de imágenes
        urls_imgs = self.obtener_imagenes(url, slug, num)
        if not urls_imgs:
            tqdm.write("  ❌ Sin imágenes candidatas")
            return False

        # Deduplicar
        visto_u = set(); urls_unicas = []
        for u in urls_imgs:
            if u not in visto_u:
                visto_u.add(u); urls_unicas.append(u)
        n_dup = len(urls_imgs) - len(urls_unicas)
        tqdm.write(f"  🔎 Candidatos: {len(urls_unicas)}"
                   + (f"  (🗑 {n_dup} duplicados)" if n_dup else ""))

        destino.mkdir(parents=True, exist_ok=True)

        # ── Descarga paralela ─────────────────────────────────────────
        speed       = SpeedTracker()
        self.detector_bloqueo.reset()
        _dominios_almacenamiento_avisados: set[str] = set()

        def _bajar_raw(args):
            i, img_url = args
            from urllib.parse import urlparse as _up
            dominio = _up(img_url).netloc.lower()
            self.detector_bloqueo.esperar_si_pausado(dominio)
            if DELAY_ENTRE_IMGS > 0:
                time.sleep(DELAY_ENTRE_IMGS)
            headers_extra = {}
            if "imageshack" in dominio:
                headers_extra = {
                    "Referer":       "https://imageshack.com/",
                    "Accept":        "image/webp,image/apng,image/*,*/*;q=0.8",
                    "Cache-Control": "no-cache",
                }
            elif "img2mw" in dominio:
                headers_extra = {
                    "Referer": "https://manhwaweb.com/",
                    "Accept":  "image/webp,image/apng,image/*,*/*;q=0.8",
                }
            for intento in range(1, 4):
                try:
                    r = self.session.get(img_url, timeout=TIMEOUT,
                                         stream=False, headers=headers_extra)
                    # Bucket S3/R2 privado o credenciales faltantes: error
                    # PERMANENTE, no tiene sentido reintentar ni avisarle
                    # al detector de bloqueo (eso solo gastaría 30s de pausa
                    # por las puras). Se rinde de inmediato para esta imagen.
                    if _es_error_almacenamiento_privado(r):
                        if dominio not in _dominios_almacenamiento_avisados:
                            _dominios_almacenamiento_avisados.add(dominio)
                            log.warning(
                                f"  [⚠] '{dominio}' — error de almacenamiento "
                                f"permanente (bucket privado o credenciales "
                                f"inválidas). No se reintenta, se omite."
                            )
                        return i, img_url, None, False
                    # 402 = bandwidth agotado (ImageShack), 403 = bloqueado
                    # No tiene sentido reintentar estos errores, pero SÍ
                    # cuentan para el detector de bloqueo si es 403/429/503
                    if r.status_code in (402, 403, 410):
                        if r.status_code == 402:
                            log.warning(f"  [⚠] Bandwidth agotado en CDN externo: {dominio}")
                        if r.status_code in (403, 410):
                            self.detector_bloqueo.registrar_error(dominio)
                        return i, img_url, None, False
                    if r.status_code in (429, 503):
                        self.detector_bloqueo.registrar_error(dominio)
                        if intento < 3:
                            time.sleep(2 ** intento)  # backoff: 2s, 4s
                            continue
                        return i, img_url, None, False
                    r.raise_for_status()
                    contenido = self.procesar_imagen(img_url, r.content)
                    speed.add(len(contenido))
                    self.detector_bloqueo.registrar_exito(dominio)
                    return i, img_url, contenido, True
                except Exception:
                    if intento < 3:
                        time.sleep(2 ** intento)  # backoff: 2s, 4s
            self.detector_bloqueo.registrar_error(dominio)
            return i, img_url, None, False

        barra_dl = None
        if TQDM_OK:
            barra_dl = tqdm(
                total=len(urls_unicas), unit="img",
                desc=f"  Cap {num_str}",
                ncols=70,
                bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}] {postfix}",
                file=sys.stdout,
            )

        resultados = {}
        with ThreadPoolExecutor(max_workers=4) as exe:
            futuros = {exe.submit(_bajar_raw, (i, u)): i
                       for i, u in enumerate(urls_unicas)}
            for fut in as_completed(futuros):
                i, img_url, datos, ok = fut.result()
                resultados[i] = (img_url, datos, ok)
                if barra_dl:
                    barra_dl.set_postfix(MB=f"{speed.total_mb:.1f}", vel=f"{speed.mbps}MB/s")
                    barra_dl.update(1)

        if barra_dl:
            barra_dl.close()

        tqdm.write(f"\n  📶 {speed.mbps} MB/s  │  {speed.total_mb} MB")

        # ── Cargar imágenes en memoria y calcular dimensiones ─────────
        imgs_ok = {}
        fallidas_desc = 0
        for idx in sorted(resultados):
            img_url, datos, ok = resultados[idx]
            if not ok or datos is None:
                fallidas_desc += 1
                continue
            try:
                imagen = Image.open(BytesIO(datos))
                ancho, alto = imagen.size
                imgs_ok[idx] = {"url": img_url, "bytes": datos,
                                 "ancho": ancho, "alto": alto}
            except Exception:
                fallidas_desc += 1

        if not imgs_ok:
            tqdm.write("  ❌ Ninguna imagen válida")
            return False

        # ── Calcular perfil del capítulo ──────────────────────────────
        anchos  = sorted(v["ancho"] for v in imgs_ok.values())
        ratios  = sorted(v["ancho"]/v["alto"] for v in imgs_ok.values() if v["alto"] > 0)
        def _med(lst): n=len(lst); return lst[n//2] if n else 0
        ancho_dom  = Counter(anchos).most_common(1)[0][0] if anchos else 0
        ratio_med  = _med(ratios)
        anchos_uni = set(anchos)
        n_wt = sum(1 for r in ratios if r < 0.4)
        tipo = "WEBTOON" if n_wt > len(ratios)/2 else "MANGA"

        modo_label = " [flex]" if es_manga_flex else ""
        tqdm.write(f"  📐 Perfil: {tipo}{modo_label}  ancho_dom={ancho_dom}px  "
                   f"ratio_med={ratio_med:.2f}  ({len(anchos_uni)} anchos distintos)")

        # ── Tabla de filtrado y guardado ──────────────────────────────
        tqdm.write(f"\n  {'─'*62}")
        tqdm.write(f"  {'#':>4}  {'Resolución':^13}  Estado")
        tqdm.write(f"  {'─'*62}")

        guardadas = rechazadas = corrompidas = 0
        contador  = 1
        rechazadas_meta = []
        RUIDO_FN  = ["banner","logo-","zzz-","promo","discord","patreon","default_profile"]
        RUIDO_URL = ["storage/teams","storage/comics/covers","/ads/","/icon/","/avatar/","/social/"]

        for idx in sorted(imgs_ok):
            meta    = imgs_ok[idx]
            img_url = meta["url"]
            datos   = meta["bytes"]
            ancho   = meta["ancho"]
            alto    = meta["alto"]
            ratio   = ancho / alto if alto > 0 else 0
            pref    = f"  {contador:>4}  {ancho}x{alto:<7}  "

            # Filtrar ruido por nombre/URL
            fname   = img_url.split("/")[-1].lower()
            url_low = img_url.lower()
            if any(x in fname for x in RUIDO_FN) or any(x in url_low for x in RUIDO_URL):
                tqdm.write(f"{pref}❌ Rechazada (ruido-url)")
                rechazadas += 1
                rechazadas_meta.append((idx, meta, "ruido-url"))
                continue

            # Filtros adaptativos
            motivo = None
            if es_manga_flex:
                # Modo manga flexible: el único rechazo posible es una imagen
                # rotundamente inútil (rota, minúscula, pixel de tracking).
                # No hay límite de ancho máximo, no hay ratio_max, no hay
                # fuera-perfil — un manga puede tener paneles a color, páginas
                # dobles o capítulos escaneados con otro equipo y todos son
                # páginas reales que hay que guardar.
                if ancho < 100 or alto < 100:
                    motivo = f"muy-pequeña:{ancho}x{alto}"
            else:
                if ancho < F["ancho_min"] or alto < F["alto_min"]:
                    motivo = f"muy-pequeña:{ancho}x{alto}"
                elif ratio > F["ratio_max"]:
                    motivo = f"banner:{ratio:.2f}"
                elif 0.8 < ratio < 1.2 and ancho < F["cuadrado_max"] and alto < F["cuadrado_max"]:
                    motivo = f"icono-cuadrado:{ancho}x{alto}"
                elif ancho_dom > 0 and alto > F["alto_min"]:
                    tol = F["tol_pct"] / 100.0
                    if tipo == "WEBTOON":
                        tol = max(tol, 0.35)
                    if ancho < ancho_dom*(1-tol) or ancho > ancho_dom*(1+tol):
                        motivo = f"fuera-perfil:{ancho}px(dom={ancho_dom}±{int(tol*100)}%)"

            if motivo:
                tqdm.write(f"{pref}❌ Rechazada ({motivo})")
                rechazadas += 1
                rechazadas_meta.append((idx, meta, motivo))
                continue

            # Guardar
            try:
                if "#scramble=" in img_url:
                    ext = "png"
                else:
                    ext = img_url.split(".")[-1].split("?")[0][:4]
                if ext not in ["jpg","jpeg","png","webp","gif"]:
                    ext = "webp"
                nombre_img = f"{contador:03d}.{ext}"
                ruta = destino / nombre_img
                with open(ruta, "wb") as f:
                    f.write(datos)
                # Verificar integridad
                try:
                    img_test = Image.open(ruta); img_test.load()
                    tqdm.write(f"{pref}✅ OK → {nombre_img}")
                except Exception as e:
                    tqdm.write(f"{pref}⚠  CORROMPIDA: {e}")
                    corrompidas += 1
                    ruta.unlink(missing_ok=True)
                    continue
                guardadas += 1
                contador  += 1
            except Exception as e:
                tqdm.write(f"{pref}❌ {e}")

        # ── Fallback si quedan muy pocas imágenes ────────────────────
        if rechazadas_meta and guardadas < F["fallback_min"]:
            tqdm.write(f"\n  ⚠  Solo {guardadas} imgs — activando modo RELAJADO "
                       f"({len(rechazadas_meta)} a revisar)...")
            recuperadas = 0
            for idx, meta, motivo_orig in rechazadas_meta:
                img_url = meta["url"]; datos = meta["bytes"]
                ancho = meta["ancho"]; alto = meta["alto"]
                ratio = ancho / alto if alto > 0 else 0
                pref  = f"  {contador:>4}  {ancho}x{alto:<7}  "
                if ratio > F["ratio_max"] * 1.5:
                    tqdm.write(f"{pref}❌ Sigue rechazada (ratio-extremo)")
                    continue
                try:
                    if "#scramble=" in img_url:
                        ext = "png"
                    else:
                        ext = img_url.split(".")[-1].split("?")[0][:4]
                    if ext not in ["jpg","jpeg","png","webp","gif"]: ext = "webp"
                    nombre_img = f"{contador:03d}.{ext}"
                    ruta = destino / nombre_img
                    with open(ruta, "wb") as f: f.write(datos)
                    tqdm.write(f"{pref}♻  Recuperada (orig: {motivo_orig}) → {nombre_img}")
                    guardadas += 1; contador += 1; recuperadas += 1
                except Exception as e:
                    tqdm.write(f"{pref}❌ {e}")
            if recuperadas:
                tqdm.write(f"  ♻  Recuperadas en fallback: {recuperadas}")

        tqdm.write(f"  {'─'*62}")

        if guardadas == 0:
            tqdm.write(f"\n  ❌ Cap {num_str}: sin imágenes guardadas")
            shutil.rmtree(destino, ignore_errors=True)
            return False

        extras = []
        if fallidas_desc: extras.append(f"⚠ {fallidas_desc} sin descargar")
        if corrompidas:   extras.append(f"⚠ {corrompidas} corrompidas")
        if rechazadas:    extras.append(f"🚫 {rechazadas} filtradas")
        if self.detector_bloqueo.veces_pausado:
            extras.append(f"🚫 {self.detector_bloqueo.veces_pausado} pausa(s) anti-bloqueo")
        tqdm.write(f"\n  ✅ Cap {num_str}: {guardadas} imgs"
                   + ("  |  " + "  ".join(extras) if extras else ""))

        # Registrar el resultado: 'completado' si no hubo nada que se
        # cayera por descarga/corrupción (las filtradas por ruido/perfil
        # no cuentan como "esperadas" — son basura que el sitio mezcla,
        # no páginas reales del capítulo).
        esperadas_reales = len(urls_unicas) - rechazadas
        estado = registrar_capitulo(carpeta_manga, num,
                                    esperadas=max(esperadas_reales, guardadas),
                                    validas=guardadas,
                                    fuente=cap.get("fuente"),
                                    prioridad_fuente=cap.get("prioridad_fuente"))
        if estado == "parcial":
            tqdm.write("  ⚠  Registrado como PARCIAL — se reintentará "
                      "lo faltante en el próximo escaneo")

        return True

    def procesar_imagen(self, img_url: str, datos: bytes) -> bytes:
        """
        Hook de post-procesado de bytes ya descargados (antes de calcular
        dimensiones y guardar). Por defecto no hace nada — los scrapers que
        necesiten transformar la imagen (ej: descramble) lo sobreescriben.
        """
        return datos

# ══════════════════════════════════════════════════════════════════════
# §7  OLYMPUS SCANLATION
# ══════════════════════════════════════════════════════════════════════

class OlympusScraper(ScraperBase):
    """
    Olympus Scanlation.

    ENFOQUE: En vez de consultar cada manga por separado, se scrapea
    /capitulos?page=1..10 para obtener todos los capítulos publicados
    recientemente. Esto es más eficiente y siempre usa el slug actual
    de la página — nunca el slug vencido guardado en seguimiento.json.

    Estructura de URL de capítulo en Olympus:
      https://olympusxyz.com/capitulo/{ID}/{slug-del-manga}
    El ID numérico es necesario para obtener las imágenes vía API.
    """

    WEB_BASE      = "https://olympusxyz.com"
    BASE_REDIRECT = "https://olympus.pages.dev"
    FALLBACK_BASE = "https://olympusxyz.com"
    MAX_PAGINAS   = OLYMPUS_MAX_PAGINAS_NOVEDADES

    def __init__(self):
        super().__init__()
        self._api_base       = None
        self._manga_id_cache = {}  # cap_id -> manga_id

    @property
    def nombre(self) -> str:
        return "Olympus Scanlation"

    def _resolver_dominio(self) -> str:
        """
        Dominio de la API del backend de Olympus.

        Antes esto intentaba resolver dinámicamente vía
        BASE_REDIRECT (olympus.pages.dev), pero esa página no
        siempre redirige al dominio real y producía URLs rotas
        como "dashboard.olympus.pages.dev" (que no existe).
        El dominio real, confirmado directamente capturando los
        requests del navegador, es siempre dashboard.olympusxyz.com.
        """
        if self._api_base:
            return self._api_base
        dominio = urlparse(self.FALLBACK_BASE).netloc  # olympusxyz.com
        self._api_base = f"https://dashboard.{dominio}/api"
        log.info(f"  [Olympus] API base: {self._api_base}")
        return self._api_base

    def obtener_novedades(self) -> list[dict]:
        """
        Scrapea /capitulos?page=1..MAX_PAGINAS y retorna todos los capítulos
        publicados recientemente.

        Cada entrada: {
            "manga_slug":  str,   # slug del manga (sin 'comic-')
            "manga_titulo": str,  # nombre visible del manga
            "cap_id":      str,   # ID numérico del capítulo
            "cap_numero":  float, # número del capítulo
            "cap_url":     str,   # URL completa del capítulo
        }
        """
        novedades = []
        vistos    = set()  # evitar duplicados

        for page in range(1, self.MAX_PAGINAS + 1):
            url = f"{self.WEB_BASE}/capitulos?page={page}"
            r   = hacer_get(url, self.session)
            if not r:
                break

            soup = BeautifulSoup(r.text, "html.parser")

            # Cada tarjeta de capítulo en /capitulos
            # URL típica: /capitulo/129489/comic-el-rey-demonio-tiene-18-anos
            for a in soup.select("a[href*='/capitulo/']"):
                href = a.get("href", "")
                # Extraer ID y slug del manga desde la URL
                m = re.search(r"/capitulo/(\d+)/comic-(.+?)(?:\?|$)", href)
                if not m:
                    continue

                cap_id     = m.group(1)
                manga_slug = m.group(2).strip("/")

                # Número del capítulo — buscar en el texto del enlace o atributos cercanos
                texto = a.get_text(" ", strip=True)
                m_num = re.search(r"[Cc]ap(?:ítulo|itulo|\.?\s*)\.?\s*(\d+(?:\.\d+)?)", texto)
                if not m_num:
                    # Buscar cualquier número en el texto
                    m_num = re.search(r"(\d+(?:\.\d+)?)", texto)
                cap_num = float(m_num.group(1)) if m_num else 0.0

                # Título del manga — buscar en elemento padre o hermano
                titulo = ""
                padre  = a.find_parent(["div", "li", "article"])
                if padre:
                    for sel in ["h3", "h4", ".manga-title", ".title", "[class*='title']", "[class*='nombre']"]:
                        t = padre.select_one(sel)
                        if t and t.get_text(strip=True):
                            titulo = t.get_text(strip=True)
                            break
                if not titulo:
                    titulo = manga_slug.replace("-", " ").title()

                clave = f"{manga_slug}_{cap_id}"
                if clave in vistos:
                    continue
                vistos.add(clave)

                novedades.append({
                    "manga_slug":   manga_slug,
                    "manga_titulo": titulo,
                    "cap_id":       cap_id,
                    "cap_numero":   cap_num,
                    "cap_url":      f"{self.WEB_BASE}/capitulo/{cap_id}/comic-{manga_slug}",
                })

            # Si no hay más páginas (sin botón "siguiente")
            if not soup.select_one("a[href*='page='][rel='next'], .pagination .next, a.next"):
                if page > 1:
                    break

            time.sleep(0.5)

        log.info(f"  [Olympus] {len(novedades)} capítulos encontrados en {self.MAX_PAGINAS} páginas")
        return novedades

    def _extraer_nombre_base(self, slug: str) -> str:
        """
        Quita el timestamp del slug para quedarnos con el nombre legible.
        'caballero-en-eterna-regresion-20260611-080407883'
        → 'caballero en eterna regresion'
        """
        s = re.sub(r"-\d{6,}-\d{6,}$", "", slug)
        s = re.sub(r"-\d{8,}$", "", s)
        return s.strip("-").replace("-", " ")

    def _buscar_slug_vigente(self, slug_vencido: str) -> str | None:
        """
        Si el slug guardado ya no resuelve, busca en las páginas de
        novedades (/capitulos) un slug cuyo nombre base coincida.
        Como obtener_novedades() ya escanea MAX_PAGINAS páginas,
        lo reutilizamos como fuente de slugs vigentes.
        """
        nombre_base = self._extraer_nombre_base(slug_vencido)
        palabras    = set(nombre_base.lower().split())
        if not palabras:
            return None

        novedades = self.obtener_novedades()
        candidatos = {}
        for nov in novedades:
            candidatos.setdefault(nov["manga_slug"], nov.get("manga_id", ""))

        mejor_slug = None
        mejor_score = 0.0
        for cand_slug in candidatos:
            cand_nombre  = self._extraer_nombre_base(cand_slug)
            cand_palabras = set(cand_nombre.lower().split())
            if not cand_palabras:
                continue
            interseccion = len(palabras & cand_palabras)
            score = interseccion / max(len(palabras), len(cand_palabras))
            if score > mejor_score:
                mejor_score = score
                mejor_slug  = cand_slug

        if mejor_slug and mejor_score >= 0.6:
            log.info(f"  [Olympus] Slug vencido → recuperado por nombre "
                     f"(score={mejor_score:.2f}): {mejor_slug}")
            return mejor_slug
        return None

    def obtener_capitulos(self, slug: str, manga_cfg: dict = None,
                          reporte_global: "ReporteEscaneo" = None) -> list[dict]:
        """
        Lista TODOS los capítulos de un manga consultando la API real
        del backend (dashboard.olympusxyz.com), no el HTML de la página
        de la serie — esa página es una SPA Nuxt/Vue, el HTML estático
        no contiene la lista de capítulos (solo aparece un link
        "Primer capítulo" sin número, que rompía el parseo anterior).

        Endpoint real (descubierto via Network tab del navegador):
          GET https://dashboard.olympusxyz.com/api/series/{slug}/chapters
              ?page={n}&direction=desc&type=comic

        Respuesta paginada estilo Laravel:
          {"data": [{"name": "40", "id": 129643, ...}, ...],
           "meta": {"current_page": 1, "last_page": 2, "total": 42}}

        Si el slug guardado ya no resuelve (404), intenta recuperar
        el slug vigente buscando por nombre entre las páginas de
        actualizaciones recientes (/capitulos), y actualiza
        seguimiento.json automáticamente si lo encuentra.
        """
        slug_norm = self._limpiar_slug_inicial(slug)
        slug_usar = slug_norm
        api       = self._resolver_dominio()

        def _pedir_pagina(slug_actual: str, page: int, reintentos: int = 3):
            """
            GET a la API de capítulos, con reintentos ante errores de
            SERVIDOR (5xx, incluyendo el 525 de Cloudflare "SSL Handshake
            Failed" que confirmamos real contra Olympus) — esos son
            transitorios y no significan que el slug esté vencido, a
            diferencia de un 404 que sí es una señal real y definitiva.

            Sin esto, un error transitorio del servidor se interpretaba
            igual que "el slug no existe", lo cual disparaba la búsqueda
            de slug vigente innecesariamente — y aunque encontrara el
            slug correcto (score 1.00), el reintento podía volver a
            pegarle al mismo error transitorio y reportar "no se pudo
            recuperar" sobre un slug que en realidad estaba bien.
            """
            url = (f"{api}/series/{slug_actual}/chapters"
                   f"?page={page}&direction=desc&type=comic")
            headers = {"Accept": "application/json",
                      "Origin": self.WEB_BASE,
                      "Referer": f"{self.WEB_BASE}/"}
            for intento in range(1, reintentos + 1):
                try:
                    r = self.session.get(url, timeout=TIMEOUT, headers=headers)
                    if r.status_code >= 500 and intento < reintentos:
                        espera = 3 * intento
                        log.warning(f"  [Olympus] Error de servidor "
                                   f"{r.status_code} consultando '{slug_actual}' "
                                   f"(intento {intento}/{reintentos}) — "
                                   f"reintentando en {espera}s...")
                        time.sleep(espera)
                        continue
                    return r
                except Exception as e:
                    if intento < reintentos:
                        time.sleep(3 * intento)
                        continue
                    # Distinguir un error de RED real (timeout, DNS, SSL a
                    # nivel de conexión) de una respuesta HTTP normal —
                    # si no se loguea esto, un problema de conexión se ve
                    # exactamente igual que "el slug no resuelve".
                    log.warning(f"  [Olympus] Error de red consultando '{url}' "
                               f"({type(e).__name__}): {e}")
                    return None
            return None

        r = _pedir_pagina(slug_usar, 1)

        # Solo un 404 explícito es señal real de "slug vencido". Cualquier
        # otra cosa (5xx persistente tras reintentos, error de red) es un
        # problema transitorio — no tiene sentido buscar un slug nuevo si
        # el slug actual en realidad está bien y el servidor es el que
        # tuvo un problema puntual.
        slug_parece_vencido = (r is not None and r.status_code == 404)

        if not r or slug_parece_vencido:
            if not r:
                log.warning(f"  [Olympus] No se pudo consultar la API para "
                           f"'{slug_usar}' tras varios reintentos (problema "
                           f"de servidor o de red) — se reintentará en el "
                           f"próximo escaneo")
                return []

            log.warning(f"  [Olympus] Slug '{slug_usar}' no resuelve (404) — "
                       f"buscando slug vigente por nombre...")
            slug_nuevo = self._buscar_slug_vigente(slug_usar)
            if slug_nuevo:
                slug_usar = slug_nuevo
                r = _pedir_pagina(slug_usar, 1)
                if manga_cfg is not None and r and r.status_code == 200:
                    manga_cfg["slug"] = f"comic-{slug_usar}"
            if not r or r.status_code != 200:
                # No se pudo recuperar el slug automáticamente. El único
                # recurso automático que existe (buscar por nombre entre
                # las páginas de novedades recientes) depende de que el
                # manga haya tenido actividad reciente en TODO el sitio,
                # no solo en este manga puntual — si no salió ahí, no hay
                # forma de que el scraper lo encuentre solo (el buscador
                # interno de Olympus está fuera de servicio al día de
                # este código, así que tampoco se puede usar esa vía).
                #
                # Si la segunda consulta dio un error de SERVIDOR (no 404),
                # el slug recuperado probablemente esté bien — fue mala
                # suerte de timing con un problema transitorio. Avisar
                # eso de forma distinta a "definitivamente hace falta
                # actualizar el slug a mano", para no generar pánico
                # innecesario por un problema que se resuelve solo.
                nombre_legible = (manga_cfg.get("nombre_carpeta", slug)
                                  if manga_cfg else slug)

                if slug_nuevo and r and r.status_code >= 500:
                    log.warning(
                        f"  [Olympus] '{nombre_legible}' — se encontró el "
                        f"slug vigente ({slug_nuevo}) pero el servidor "
                        f"respondió {r.status_code} en el reintento. "
                        f"Probablemente un problema transitorio — se "
                        f"reintentará en el próximo escaneo, no hace falta "
                        f"acción manual por ahora."
                    )
                    if manga_cfg is not None:
                        manga_cfg["slug"] = f"comic-{slug_nuevo}"
                    return []

                log.error(
                    f"  ⚠️  [Olympus] '{nombre_legible}' — el slug guardado "
                    f"venció y no se pudo recuperar solo (no tuvo actividad "
                    f"reciente en las últimas {self.MAX_PAGINAS} páginas de "
                    f"novedades). ACCIÓN MANUAL NECESARIA: buscá el manga en "
                    f"https://olympusxyz.com/ y actualizá el campo 'slug' "
                    f"(o agregá 'url_manga' con la URL completa) en "
                    f"seguimiento.json para este manga."
                )
                if reporte_global is not None:
                    reporte_global.error(
                        nombre_legible, "olympus",
                        "🔧 Slug vencido sin recuperación automática — "
                        "actualizar 'slug' o 'url_manga' manualmente en "
                        "seguimiento.json"
                    )
                return []

        if r.status_code != 200:
            log.error(f"  [Olympus] La API de capítulos respondió "
                     f"{r.status_code} para '{slug_usar}'")
            return []

        try:
            primera = r.json()
        except Exception as e:
            raise SitioRotoError(
                f"La API de capítulos de '{slug_usar}' respondió 200 pero "
                f"el cuerpo no es JSON válido ({type(e).__name__}: {e}) — "
                f"posible cambio en el backend de Olympus"
            )

        datos_raw = primera.get("data", [])
        if not isinstance(datos_raw, list):
            raise SitioRotoError(
                f"La API de capítulos de '{slug_usar}' respondió JSON pero "
                f"sin el campo 'data' esperado como lista — posible cambio "
                f"de formato en el backend de Olympus"
            )

        meta        = primera.get("meta", {})
        last_page   = meta.get("last_page", 1)

        # Obtener manga_id de la portada, con el manga_id ya persistido
        # en seguimiento.json como respaldo si la portada no responde
        # esta vez. El manga_id es estable (no cambia con el tiempo,
        # a diferencia del slug textual), así que conviene guardarlo
        # una vez y no depender de poder volver a sacarlo cada escaneo.
        manga_id = ""
        rp = hacer_get(f"{self.WEB_BASE}/series/comic-{slug_usar}", self.session)
        if rp:
            m_id = re.search(r"/comics/covers/(\d+)/", rp.text)
            if m_id:
                manga_id = m_id.group(1)

        if not manga_id and manga_cfg is not None:
            manga_id = manga_cfg.get("manga_id", "")
            if manga_id:
                log.info(f"  [Olympus] Usando manga_id={manga_id} "
                         f"persistido (la portada no respondió esta vez)")

        if manga_id and manga_cfg is not None:
            manga_cfg["manga_id"] = manga_id

        def _parsear_pagina(data_list: list) -> list[dict]:
            caps_pag = []
            for item in data_list:
                if not isinstance(item, dict):
                    continue
                nombre_raw = item.get("name", "")
                cap_id_a   = item.get("id")
                if cap_id_a is None:
                    continue
                num = numero_a_float(nombre_raw)

                if manga_id:
                    self._manga_id_cache[str(cap_id_a)] = manga_id

                caps_pag.append({
                    "numero": num,
                    "url":    f"{self.WEB_BASE}/capitulo/{cap_id_a}/comic-{slug_usar}",
                    "titulo": f"Capítulo {nombre_raw}",
                    "slug":   slug_usar,
                    "fuente": "olympus",
                })
            return caps_pag

        caps = _parsear_pagina(datos_raw)

        # Traer el resto de las páginas si hay más de una
        for page in range(2, last_page + 1):
            rn = _pedir_pagina(slug_usar, page)
            if not rn or rn.status_code != 200:
                log.warning(f"  [Olympus] No se pudo traer la página "
                           f"{page}/{last_page} de capítulos de '{slug_usar}'")
                continue
            try:
                datos_pag = rn.json().get("data", [])
            except Exception:
                continue
            caps.extend(_parsear_pagina(datos_pag))

        # Deduplicar por número (por si la API repite algo entre páginas)
        vistos = {}
        for c in caps:
            vistos[c["numero"]] = c
        caps = list(vistos.values())
        caps.sort(key=lambda x: x["numero"])

        if not caps:
            log.warning(f"  [Olympus] La serie '{slug_usar}' no tiene "
                       f"ningún capítulo en la API")
        else:
            log.info(f"  [Olympus] {len(caps)} capítulo(s) encontrados "
                     f"para '{slug_usar}' (API, {last_page} página(s))")
        return caps

    def _limpiar_slug_inicial(self, slug: str) -> str:
        """Normaliza el slug guardado (con o sin 'comic-') al formato base."""
        s = slug.strip().lstrip("/")
        if s.startswith("series/"):
            s = s.split("series/", 1)[1]
        if s.startswith("comic-"):
            s = s[len("comic-"):]
        return s

    def obtener_imagenes(self, cap_url: str, slug: str = "", numero=None) -> list[str]:
        """
        Obtiene imágenes de un capítulo de Olympus.

        Estrategia, en orden:
          1. Scraping directo del HTML del capítulo. La página de
             lectura es server-side rendered: las imágenes están en
             tags <img src="..."> directamente en el HTML estático,
             SIN necesitar adivinar el patrón de nombres del CDN.
             Esto es lo más confiable, porque distintos capítulos del
             mismo manga pueden tener patrones de nombre distintos
             (ej: "01_01.webp" con cero a la izquierda en unos,
             "1_01.webp" sin cero en otros — según qué herramienta usó
             el equipo de traducción al subirlo).
          2. Si el HTML no trae imágenes reconocibles (layout cambió,
             o vino vacío), caer al sistema viejo de adivinar el patrón
             con HEAD requests contra el CDN, probando tanto con cero
             a la izquierda como sin él.
        """
        api     = self._resolver_dominio()
        CDN_IMG = "https://img.imagesolymp.xyz"

        m = re.search(r"/capitulo/(\d+)/", cap_url)
        if not m:
            log.warning(f"  [Olympus] No se pudo extraer cap_id de: {cap_url}")
            return []
        cap_id = m.group(1)

        # ── Estrategia 1: scraping directo del HTML del capítulo ────
        r_html = hacer_get(cap_url, self.session)
        if r_html and r_html.status_code == 200:
            soup = BeautifulSoup(r_html.text, "html.parser")
            urls_html = []
            for img in soup.find_all("img"):
                src = img.get("src", "")
                # Olympus usa MÁS DE UN subdominio de CDN para las imágenes
                # de capítulos según el lote/momento en que se subieron —
                # confirmado: img.imagesolymp.xyz Y media.imagesolymp.xyz
                # (probablemente haya más). No fijar un subdominio puntual,
                # aceptar cualquiera que termine en imagesolymp.xyz.
                if re.search(r"://[a-z0-9.-]*imagesolymp\.xyz/comics/", src) and "/covers/" not in src:
                    urls_html.append(src)

            if urls_html:
                # Deduplicar preservando el orden de aparición en el HTML
                # (que ya viene en el orden correcto de lectura)
                vistos = set()
                urls_unicas = []
                for u in urls_html:
                    if u not in vistos:
                        vistos.add(u)
                        urls_unicas.append(u)

                # Ordenar por el número de página/subpágina extraído de la
                # URL. Hay dos formatos vistos: "{pagina}_{subpagina}.webp"
                # y "{n}.webp" simple (sin sub-página) — soportar ambos,
                # si no, todo cae en el mismo grupo y se pierde el orden.
                def _clave_orden(u: str):
                    mo = re.search(r"/(\d+)_(\d+)\.webp", u)
                    if mo:
                        return (int(mo.group(1)), int(mo.group(2)))
                    mo2 = re.search(r"/(\d+)\.webp", u)
                    if mo2:
                        return (int(mo2.group(1)), 0)
                    return (9999, 9999)
                urls_unicas.sort(key=_clave_orden)

                log.info(f"  [Olympus] {len(urls_unicas)} imágenes extraídas "
                         f"directamente del HTML del capítulo (cap_id={cap_id})")
                return urls_unicas

        log.info("  [Olympus] El HTML del capítulo no trajo imágenes "
                 "reconocibles, probando patrones de CDN como respaldo...")

        # ── Estrategia 2 (respaldo): adivinar el patrón vía CDN ──────
        manga_id = self._manga_id_cache.get(cap_id, "")

        if not manga_id:
            try:
                r = self.session.get(f"{api}/capitulo/{cap_id}", timeout=TIMEOUT)
            except Exception:
                r = None
            if r and r.status_code == 200:
                try:
                    data = r.json()
                    paginas = (data.get("paginas") or data.get("pages") or
                               data.get("images") or data.get("imgs") or
                               data.get("chapter", {}).get("img") or [])
                    if isinstance(paginas, dict):
                        paginas = paginas.get("data", [])
                    urls = []
                    for p in paginas:
                        if isinstance(p, str) and p.startswith("http"):
                            urls.append(p)
                        elif isinstance(p, dict):
                            u = (p.get("url") or p.get("image") or
                                 p.get("src") or p.get("imagen") or "")
                            if u.startswith("http"):
                                urls.append(u)
                    if urls:
                        return urls
                    manga_id = str(data.get("manga_id") or data.get("comic_id") or
                                   data.get("series_id") or "")
                except Exception:
                    pass

        if not manga_id and slug:
            slug_limpio = slug.lstrip("/")
            if not slug_limpio.startswith("comic-"):
                slug_limpio = f"comic-{slug_limpio}"
            url_serie = f"{self.WEB_BASE}/series/{slug_limpio}"
            r2 = hacer_get(url_serie, self.session)
            if r2:
                m2 = re.search(r"/covers/(\d+)/", r2.text)
                if m2:
                    manga_id = m2.group(1)
                    log.info(f"  [Olympus] manga_id={manga_id} desde portada")

        if not manga_id:
            log.warning(f"  [Olympus] No se pudo obtener manga_id para cap {cap_id}")
            return []

        BASE = f"{CDN_IMG}/comics/{manga_id}/{cap_id}"

        def _head(url):
            try:
                r3 = self.session.head(url, timeout=8)
                return r3.status_code == 200
            except Exception:
                return False

        # Probar las 4 combinaciones de padding posibles para el
        # patrón {pagina}_{subpagina}.webp: ambos con cero a la
        # izquierda, ninguno, o cualquier combinación de los dos —
        # distintos capítulos del mismo manga pueden variar.
        combos_padding = [
            (True,  True),   # 01_01.webp
            (False, False),  # 1_01.webp  (caso real visto: cap 5)
            (True,  False),  # 01_1.webp
            (False, True),   # 1_01.webp con sub con cero — raro, por si acaso
        ]

        def _fmt(n: int, con_cero: bool) -> str:
            return f"{n:02d}" if con_cero else str(n)

        n_sub = 0
        padding_detectado = None
        for pad_pag, pad_sub in combos_padding:
            for s in range(1, 30):
                url_prueba = f"{BASE}/{_fmt(1, pad_pag)}_{_fmt(s, pad_sub)}.webp"
                if _head(url_prueba):
                    n_sub = s
                else:
                    break
            if n_sub > 0:
                padding_detectado = (pad_pag, pad_sub)
                break

        if n_sub == 0:
            # Ningún patrón {pagina}_{subpagina} funcionó — probar el
            # patrón secuencial simple sin sub-páginas: {n:03d}.webp
            log.info("  [Olympus] Patrones pagina_subpagina sin resultados, "
                     "probando patrón secuencial simple...")

            urls_alt = []
            primero_ok = None
            for inicio in (0, 1):
                if _head(f"{BASE}/{inicio:03d}.webp"):
                    primero_ok = inicio
                    break

            if primero_ok is not None:
                n = primero_ok
                while _head(f"{BASE}/{n:03d}.webp"):
                    urls_alt.append(f"{BASE}/{n:03d}.webp")
                    n += 1
                    if n - primero_ok > 300:
                        break

            if urls_alt:
                log.info(f"  [Olympus] {len(urls_alt)} imágenes con patrón "
                         f"secuencial simple (manga_id={manga_id}, cap_id={cap_id})")
                return urls_alt

            log.warning(f"  [Olympus] Sin imágenes en CDN con ningún patrón "
                       f"conocido (manga_id={manga_id}, cap_id={cap_id})")
            return []

        pad_pag, pad_sub = padding_detectado
        log.info(f"  [Olympus] {n_sub} sub-páginas detectadas "
                 f"(padding página={'01' if pad_pag else '1'}, "
                 f"padding sub={'01' if pad_sub else '1'})")

        def _check_pagina(p):
            return p, _head(f"{BASE}/{_fmt(p, pad_pag)}_{_fmt(1, pad_sub)}.webp")

        with ThreadPoolExecutor(max_workers=10) as exe:
            res_pags = dict(exe.map(_check_pagina, range(1, 50)))

        n_pags = 0
        for p in range(1, 50):
            if res_pags.get(p, False):
                n_pags = p
            else:
                break

        if n_pags == 0:
            return []

        log.info(f"  [Olympus] {n_pags} páginas detectadas, verificando "
                 f"sub-páginas reales de cada una...")

        def _contar_subpaginas(p: int) -> tuple[int, int]:
            count = 0
            for s in range(1, n_sub + 5):
                if _head(f"{BASE}/{_fmt(p, pad_pag)}_{_fmt(s, pad_sub)}.webp"):
                    count = s
                else:
                    break
            return p, count

        with ThreadPoolExecutor(max_workers=10) as exe:
            subpags_reales = dict(exe.map(_contar_subpaginas, range(1, n_pags + 1)))

        urls = []
        total_esperado = 0
        for p in range(1, n_pags + 1):
            n_sub_real = subpags_reales.get(p, 0)
            total_esperado += n_sub_real
            for s in range(1, n_sub_real + 1):
                urls.append(f"{BASE}/{_fmt(p, pad_pag)}_{_fmt(s, pad_sub)}.webp")

        log.info(f"  [Olympus] {n_pags} páginas, {total_esperado} imágenes "
                 f"reales (no asumidas)")

        if urls:
            log.info(f"  [Olympus] {len(urls)} imágenes totales (manga_id={manga_id}, cap_id={cap_id})")
        return urls

# ══════════════════════════════════════════════════════════════════════
# §8  MADARA (BASE WORDPRESS)
# ══════════════════════════════════════════════════════════════════════

class MadaraScraper(ScraperBase):
    """
    Base para sitios WordPress con plugin Madara.
    Comparten la misma estructura: Temple Scan y Dragon Translation.
    """

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.session.headers.update({
            "Referer": self.base_url,
        })

    def obtener_capitulos(self, slug: str) -> list[dict]:
        """
        Obtiene la lista de capítulos de un manga Madara.

        Estrategia en orden de prioridad:
        1. Parsear capítulos directamente desde el HTML de la página del manga
           (más confiable — no depende de AJAX ni nonces)
        2. Fallback: AJAX POST a /wp-admin/admin-ajax.php con manga_id
           (algunos sitios Madara solo los exponen así)
        """
        # Buscar la página del manga probando distintos prefijos
        prefijos = ["manga", "serie", "manhwa", "comic", "webtoon"]
        r_manga  = None
        for prefijo in prefijos:
            url_intento = f"{self.base_url}/{prefijo}/{slug}/"
            resp = hacer_get(url_intento, self.session)
            if resp and resp.status_code == 200:
                r_manga = resp
                break

        if not r_manga:
            # Ningún prefijo conocido respondió 200 — puede ser que el
            # manga ya no exista en el sitio, o que el sitio esté caído,
            # o que use un prefijo nuevo que no probamos. Lo distinguimos
            # de "0 capítulos nuevos" porque acá ni siquiera encontramos
            # la página del manga.
            raise SitioRotoError(
                f"No se encontró la página del manga '{slug}' con ninguno "
                f"de los prefijos conocidos ({', '.join(prefijos)}) — "
                f"el manga pudo haber sido removido o el sitio cambió "
                f"su estructura de URLs"
            )

        caps = []

        # Estrategia 1: parsear HTML directamente
        soup = BeautifulSoup(r_manga.text, "html.parser")
        caps = self._parsear_caps_html(soup, slug)

        # Estrategia 2: AJAX fallback si el HTML no dio resultados
        if not caps:
            caps = self._caps_via_ajax(slug, r_manga)

        if not caps:
            # La página del manga SÍ se encontró (200) pero ni el parseo
            # HTML ni el AJAX devolvieron ningún capítulo. Esto es distinto
            # a "el manga está al día" — sugiere que los selectores que
            # usamos ya no matchean nada en el HTML actual del sitio.
            raise SitioRotoError(
                f"La página de '{slug}' respondió 200 pero no se encontró "
                f"NINGÚN capítulo ni por HTML ni por AJAX — posible cambio "
                f"de estructura en el sitio (selectores rotos)"
            )

        caps.sort(key=lambda x: x["numero"])
        return caps

    def _parsear_caps_html(self, soup: BeautifulSoup, slug: str) -> list[dict]:
        """Extrae capítulos desde el HTML de la página del manga."""
        caps = []
        # Selectores comunes en distintas versiones de Madara
        selectores = [
            "li.wp-manga-chapter a",
            "ul.version-chap li a",
            "ul#list-chapters li a",
            ".chapters-list li a",
            ".chapter-list li a",
        ]
        enlaces = []
        for sel in selectores:
            enlaces = soup.select(sel)
            if enlaces:
                break

        for a in enlaces:
            href = a.get("href", "").strip()
            if not href or "javascript" in href:
                continue
            texto = a.get_text(" ", strip=True)
            m = re.search(r"(\d+(?:\.\d+)?)", texto)
            if not m:
                continue
            num = float(m.group(1))
            caps.append({
                "numero": num,
                "url":    href if href.startswith("http") else f"{self.base_url}{href}",
                "titulo": texto,
                "slug":   slug,
            })
        return caps

    def _caps_via_ajax(self, slug: str, r_manga=None) -> list[dict]:
        """
        Fallback: obtiene capítulos via AJAX.
        Extrae el manga_id y el nonce de seguridad desde el HTML
        para que el servidor no rechace el request con 400.
        """
        manga_id = None
        nonce    = None

        if r_manga:
            soup = BeautifulSoup(r_manga.text, "html.parser")
            # manga_id desde el holder
            holder = soup.select_one("div[id^=manga-chapters-holder]")
            if holder:
                manga_id = holder.get("data-id")
            # nonce desde el script extra
            for script in soup.select("script#wp-manga-js-extra"):
                txt = script.string or ""
                m = re.search(r'"manga_id"\s*:\s*"?(\d+)"?', txt)
                if m and not manga_id:
                    manga_id = m.group(1)
                m2 = re.search(r'"nonce"\s*:\s*"([a-f0-9]+)"', txt)
                if m2:
                    nonce = m2.group(1)

        if not manga_id:
            manga_id = self._obtener_manga_id(slug)
        if not manga_id:
            return []

        post_data = {
            "action": "manga_get_chapters",
            "manga":  manga_id,
        }
        if nonce:
            post_data["security"] = nonce

        url_ajax = f"{self.base_url}/wp-admin/admin-ajax.php"
        r = hacer_post(url_ajax, self.session, data=post_data,
                       headers={"X-Requested-With": "XMLHttpRequest",
                                "Referer": f"{self.base_url}/manga/{slug}/"})
        if not r:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        return self._parsear_caps_html(soup, slug)

    def _obtener_manga_id(self, slug: str) -> str | None:
        """
        Obtiene el ID numérico del manga desde la página de la serie.
        Prueba /manga/, /serie/ y otros prefijos porque cada sitio Madara
        puede usar uno distinto (Temple usa /serie/, Dragon usa /manga/).
        """
        prefijos = ["manga", "serie", "manhwa", "comic", "webtoon"]
        r = None
        for prefijo in prefijos:
            url_intento = f"{self.base_url}/{prefijo}/{slug}/"
            resp = hacer_get(url_intento, self.session)
            if resp and resp.status_code == 200:
                r = resp
                break
        if not r:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # Buscar en el holder de capítulos
        holder = soup.select_one("div[id^=manga-chapters-holder]")
        if holder and holder.get("data-id"):
            return holder["data-id"]

        # Buscar en script extra de WP Manga
        for script in soup.select("script#wp-manga-js-extra"):
            m = re.search(r'"manga_id"\s*:\s*"?(\d+)"?', script.string or "")
            if m:
                return m.group(1)

        # Buscar en el body el atributo post-id
        body = soup.find("body")
        if body:
            classes = " ".join(body.get("class", []))
            m = re.search(r"postid-(\d+)", classes)
            if m:
                return m.group(1)

        return None

    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        """
        Madara — obtener imágenes del capítulo.

        Estrategia:
        1. Intentar con requests normal (imágenes directas o AES)
        2. Si hay redirección JS (como Temple Scan → blayvia.com),
           usar Selenium para ejecutar el JS y obtener las imágenes
        """
        # Estrategia 1: requests normal
        r = hacer_get(cap_url, self.session)
        if r:
            soup = BeautifulSoup(r.text, "html.parser")

            # 1a. Protector AES
            protector = soup.select_one("#chapter-protector-data")
            if protector:
                urls = self._desencriptar_protector(protector.get_text(strip=True), r.text)
                if urls:
                    return urls

            # 1b. Imágenes directas
            imgs = soup.select(
                "div.page-break img, li.blocks-gallery-item img, "
                ".reading-content img, .wp-manga-chapter-img"
            )
            urls = []
            for img in imgs:
                src = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
                src = src.strip()
                if src and src.startswith("http") and self._es_imagen_valida(src):
                    urls.append(src)
            if urls:
                return urls

            # 1c. Detectar redirección JS (form POST a dominio externo)
            form = soup.select_one("form[action]")
            if form:
                action = form.get("action", "")
                # Si el form apunta a un dominio diferente al base → Selenium
                from urllib.parse import urlparse as _up
                if action and _up(action).netloc != _up(self.base_url).netloc:
                    log.info(f"  [{self.nombre}] Redirección JS detectada → usando Selenium")
                    return self._obtener_imagenes_selenium(cap_url)

        # Estrategia 2: Selenium como fallback general
        log.info(f"  [{self.nombre}] Sin imágenes via requests → intentando Selenium")
        return self._obtener_imagenes_selenium(cap_url)

    def _obtener_imagenes_selenium(self, cap_url: str) -> list[str]:
        """
        Abre Brave con un perfil de trabajo dedicado (brave-scraper)
        que persiste entre sesiones — guarda cookies, extensiones, config.
        Descarga el ChromeDriver correcto automáticamente.
        """
        import re as _re
        import zipfile
        import urllib.request

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.chrome.service import Service as ChromeService
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
        except ImportError:
            log.error("  Instalar: pip install selenium")
            return []

        # ── Detectar Brave ────────────────────────────────────────────
        brave_paths = [
            os.path.join(os.environ.get("PROGRAMFILES", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.expanduser(os.path.join("~", "AppData", "Local", "BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
        ]
        ruta_brave = next((p for p in brave_paths if os.path.isfile(p)), None)

        # ── Versión de Brave ──────────────────────────────────────────
        def _version_brave(ruta: str) -> str:
            try:
                lv = os.path.join(os.path.dirname(ruta), "Last Version")
                if os.path.isfile(lv):
                    return open(lv).read().strip()
            except Exception:
                pass
            try:
                import winreg
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        key = winreg.OpenKey(hive, r"SOFTWARE\BraveSoftware\Brave-Browser\BLBeacon")
                        v, _ = winreg.QueryValueEx(key, "version")
                        winreg.CloseKey(key)
                        return v
                    except Exception:
                        continue
            except Exception:
                pass
            return ""

        # ── ChromeDriver compatible ───────────────────────────────────
        def _obtener_driver_path(version: str) -> str | None:
            major      = version.split(".")[0] if version else CHROMEDRIVER_VERSION_FALLBACK
            cache_dir  = Path(os.path.expanduser("~")) / ".m4rto_drivers"
            cache_dir.mkdir(exist_ok=True)
            driver_exe = cache_dir / f"chromedriver_{major}.exe"
            if driver_exe.exists():
                return str(driver_exe)
            log.info(f"  [Selenium] Descargando ChromeDriver para Brave {major}...")
            try:
                api = f"https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_{major}"
                with urllib.request.urlopen(api, timeout=10) as r:
                    driver_version = r.read().decode().strip()
                zip_url  = (f"https://storage.googleapis.com/chrome-for-testing-public/"
                            f"{driver_version}/win64/chromedriver-win64.zip")
                zip_path = cache_dir / "chromedriver.zip"
                urllib.request.urlretrieve(zip_url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as z:
                    for member in z.namelist():
                        if member.endswith("chromedriver.exe"):
                            with z.open(member) as src, open(driver_exe, "wb") as dst:
                                dst.write(src.read())
                            break
                zip_path.unlink()
                log.info(f"  [Selenium] ChromeDriver {driver_version} listo")
                return str(driver_exe)
            except Exception as e:
                log.warning(f"  [Selenium] Error descargando driver: {e}")
                return None

        # ── Configurar Brave ──────────────────────────────────────────
        opts        = ChromeOptions()
        driver_path = None

        if ruta_brave and os.path.isfile(ruta_brave):
            opts.binary_location = ruta_brave
            version     = _version_brave(ruta_brave)
            driver_path = _obtener_driver_path(version)
            log.info(f"  [Selenium] Brave {version}")

            # Perfil de trabajo dedicado — persiste entre sesiones
            # (ver BRAVE_PROFILE_DIR en §1, no interfiere con tu Brave personal)
            opts.add_argument(f"--user-data-dir={BRAVE_PROFILE_DIR}")
            opts.add_argument("--profile-directory=Default")
        else:
            log.info("  [Selenium] Usando Chrome")

        if SELENIUM_HEADLESS:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-logging")
        opts.add_argument("--log-level=3")
        opts.add_argument(f"user-agent={HEADERS_BASE['User-Agent']}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        opts.add_experimental_option("useAutomationExtension", False)

        driver = None
        try:
            if driver_path:
                driver = webdriver.Chrome(service=ChromeService(driver_path), options=opts)
            else:
                driver = webdriver.Chrome(options=opts)

            driver.execute_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            driver.get(cap_url)
            url_inicial = driver.current_url
            log.info(f"  [Selenium] {url_inicial}")

            # Esperar redirección JS (máx 20 seg)
            try:
                WebDriverWait(driver, 20).until(
                    lambda d: d.current_url != url_inicial and "data:," not in d.current_url
                )
            except Exception:
                pass

            # Esperar carga del lector
            time.sleep(5)

            page_src = driver.page_source
            urls     = []
            vistos   = set()

            # 1. Selector directo manga-page-img
            for img in driver.find_elements(By.CSS_SELECTOR,
                "img.manga-page-img, .chapter-images img, .reading-content img, img[src*=WP-manga]"
            ):
                for attr in ["src", "data-src", "data-lazy-src", "data-original"]:
                    src = (img.get_attribute(attr) or "").strip()
                    if src and src not in vistos and src.startswith("http") and self._es_imagen_valida(src):
                        urls.append(src)
                        vistos.add(src)

            # 2. Regex para WP-manga
            for u in _re.findall(r"https?://[^\s<>]+/WP-manga/data/[^\s<>]+", page_src, _re.IGNORECASE):
                u = u.strip(".,;)'\"")
                if u not in vistos:
                    urls.append(u)
                    vistos.add(u)

            # 3. Fallback general
            usado_fallback_general = False
            if not urls:
                usado_fallback_general = True
                for img in driver.find_elements(By.CSS_SELECTOR, "img"):
                    for attr in ["src", "data-src", "data-lazy-src"]:
                        src = (img.get_attribute(attr) or "").strip()
                        if src and src not in vistos and src.startswith("http") and self._es_imagen_valida(src):
                            urls.append(src)
                            vistos.add(src)

            if not urls or usado_fallback_general:
                # Se vuelca el HTML tanto si quedó vacío como si lo
                # único que encontró algo fue el fallback genérico
                # (selectores específicos del lector no encontraron
                # nada — la página puede no ser realmente el capítulo,
                # ej: una página de error con imágenes decorativas que
                # el fallback recoge igual al no ser específico).
                debug_path = DEBUG_SELENIUM_PATH
                debug_path.write_text(page_src, encoding="utf-8")
                if not urls:
                    log.warning(f"  [Selenium] Sin imágenes. HTML en {debug_path}")
                else:
                    log.warning(f"  [Selenium] {len(urls)} imagen(es) encontradas "
                               f"SOLO por el fallback genérico (selectores específicos "
                               f"no encontraron nada) — sospechoso de página de error. "
                               f"HTML volcado en {debug_path} para revisar.")

            log.info(f"  [Selenium] {len(urls)} imágenes — {driver.current_url}")
            return urls

        except Exception as e:
            log.error(f"  [Selenium] Error: {e}")
            return []
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _es_imagen_valida(self, url: str) -> bool:
        """Filtra URLs que no son imágenes del capítulo."""
        excluir = ["gravatar", "avatar", "logo", "icon", "banner", "ads",
                   "Me-Gusta", "Me-Divierte", "Me-Sorprende", "Me-Molesta",
                   "Me-Entristece", "Me-Encanta", "spinner", "safeframe",
                   "doubleclick", "googlesyndication"]
        url_l = url.lower()
        if any(e.lower() in url_l for e in excluir):
            return False
        # Imágenes de WP-manga siempre son válidas
        if "/wp-manga/data/" in url_l or "/wp-content/uploads/wp-manga/" in url_l:
            return True
        # Otras imágenes por extensión
        return any(url_l.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"])

    def _desencriptar_protector(self, data_str: str, page_html: str) -> list[str]:
        """
        Intenta desencriptar el protector AES de Madara.
        Si no puede (falta la clave), devuelve lista vacía.
        """
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
            import base64

            # Extraer nonce del HTML
            m_nonce = re.search(r"wpmangaprotectornonce\s*=\s*['\"]([^'\"]+)['\"]", page_html)
            if not m_nonce:
                return []
            nonce = m_nonce.group(1)

            # La clave es el nonce hasheado
            key = hashlib.md5(nonce.encode()).hexdigest()[:16].encode()

            data = json.loads(data_str)
            ct   = base64.b64decode(data.get("ct", ""))
            iv   = bytes.fromhex(data.get("iv", ""))

            cipher     = AES.new(key, AES.MODE_CBC, iv)
            decrypted  = unpad(cipher.decrypt(ct), AES.block_size)
            urls       = json.loads(decrypted.decode("utf-8"))
            return [u.strip() for u in urls if isinstance(u, str)]

        except ImportError:
            log.warning("  pycryptodome no instalado — saltando capítulo protegido")
            return []
        except Exception as e:
            log.warning(f"  Error desencriptando protector: {e}")
            return []

# ══════════════════════════════════════════════════════════════════════
# §9  TEMPLE SCAN
# ══════════════════════════════════════════════════════════════════════

class TempleScraper(MadaraScraper):
    """
    Temple Scan — WordPress/Madara.
    URL base se resuelve vía Supabase automáticamente.
    """

    SUPABASE_URL = (
        "https://ysilhsqbtixygcgscvbb.supabase.co/rest/v1/parameters"
        "?select=value&name=eq.redirect_url_templescan"
    )
    SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlhdCI6MTYzMDYwODc1OSwiZXhwIjoxOTQ2MTg0NzU5fQ.9u3GGsP3t5HgBCqiV4bDEPL84xBbhPE5eXsV7x6EBfU"
    FALLBACK_URL = "https://aedexnox.akan01.com"

    def __init__(self):
        base = self._resolver_url()
        super().__init__(base)

    @property
    def nombre(self) -> str:
        return "Temple Scan"

    def _resolver_url(self) -> str:
        """Obtiene la URL actual de Temple Scan desde Supabase."""
        try:
            r = requests.get(
                self.SUPABASE_URL,
                headers={
                    **HEADERS_BASE,
                    "apikey":        self.SUPABASE_KEY,
                    "Authorization": f"Bearer {self.SUPABASE_KEY}",
                },
                timeout=TIMEOUT
            )
            data = r.json()
            if data and isinstance(data, list) and data[0].get("value"):
                url = data[0]["value"].strip().rstrip("/")
                log.info(f"  [Temple] URL resuelta: {url}")
                return url
        except Exception as e:
            log.warning(f"  [Temple] No se pudo resolver URL vía Supabase: {e}")
        log.info(f"  [Temple] Usando URL de fallback: {self.FALLBACK_URL}")
        return self.FALLBACK_URL

# ══════════════════════════════════════════════════════════════════════
# §10  DRAGON TRANSLATION
# ══════════════════════════════════════════════════════════════════════

class DragonScraper(MadaraScraper):
    """Dragon Translation — WordPress/Madara. URL fija."""

    def __init__(self):
        super().__init__("https://dragontranslation.org")

    @property
    def nombre(self) -> str:
        return "Dragon Translation"

# ══════════════════════════════════════════════════════════════════════
# §11  MANHWAS WEB
# ══════════════════════════════════════════════════════════════════════

class ManhwasWebScraper(ScraperBase):
    """
    ManhwasWEB — SPA React con backend en Railway.
    
    ARQUITECTURA:
      Frontend: https://manhwaweb.com (React/Vite — HTML vacío, no scrapeable)
      Backend:  https://manhwawebbackend-production.up.railway.app
      Imágenes: https://{base}/manhwas/{manga_id}/chapter_{num}/ver_01/{n:03d}.webp
    
    ENDPOINTS:
      GET /manhwa/see/{manga_id}      → info + lista de capítulos
      GET /chapters/see/{cap_id}      → imágenes del capítulo (si tiene)
    
    SLUG en seguimiento.json:
      Es el _id completo del manga, ej: gata-rebelde_1780473612629
      Se obtiene de la URL: manhwaweb.com/manhwa/gata-rebelde_1780473612629
    """

    BACKEND = "https://manhwawebbackend-production.up.railway.app"
    IMG_CDN  = "https://img2mw.xyz"  # fallback si no viene "base" en la API

    def __init__(self):
        super().__init__()
        self.session.headers.update({
            "Origin":  "https://manhwaweb.com",
            "Referer": "https://manhwaweb.com/",
            "Accept":  "application/json, text/plain, */*",
        })

    @property
    def nombre(self) -> str:
        return "ManhwasWEB"

    def obtener_capitulos(self, slug: str) -> list[dict]:
        """
        Llama a /manhwa/see/{slug} y extrae la lista de capítulos.
        El slug es el _id completo: ej "gata-rebelde_1780473612629"
        """
        url = f"{self.BACKEND}/manhwa/see/{slug}"
        r   = hacer_get(url, self.session)
        if not r:
            return []

        try:
            data = r.json()
        except Exception as e:
            log.error(f"  [ManhwasWEB] JSON inválido: {e}")
            return []

        # Guardar el CDN base para construir URLs de imágenes
        self._base_cdn   = data.get("base", "img2mw.xyz")
        self._manga_id   = data.get("_id", slug)

        caps_raw = data.get("chapters", [])
        if not caps_raw:
            log.warning(f"  [ManhwasWEB] Sin capítulos en la respuesta para '{slug}'")
            return []

        caps = []
        for c in caps_raw:
            num     = numero_a_float(c.get("chapter", 0))
            link    = c.get("link", "")
            # Extraer el cap_id del link: manhwaweb.com/leer/{cap_id}
            cap_id  = link.split("/leer/")[1] if "/leer/" in link else ""
            # Las imágenes del capítulo pueden venir en c["img"] (lista)
            # o construirse desde el CDN — guardamos ambos
            imgs_api = c.get("img", [])
            caps.append({
                "numero":   num,
                "url":      f"{self.BACKEND}/chapters/see/{cap_id}",
                "titulo":   f"Capítulo {int(num) if num == int(num) else num}",
                "slug":     slug,
                "fuente":   "manhwaweb",
                "cap_id":   cap_id,
                "imgs_api": imgs_api,
                "cdn_base": self._base_cdn,
                "manga_id": self._manga_id,
            })

        caps.sort(key=lambda x: x["numero"])
        return caps

    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        """
        Llama a /chapters/see/{cap_id} y extrae data["chapter"]["img"].
        Respuesta real: {"chapter": {"img": ["https://...001.webp", ...]}, "roto": "no"}
        Si "roto" == "si", el capítulo está marcado como roto en el servidor.
        """
        r = hacer_get(cap_url, self.session)
        if not r:
            return []

        try:
            data = r.json()
        except Exception as e:
            log.error(f"  [ManhwasWEB] JSON inválido en capítulo: {e}")
            return []

        # Verificar si el capítulo está marcado como roto
        if data.get("roto") == "si":
            log.warning(f"  [ManhwasWEB] Capítulo {numero} marcado como roto en el servidor")

        # Las imágenes están en data["chapter"]["img"]
        chapter = data.get("chapter", {})
        imgs_raw = chapter.get("img", [])

        if not imgs_raw:
            log.warning(f"  [ManhwasWEB] Sin imágenes en chapter.img para cap {numero}")
            return []

        urls = []
        for img in imgs_raw:
            if isinstance(img, str) and img.startswith("http"):
                urls.append(img)
            elif isinstance(img, dict):
                u = img.get("url") or img.get("src") or ""
                if u.startswith("http"):
                    urls.append(u)

        return urls


# ══════════════════════════════════════════════════════════════════════
# §11b  NEXUS SCANLATION
# ══════════════════════════════════════════════════════════════════════

class NexusScraper(ScraperBase):
    """
    API JSON propia (Next.js). Las imágenes pueden venir "scrambled":
    la API entrega junto a la URL un objeto opcional 'sc' = {c, r, s}
    (columnas, filas, semilla). La imagen descargada está cortada en
    una grilla c×r y las celdas reordenadas con un shuffle determinista
    (Fisher-Yates con PRNG mulberry32, semilla = s). Acá se reconstruye
    la imagen original con Pillow antes de guardarla a disco.

    Algoritmo sacado de la extensión oficial de Tachiyomi/Mihon para
    este sitio (decompilado del .dex) — es exactamente lo mismo que
    hace el lector web, no es un bypass de nada nuevo.
    """

    BASE_URL = "https://nexusscanlation.com"
    API_BASE = "https://api.nexusscanlation.com/api/v1"

    @property
    def nombre(self) -> str:
        return "Nexus Scanlation"

    def _headers_api(self) -> dict:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.BASE_URL}/",
            "Origin": self.BASE_URL,
            "Accept-Language": "es-419,es;q=0.9,es-ES;q=0.8",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }

    def obtener_capitulos(self, slug: str) -> list[dict]:
        url = f"{self.API_BASE}/series/{slug}"
        try:
            r = self.session.get(url, headers=self._headers_api(), timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error(f"  [{self.nombre}] Error obteniendo capítulos de '{slug}': {e}")
            return []

        caps = []
        for c in (data.get("capitulos") or []):
            try:
                numero = float(c["numero"])
            except (KeyError, TypeError, ValueError):
                continue
            cap_slug = c.get("slug")
            if not cap_slug:
                continue
            caps.append({
                "numero": numero,
                "url": f"{self.BASE_URL}/series/{slug}/chapter/{cap_slug}",
                "titulo": c.get("titulo") or "",
                "slug": slug,
                "fuente": "nexus",
                "es_premium": bool(c.get("es_premium")),
            })
        caps.sort(key=lambda c: c["numero"])
        return caps

    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        cap_slug = cap_url.rstrip("/").split("/")[-1]
        url = f"{self.API_BASE}/series/{slug}/capitulos/{cap_slug}"
        try:
            r = self.session.get(url, headers=self._headers_api(), timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error(f"  [{self.nombre}] Error obteniendo páginas de cap {numero}: {e}")
            return []

        # A veces viene envuelto en {"data": {...}}, a veces directo.
        pages_data = data.get("data") if isinstance(data.get("data"), dict) else data

        if pages_data.get("es_premium") or pages_data.get("locked"):
            log.warning(f"  [{self.nombre}] Cap {numero}: premium/bloqueado, se omite")
            return []

        # Ordenar explícitamente por 'orden' en vez de confiar en que el
        # array ya venga ordenado — en todos los casos vistos coincidía,
        # pero si la API alguna vez lo devolviera desordenado, confiar
        # en el orden del array daría páginas mezcladas con dimensiones
        # perfectamente válidas y ningún filtro que lo detecte (mismo
        # tipo de error silencioso que el del descramble, a nivel de
        # páginas en vez de tiles). Si falta 'orden' en alguna página,
        # se la manda al final en vez de romper el sort.
        paginas = sorted(
            pages_data.get("paginas") or [],
            key=lambda p: p.get("orden", float("inf"))
        )

        urls = []
        for p in paginas:
            img_url = p.get("url")
            if not img_url:
                continue
            sc = p.get("sc")
            if sc and all(k in sc for k in ("c", "r", "s")):
                img_url = f"{img_url}#scramble={sc['c']},{sc['r']},{sc['s']}"
            urls.append(img_url)
        return urls

    # ── Descramble ──────────────────────────────────────────────────

    @staticmethod
    def _mulberry32_shuffle(n: int, seed: int) -> list[int]:
        MASK = 0xFFFFFFFF
        state = seed & MASK

        def rng() -> float:
            nonlocal state
            state = (state + 0x6D2B79F5) & MASK
            t = state
            t = ((t ^ (t >> 15)) * (t | 1)) & MASK
            t = (t ^ ((((t ^ (t >> 7)) * (t | 61)) & MASK) + t)) & MASK
            return ((t ^ (t >> 14)) & MASK) / 4294967296.0

        perm = list(range(n))
        for i in range(n - 1, 0, -1):
            j = int(rng() * (i + 1))
            perm[i], perm[j] = perm[j], perm[i]
        return perm

    def _descramble(self, datos: bytes, cols: int, rows: int, seed: int) -> bytes:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(datos)).convert("RGB")
        ancho, alto = img.size
        tile_w, tile_h = ancho // cols, alto // rows
        total = cols * rows
        if tile_w == 0 or tile_h == 0 or total == 0:
            return datos

        perm = self._mulberry32_shuffle(total, seed)
        # IMPORTANTE: el sitio arma el rompecabezas aplicando la
        # permutación hacia adelante (tile original i → posición
        # perm[i] en la imagen mezclada que se sirve). Para deshacerlo
        # hay que usar la INVERSA de esa permutación, no la
        # permutación directa — confirmado empíricamente reconstruyendo
        # una imagen real con ambas variantes y midiendo continuidad de
        # bordes entre celdas vecinas (la inversa da una imagen nítida
        # y coherente; la directa da el mismo tipo de "rompecabezas"
        # cortado que se ve en capítulos mal descrambleados).
        inv_perm = [0] * total
        for i, p in enumerate(perm):
            inv_perm[p] = i

        salida = Image.new("RGB", (tile_w * cols, tile_h * rows))

        for i in range(total):
            dest_x, dest_y = (i % cols) * tile_w, (i // cols) * tile_h
            src_idx = inv_perm[i]
            src_x, src_y = (src_idx % cols) * tile_w, (src_idx // cols) * tile_h
            celda = img.crop((src_x, src_y, src_x + tile_w, src_y + tile_h))
            salida.paste(celda, (dest_x, dest_y))

        buf = io.BytesIO()
        salida.save(buf, format="PNG")
        return buf.getvalue()

    def procesar_imagen(self, img_url: str, datos: bytes) -> bytes:
        if "#scramble=" not in img_url:
            return datos
        frag = img_url.split("#scramble=", 1)[1]
        try:
            c_str, r_str, s_str = frag.split(",")
            cols, rows, seed = int(c_str), int(r_str), int(s_str)
            return self._descramble(datos, cols, rows, seed)
        except Exception as e:
            log.warning(f"  [{self.nombre}] Error descrambling ({frag}): {e}")
            return datos


# ══════════════════════════════════════════════════════════════════════
# §11c  IKIGAI MANGAS
# ══════════════════════════════════════════════════════════════════════

class IkigaiScraper(ScraperBase):
    """
    Ikigai Mangas — plataforma propia (app Qwik con SSR), no es Madara.

    Particularidades de este sitio (confirmadas inspeccionando HTML
    real, no asumidas):

    - NO hace falta Selenium. El HTML que devuelve un GET normal con
      User-Agent de navegador ya viene completamente renderizado
      (server-side render), sin challenge de Cloudflare.

    - El listado de capítulos de una serie y el lector de cada
      capítulo viven en DOMINIOS DISTINTOS, y cada uno rota de forma
      independiente (anti-bloqueo/DMCA). Los dominios viejos no
      mueren de golpe: durante un tiempo siguen respondiendo solo
      para REDIRIGIR al dominio nuevo. Por eso este scraper no
      depende de ningún dominio fijo hardcodeado más que una
      "semilla" inicial: cada vez que una request termina (post-
      redirect) en un dominio distinto al que tenía cacheado, lo
      actualiza y lo persiste en ikigai_estado.json — se va
      autorregenerando solo mientras la cadena de redirects hacia
      el dominio nuevo siga viva en el momento en que el scraper
      corre. Si algún día TODOS los dominios (cacheados + semilla)
      dejan de responder a la vez, no hay forma automática de
      recuperarse — se loguea un SitioRotoError pidiendo un dominio
      nuevo a mano, en vez de fallar en silencio.

    - La paginación del listado de capítulos es 100% automática: se
      lee directo del <nav aria-label="pagination"> de la página 1
      (trae links a todas las páginas existentes), así que no hace
      falta indicarle a mano cuál es la página más vieja.

    - Las imágenes reales del capítulo viven siempre bajo la ruta
      exacta image*.ikigaimangas.cloud/series/{id_serie}/{id_cap}/...
      El sitio mete banners de publicidad (ej. bannerikigai.png)
      entreverados con las páginas reales, con la misma clase CSS y
      tamaño — así que los filtros genéricos de ancho/alto/ratio NO
      los distinguen de forma confiable. En cambio, los banners viven
      en otra ruta (ej. .../posts/misc/...), así que se descartan
      comparando la URL exacta, no las dimensiones de la imagen.
    """

    SEMILLA_SERIES = "zonaikigai.gamesview.shop"

    def __init__(self):
        super().__init__()
        estado = self._cargar_estado()
        self._dominio_series = estado.get("dominio_series") or self.SEMILLA_SERIES
        self._dominio_lector = estado.get("dominio_lector") or ""

    @property
    def nombre(self) -> str:
        return "Ikigai Mangas"

    # ── Manejo de dominios rotativos ───────────────────────────────────

    def _cargar_estado(self) -> dict:
        if IKIGAI_ESTADO_PATH.exists():
            try:
                return json.loads(IKIGAI_ESTADO_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning(f"  [Ikigai] No se pudo leer {IKIGAI_ESTADO_PATH.name}: {e}")
        return {}

    def _guardar_estado(self):
        tmp = IKIGAI_ESTADO_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "dominio_series": self._dominio_series,
            "dominio_lector": self._dominio_lector,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(IKIGAI_ESTADO_PATH)

    def _get_listado(self, path: str):
        """
        GET sobre el dominio de LISTADO cacheado, siguiendo redirects.
        Si termina en un dominio distinto al cacheado, actualiza el
        caché. Si el cacheado no responde ni para redirigir, reintenta
        una vez con la semilla antes de rendirse.
        """
        dominio = self._dominio_series
        r = hacer_get(f"https://{dominio}{path}", self.session, allow_redirects=True)
        if not r and dominio != self.SEMILLA_SERIES:
            log.warning(f"  [Ikigai] Dominio de listado '{dominio}' sin respuesta, "
                       f"probando semilla '{self.SEMILLA_SERIES}'")
            dominio = self.SEMILLA_SERIES
            r = hacer_get(f"https://{dominio}{path}", self.session, allow_redirects=True)
        if not r:
            return None
        nuevo = urlparse(r.url).netloc
        if nuevo and nuevo != self._dominio_series:
            log.info(f"  [Ikigai] Dominio de listado rotó: {self._dominio_series} → {nuevo}")
            self._dominio_series = nuevo
            self._guardar_estado()
        return r

    def _get_lector(self, url: str):
        """
        GET sobre la URL de un capítulo (dominio del lector, que puede
        venir ya desactualizado desde que se armó la lista hace rato),
        siguiendo redirects. Si no responde ni para redirigir, se
        reintenta una vez reconstruyendo la misma ruta sobre el
        dominio de listado actual (que sabemos que responde, porque
        ya se usó en obtener_capitulos).
        """
        r = hacer_get(url, self.session, allow_redirects=True)
        if not r:
            ruta = urlparse(url).path
            url_alt = f"https://{self._dominio_series}{ruta}"
            if url_alt != url:
                log.warning(f"  [Ikigai] Dominio del lector sin respuesta para "
                           f"'{url}', reintentando vía dominio de listado")
                r = hacer_get(url_alt, self.session, allow_redirects=True)
        if not r:
            return None
        nuevo = urlparse(r.url).netloc
        if nuevo and nuevo != self._dominio_lector:
            log.info(f"  [Ikigai] Dominio del lector rotó: "
                     f"{self._dominio_lector or '(sin caché)'} → {nuevo}")
            self._dominio_lector = nuevo
            self._guardar_estado()
        return r

    # ── Capítulos ───────────────────────────────────────────────────────

    def obtener_capitulos(self, slug: str) -> list[dict]:
        r1 = self._get_listado(f"/series/{slug}/?pagina=1")
        if not r1:
            raise SitioRotoError(
                f"No se pudo cargar la página de '{slug}' en ningún dominio "
                f"conocido (cacheado ni semilla) — puede que todos los "
                f"dominios espejo hayan rotado a la vez. Hace falta "
                f"confirmar un dominio nuevo a mano."
            )

        soup1 = BeautifulSoup(r1.text, "html.parser")
        ultima_pagina = self._detectar_ultima_pagina(soup1)

        caps: dict[float, dict] = {}
        self._acumular_caps(soup1, slug, caps)

        for pagina in range(2, ultima_pagina + 1):
            r = self._get_listado(f"/series/{slug}/?pagina={pagina}")
            if not r:
                log.warning(f"  [Ikigai] No se pudo cargar la página {pagina} "
                           f"de '{slug}', se sigue con lo que ya se tiene")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            self._acumular_caps(soup, slug, caps)

        if not caps:
            raise SitioRotoError(
                f"La página de '{slug}' respondió pero no se encontró "
                f"ningún capítulo — posible cambio de estructura en el sitio "
                f"(selectores rotos)"
            )

        return sorted(caps.values(), key=lambda c: c["numero"])

    def _detectar_ultima_pagina(self, soup: BeautifulSoup) -> int:
        """
        Lee el <nav aria-label="pagination"> para saber cuántas páginas
        de capítulos tiene la serie — nada hardcodeado ni pedido a mano.
        Si no hay ese nav (serie con una sola página), devuelve 1.
        """
        nav = soup.find("nav", attrs={"aria-label": "pagination"})
        if not nav:
            return 1
        paginas = set()
        for a in nav.find_all("a", href=True):
            m = re.search(r"pagina=(\d+)", a["href"])
            if m:
                paginas.add(int(m.group(1)))
            m2 = re.search(r"[Pp][aá]gina\s+(\d+)", a.get("aria-label", ""))
            if m2:
                paginas.add(int(m2.group(1)))
        return max(paginas) if paginas else 1

    def _acumular_caps(self, soup: BeautifulSoup, slug: str, caps: dict):
        """
        Extrae capítulos de una página de listado ya parseada.
        Ignora a propósito los links "Primer Capítulo" / "Último
        Capítulo" (no llevan un número justo después de la palabra
        "Capítulo", así que el regex no matchea y se descartan solos).
        """
        dominio_base = self._dominio_lector or self._dominio_series
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/capitulo/" not in href:
                continue
            texto = a.get_text(" ", strip=True)
            m = re.search(r"Cap[ií]tulo\s+([\d.]+)", texto, re.IGNORECASE)
            if not m:
                continue
            num = float(m.group(1))
            if num in caps:
                continue
            url_cap = urljoin(f"https://{dominio_base}/", href)
            caps[num] = {
                "numero": num,
                "url":    url_cap,
                "titulo": texto,
                "slug":   slug,
            }

    # ── Imágenes ─────────────────────────────────────────────────────────

    # Páginas reales: image*.ikigaimangas.cloud/series/{id_serie}/{id_cap}/...
    # (ambos IDs son siempre numéricos — los banners de publicidad viven
    # en otras rutas, ej. .../posts/misc/bannerikigai.png, que esta
    # expresión no matchea).
    _RE_RUTA_PAGINA_REAL = re.compile(r"(https://image\d*\.ikigaimangas\.cloud/series/\d+/\d+/)")

    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        r = self._get_lector(cap_url)
        if not r:
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        prefijo = None
        candidatas = []
        for tag in soup.find_all("img"):
            src = tag.get("src") or tag.get("data-src")
            if not src:
                continue
            m = self._RE_RUTA_PAGINA_REAL.search(src)
            if not m:
                continue  # no es una página del capítulo (cover, banner, etc.)
            if prefijo is None:
                prefijo = m.group(1)  # fija el id_serie/id_cap de ESTE capítulo
            candidatas.append(src)

        if not prefijo:
            log.warning(f"  [Ikigai] Cap {numero}: no se encontró ninguna imagen "
                       f"bajo series/{{id}}/{{id}}/ — posible cambio de estructura "
                       f"en el sitio")
            return []

        # Se descarta cualquier imagen que matchee el patrón general pero
        # NO el id_serie/id_cap exacto de este capítulo (por si la página
        # mezclara, ej., una miniatura de "capítulo siguiente").
        return [u for u in candidatas if u.startswith(prefijo)]




def crear_scraper(fuente: str) -> ScraperBase | None:
    fuente = fuente.lower().strip()
    scrapers = {
        "olympus":   OlympusScraper,
        "temple":    TempleScraper,
        "dragon":    DragonScraper,
        "manhwaweb": ManhwasWebScraper,
        "nexus":     NexusScraper,
        "ikigai":    IkigaiScraper,
    }
    cls = scrapers.get(fuente)
    if not cls:
        log.error(f"Fuente desconocida: '{fuente}'. Opciones: {list(scrapers.keys())}")
        return None
    try:
        return cls()
    except Exception as e:
        log.error(f"Error iniciando scraper de {fuente}: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════
# §13  LÓGICA PRINCIPAL DE ESCANEO
# ══════════════════════════════════════════════════════════════════════

def obtener_carpeta_base() -> Path:
    """
    Lee MANGA_PATH_SCRAPER del .env del proyecto padre.
    Si no existe, usa MANGA_PATH como fallback.
    """
    # Subir dos niveles: scraper/ → Servidor-de-Mangas/
    env_path = SCRIPT_DIR.parent / ".env"
    if not env_path.exists():
        # Intentar un nivel arriba (por si la carpeta está anidada diferente)
        env_path = SCRIPT_DIR.parent.parent / ".env"

    carpeta = None
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MANGA_PATH_SCRAPER="):
                carpeta = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        if not carpeta:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("MANGA_PATH=") and not line.startswith("MANGA_PATH_2"):
                    carpeta = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not carpeta:
        log.warning("No se encontró MANGA_PATH_SCRAPER ni MANGA_PATH en .env")
        log.warning("Usando carpeta por defecto: ./mangas")
        carpeta = str(SCRIPT_DIR.parent / "mangas")

    p = Path(carpeta)
    p.mkdir(parents=True, exist_ok=True)
    return p

def _descargar_con_timeout(scraper: ScraperBase, cap: dict, carpeta_manga: Path,
                            timeout_seg: float) -> tuple[bool, bool]:
    """
    Ejecuta scraper.descargar_capitulo() con un límite de tiempo real.

    Corre la descarga en un thread daemon aparte: si no termina dentro
    de timeout_seg, esta función retorna igual (no bloquea el resto del
    escaneo), marcando colgado=True. El thread daemon eventualmente
    termina solo en segundo plano (o el proceso se cierra junto con el
    programa principal si nunca termina) — no se "mata" a la fuerza
    porque Python no tiene una forma segura de hacerlo sin arriesgar
    corrupción de archivos a mitad de escritura.

    Retorna (ok, colgado):
      - (True/False, False)  → terminó a tiempo, resultado normal
      - (False, True)        → no terminó a tiempo, se abandonó
    """
    resultado: list = [None]
    excepcion: list = [None]

    def _worker():
        try:
            resultado[0] = scraper.descargar_capitulo(cap, carpeta_manga)
        except Exception as e:
            excepcion[0] = e

    hilo = threading.Thread(target=_worker, daemon=True)
    hilo.start()
    hilo.join(timeout=timeout_seg)

    if hilo.is_alive():
        # No terminó a tiempo — se abandona el thread (sigue corriendo
        # en background como daemon, pero ya no bloquea el escaneo).
        return False, True

    if excepcion[0] is not None:
        raise excepcion[0]

    return bool(resultado[0]), False


def _descargar_caps_nuevos(caps_nuevos: list[dict], manga_cfg: dict,
                            carpeta_manga: Path, scraper: ScraperBase,
                            reporte: "ReporteEscaneo" = None) -> int:
    """
    Descarga una lista de capítulos nuevos para un manga.
    Actualiza manga_cfg['ultimo_capitulo'] y agrega notificaciones.
    Si se pasa un ReporteEscaneo, registra ahí cada resultado
    (éxito, parcial, error) para que quede en el reporte.html.

    Aplica dos límites de tiempo para que un manga problemático no
    estire un escaneo de 3 horas mucho más de lo esperado:
    - Por capítulo individual: si descargar_capitulo() no termina en
      TIMEOUT_MANGA_SEG, se corta esa llamada puntual (corre en un
      thread aparte para poder "abandonarla" sin matar el proceso).
    - Acumulado por manga: si la suma de tiempo ya gastado en este
      manga supera TIMEOUT_MANGA_SEG, se cortan los capítulos
      restantes sin intentarlos.

    ── Sistema multi-fuente (prioridad_fuente) ───────────────────────
    Si manga_cfg tiene un campo 'prioridad_fuente' (número, menor =
    mejor), esta función asume que puede haber OTRA entrada de
    seguimiento.json con distinta 'fuente' apuntando al mismo
    'nombre_carpeta' — es decir, el mismo manga trackeado desde 2+
    sitios. Antes de descargar cada capítulo, se consulta
    registro_progreso.json (que vive en la carpeta compartida) para
    ver qué fuente lo tiene actualmente:
      - Si ya está cubierto por una fuente de prioridad igual o mejor
        → se omite sin gastar ni una request de imagen. Importante:
        NO se avanza manga_cfg['ultimo_capitulo'] en este caso, para
        que esta entrada siga revisando ese número en escaneos
        futuros (red de seguridad si la fuente mejor llegara a
        perder ese capítulo más adelante).
      - Si está cubierto por una fuente PEOR (o no está cubierto) →
        se descarga, pero a una carpeta de staging aparte, nunca
        directo en el lugar real. Solo si la descarga termina 100%
        completa se borra la versión vieja y se reemplaza — así,
        sitios que dividen el capítulo en distinta cantidad de
        imágenes nunca dejan páginas mezcladas, y si la descarga
        falla a mitad de camino la versión existente (que funciona)
        queda intacta.
    Si manga_cfg NO tiene 'prioridad_fuente', nada de esto se activa
    y el comportamiento es idéntico al de siempre.

    Retorna cantidad descargada.
    """
    descargados      = 0
    fuente           = manga_cfg["fuente"]
    nombre           = manga_cfg["nombre_carpeta"]
    prioridad_propia = prioridad_efectiva(manga_cfg)  # SIEMPRE un número
    t_inicio_manga   = time.time()
    omitidos_cubiertos = 0  # resumen al final, en vez de 1 línea por capítulo
    omitidos_legado    = 0

    for idx_cap, cap in enumerate(caps_nuevos):
        num      = cap["numero"]
        nombrecap = nombre_capitulo(num)

        # Límite acumulado: si ya gastamos demasiado tiempo en este
        # manga, no seguir intentando los capítulos que faltan —
        # van a quedar pendientes para el próximo escaneo (no se
        # pierden, solo se postergan).
        transcurrido = time.time() - t_inicio_manga
        if transcurrido > TIMEOUT_MANGA_SEG:
            faltantes = len(caps_nuevos) - idx_cap
            log.warning(f"  ⏱  {nombre} — timeout de manga superado "
                       f"({_fmt_duracion(int(transcurrido))}), se posponen "
                       f"{faltantes} capítulo(s) restantes al próximo escaneo")
            if reporte:
                reporte.advertencia(nombre, fuente,
                    f"⏱ Timeout de manga superado — {faltantes} capítulo(s) "
                    f"pospuestos al próximo escaneo")
            break

        # ── Cruce de prioridad multi-fuente ───────────────────────────
        # Esto se evalúa para TODOS los mangas (ahora siempre hay una
        # prioridad efectiva), pero en la práctica solo hace algo
        # distinto al camino de siempre cuando aparece una fuente
        # REAL y DISTINTA a la nuestra en el registro — es decir,
        # solo cuando el manga de verdad está trackeado desde 2+
        # sitios apuntando a la misma carpeta. Para un manga de una
        # sola fuente, el registro de un capítulo ya descargado va a
        # tener SU PROPIA fuente (caso 1 de abajo) y cae directo al
        # chequeo de siempre — cero cambio de comportamiento.
        upgrade_de = None  # nombre de la fuente que se intenta superar
        reg_previo = cargar_registro_progreso(carpeta_manga).get(str(num))
        if reg_previo and reg_previo.get("estado") == "completado":
            fuente_previa = reg_previo.get("fuente")
            prio_previa   = reg_previo.get("prioridad_fuente")

            if fuente_previa == fuente:
                # Caso 1: es nuestro propio trabajo anterior
                # confirmándose a sí mismo — cae al chequeo de
                # siempre más abajo (capitulo_ya_existe), que SÍ
                # avanza ultimo_capitulo normalmente.
                pass
            elif fuente_previa is None:
                # Caso 2: existe en disco pero el registro es de
                # antes de que este sistema guardara la fuente (o
                # de antes de que prioridad_fuente existiera para
                # este manga). Más seguro no tocarlo. Tampoco se
                # avanza ultimo_capitulo, para seguir revisándolo en
                # escaneos futuros por las dudas.
                omitidos_legado += 1
                continue
            else:
                # Caso 3: es de OTRA fuente real y distinta a la
                # nuestra — acá sí corresponde comparar prioridades.
                if prio_previa is not None and prioridad_propia >= prio_previa:
                    omitidos_cubiertos += 1
                    if fuente_previa == FUENTE_EXTERNA:
                        # Nada puede tener mejor prioridad que
                        # 'externa' (rango 0) — jamás va a hacer
                        # falta volver a revisar este número, así que
                        # acá SÍ es seguro avanzar ultimo_capitulo
                        # (evita re-chequear esto en TODOS los
                        # escaneos futuros para siempre).
                        manga_cfg["ultimo_capitulo"] = max(
                            float(manga_cfg.get("ultimo_capitulo", 0)), num
                        )
                    continue
                else:
                    upgrade_de = fuente_previa

        if capitulo_ya_existe(carpeta_manga, num) and upgrade_de is None:
            log.info(f"    ⏭  {nombrecap} ya existe, actualizando registro")
            manga_cfg["ultimo_capitulo"] = max(
                float(manga_cfg.get("ultimo_capitulo", 0)), num
            )
            continue

        # Tiempo restante real para ESTE capítulo: lo que quede del
        # presupuesto del manga (nunca más que TIMEOUT_MANGA_SEG).
        restante = max(TIMEOUT_MANGA_SEG - transcurrido, 30)  # mínimo 30s de margen

        # Si es un intento de upgrade, descargar a una carpeta de
        # staging aparte (nunca directo en el lugar real — ver
        # docstring). Se limpia cualquier resto de un intento anterior
        # interrumpido antes de empezar.
        carpeta_destino = carpeta_manga
        if upgrade_de is not None:
            carpeta_destino = carpeta_manga / f"_staging_{fuente}_{nombrecap}"
            shutil.rmtree(carpeta_destino, ignore_errors=True)
            log.info(f"    ⬆️  {nombrecap}: '{fuente}' (prioridad {prioridad_propia}) "
                     f"intenta mejorar la versión actual de '{upgrade_de}'...")

        try:
            pausas_antes = scraper.detector_bloqueo.veces_pausado
            ok, colgado = _descargar_con_timeout(scraper, cap, carpeta_destino, restante)
            pausas_nuevas = scraper.detector_bloqueo.veces_pausado - pausas_antes
        except Exception as e:
            log.error(f"  Error inesperado descargando {nombrecap}: {e}")
            marcar_capitulo_con_error(manga_cfg, num)
            if upgrade_de is not None:
                shutil.rmtree(carpeta_destino, ignore_errors=True)
            if reporte:
                reporte.error(nombre, fuente, f"{nombrecap}: excepción ({type(e).__name__}) — {e}")
            time.sleep(1)
            continue

        if colgado:
            log.warning(f"  ⏱  {nombrecap} de {nombre} excedió {int(restante)}s "
                       f"sin terminar — se abandona y se sigue con el resto")
            marcar_capitulo_con_error(manga_cfg, num)
            if upgrade_de is not None:
                shutil.rmtree(carpeta_destino, ignore_errors=True)
            if reporte:
                reporte.advertencia(nombre, fuente,
                    f"⏱ {nombrecap}: excedió el tiempo límite ({int(restante)}s) "
                    f"y se abandonó — se reintentará en el próximo escaneo")
            continue

        if reporte and pausas_nuevas > 0:
            reporte.bloqueo(nombre, fuente,
                f"{nombrecap}: {pausas_nuevas} pausa(s) por posible bloqueo del servidor")

        if ok:
            if upgrade_de is not None:
                # Descarga nueva 100% completa — ahora sí se reemplaza
                # la versión vieja, recién en este momento.
                carpeta_vieja = carpeta_manga / nombrecap
                carpeta_nueva = carpeta_destino / nombrecap
                info_nueva    = cargar_registro_progreso(carpeta_destino).get(str(num))
                shutil.rmtree(carpeta_vieja, ignore_errors=True)
                shutil.move(str(carpeta_nueva), str(carpeta_vieja))
                shutil.rmtree(carpeta_destino, ignore_errors=True)
                if info_nueva:
                    reg_real = cargar_registro_progreso(carpeta_manga)
                    reg_real[str(num)] = info_nueva
                    guardar_registro_progreso(carpeta_manga, reg_real)
                log.info(f"    ⬆️  {nombrecap} reemplazado: '{upgrade_de}' → "
                         f"'{fuente}' (mejor prioridad)")

            descargados += 1
            manga_cfg["ultimo_capitulo"] = max(
                float(manga_cfg.get("ultimo_capitulo", 0)), num
            )
            desmarcar_capitulo_con_error(manga_cfg, num)

            if reporte:
                reg  = cargar_registro_progreso(carpeta_manga)
                info = reg.get(str(num), {})
                if info.get("estado") == "parcial":
                    reporte.parcial(nombre, fuente, nombrecap,
                                    info.get("validas", 0), info.get("esperadas", 0))
                else:
                    reporte.exito(nombre, fuente, nombrecap,
                                  info.get("validas", 0))
        else:
            marcar_capitulo_con_error(manga_cfg, num)
            if upgrade_de is not None:
                shutil.rmtree(carpeta_destino, ignore_errors=True)
                log.warning(f"    ⬆️  {nombrecap}: no se pudo mejorar desde "
                           f"'{fuente}', se mantiene la versión de '{upgrade_de}'")
            if reporte:
                reporte.error(nombre, fuente, f"{nombrecap}: no se pudo descargar")

        time.sleep(1)

    if omitidos_cubiertos or omitidos_legado:
        partes = []
        if omitidos_cubiertos:
            partes.append(f"{omitidos_cubiertos} ya cubierto(s) por otra fuente de mayor prioridad")
        if omitidos_legado:
            partes.append(f"{omitidos_legado} sin metadata de fuente (legado)")
        log.info(f"    ⏭  {', '.join(partes)}, omitidos sin descargar")

    return descargados


def escanear_olympus(mangas_olympus: list[dict], carpeta_base: Path,
                     scraper: OlympusScraper, reporte: "ReporteEscaneo" = None,
                     guardado_incremental=None) -> int:
    """
    Flujo para Olympus (simplificado):

    Por cada manga, SIEMPRE se consulta la página completa de la serie
    (/series/comic-{slug}) para traer la lista real de TODOS sus
    capítulos — nunca se confía en que "apareció en las páginas de
    novedades recientes" como única fuente de qué hay que descargar,
    porque eso deja huecos si el manga tiene capítulos viejos que no
    salieron en esas páginas.

    - ultimo_capitulo == 0  → se descargan todos los capítulos de la serie
    - ultimo_capitulo == X  → se descargan solo los capítulos > X

    Si el slug guardado ya no resuelve (404), se recupera el slug
    vigente buscando por nombre entre las páginas de novedades
    recientes (/capitulos), y se actualiza seguimiento.json solo.

    guardado_incremental: callback sin argumentos que persiste
    seguimiento.json. Se llama al terminar cada manga (no solo al
    final del bloque completo de Olympus) para no perder progreso
    si el proceso se interrumpe a mitad de una corrida con varios
    mangas de Olympus.
    """
    if not mangas_olympus:
        return 0

    total = 0

    for manga_cfg in mangas_olympus:
        nombre        = manga_cfg["nombre_carpeta"]
        slug_guardado = manga_cfg.get("slug", "")
        ultimo        = float(manga_cfg.get("ultimo_capitulo", 0))
        carpeta_manga = carpeta_base / nombre

        # Si hay capítulos en disco pero no hay registro_progreso.json
        # (ej: manga descargado aparte y copiado a esta carpeta), generar
        # el registro completo de una sola pasada antes de seguir.
        regenerar_registro_completo(carpeta_manga)

        caps_en_disco = contar_capitulos_en_disco(carpeta_manga)
        if ultimo > 0 and caps_en_disco == 0:
            log.warning(f"  ⚠  [Olympus] {nombre} — seguimiento.json indica cap "
                       f"{ultimo} pero no hay nada completo en disco. "
                       f"Forzando descarga completa.")
            if reporte:
                reporte.advertencia(nombre, "olympus",
                    f"Sin capítulos completos en disco pese a ultimo_capitulo={ultimo}. "
                    f"Se fuerza descarga completa.")
            ultimo = 0.0

        if ultimo == 0:
            log.info(f"  [Olympus] 📖 {nombre} — descarga completa "
                     f"(ultimo_capitulo=0), consultando la serie...")
        else:
            log.info(f"  [Olympus] 📖 {nombre} — buscando capítulos > {ultimo}...")

        try:
            caps = scraper.obtener_capitulos(slug_guardado, manga_cfg, reporte)
        except SitioRotoError as e:
            log.error(f"  [Olympus] 🔧 POSIBLE SITIO ROTO en {nombre}: {e}")
            if reporte:
                reporte.error(nombre, "olympus", f"🔧 SITIO ROTO (revisar selectores): {e}")
            continue
        except Exception as e:
            log.error(f"  [Olympus] Error obteniendo capítulos de {nombre}: "
                     f"{type(e).__name__}: {e}")
            if reporte:
                reporte.error(nombre, "olympus",
                    f"Error obteniendo capítulos ({type(e).__name__}): {e}")
            continue

        if not caps:
            log.warning(f"  [Olympus] {nombre} — sin capítulos disponibles "
                       f"en la página de la serie")
            if reporte:
                reporte.advertencia(nombre, "olympus",
                    "La página de la serie no devolvió ningún capítulo")
            continue

        # Cap 0 suele ser un placeholder de "fecha de lanzamiento" sin
        # contenido real del manga — se ignora siempre, en cualquier sitio.
        caps = [c for c in caps if c["numero"] != 0]

        # Etiquetar prioridad efectiva (paridad con escanear_manga) —
        # sin esto, un manga de Olympus combinado con otra fuente en el
        # sistema multi-fuente quedaría siempre con prioridad_fuente=None
        # y cualquier rival lo trataría como "hay que intentar mejorarlo"
        # sin importar la prioridad real.
        prioridad_manga = prioridad_efectiva(manga_cfg)
        tipo_contenido  = manga_cfg.get("tipo_contenido")   # "manga" | None
        for c in caps:
            c.setdefault("prioridad_fuente", prioridad_manga)
            if tipo_contenido:
                c.setdefault("tipo_contenido", tipo_contenido)

        # Detectar huecos (paridad con escanear_manga): cualquier
        # capítulo que la serie dice que existe, con numero <= ultimo,
        # pero que no está completo en disco.
        if ultimo > 0:
            huecos = [c["numero"] for c in caps
                      if c["numero"] <= ultimo
                      and not capitulo_esta_completo(carpeta_manga, c["numero"])]
            if huecos:
                huecos.sort()
                nombres_huecos = ", ".join(nombre_capitulo(h) for h in huecos)
                log.warning(f"  ⚠  [Olympus] {nombre} — {len(huecos)} capítulo(s) "
                           f"por debajo del último ({ultimo}) no están completos "
                           f"en disco: {nombres_huecos}. Se marcan para reintentar.")
                if reporte:
                    reporte.advertencia(nombre, "olympus",
                        f"{len(huecos)} hueco(s) detectado(s) (borrados a mano o "
                        f"corruptos): {nombres_huecos} — se reintentarán.")
                for h in huecos:
                    marcar_capitulo_con_error(manga_cfg, h)

        nuevos = [c for c in caps if c["numero"] > ultimo]

        # Sumar los capítulos marcados con error en escaneos anteriores
        # (ej: fallaron por completo y no llegaron a tener carpeta —
        # registro_progreso.json no los puede ver, así que sin esto
        # quedarían perdidos para siempre por estar por debajo de
        # ultimo_capitulo).
        pendientes_error = capitulos_pendientes_por_error(manga_cfg)
        if pendientes_error:
            ya_incluidos = {c["numero"] for c in nuevos}
            caps_por_num = {c["numero"]: c for c in caps}
            agregados = 0
            for num_err in pendientes_error:
                if num_err in ya_incluidos:
                    continue
                cap_err = caps_por_num.get(num_err)
                if cap_err:
                    nuevos.append(cap_err)
                    agregados += 1
            if agregados:
                log.info(f"  [Olympus] +{agregados} capítulo(s) marcados "
                         f"con error en escaneos anteriores, reintentando")
            nuevos.sort(key=lambda x: x["numero"])

        if not nuevos:
            log.info(f"  [Olympus] ✓ {nombre} al día (hasta cap {caps[-1]['numero']})")
            if guardado_incremental:
                guardado_incremental()
            continue

        log.info(f"  [Olympus] → {len(nuevos)} capítulo(s) para descargar")
        total += _descargar_caps_nuevos(nuevos, manga_cfg, carpeta_manga, scraper, reporte)

        if guardado_incremental:
            guardado_incremental()

    return total


def escanear_manga(manga_cfg: dict, carpeta_base: Path, reporte: "ReporteEscaneo" = None) -> int:
    """
    Escanea un manga (no-Olympus) y descarga capítulos nuevos.
    Retorna la cantidad de capítulos nuevos descargados.

    Si la carpeta del manga no tiene ningún capítulo real en disco
    (se borró manualmente, o nunca se descargó), se ignora el valor
    de ultimo_capitulo guardado y se vuelve a descargar todo desde
    el principio — para evitar quedar "atascado" creyendo que ya
    se tienen capítulos que en realidad no existen.
    """
    nombre        = manga_cfg["nombre_carpeta"]
    fuente        = manga_cfg["fuente"]
    slug          = manga_cfg["slug"]
    ultimo_guardado = float(manga_cfg.get("ultimo_capitulo", 0))
    carpeta_manga = carpeta_base / nombre

    # Si hay capítulos en disco pero no hay registro_progreso.json
    # (ej: manga descargado aparte y copiado a esta carpeta), generar
    # el registro completo de una sola pasada antes de seguir.
    regenerar_registro_completo(carpeta_manga)

    caps_en_disco = contar_capitulos_en_disco(carpeta_manga)
    if ultimo_guardado > 0 and caps_en_disco == 0:
        log.warning(f"  ⚠  {nombre} — seguimiento.json indica cap {ultimo_guardado} "
                   f"pero no hay nada en disco. Forzando descarga completa.")
        if reporte:
            reporte.advertencia(nombre, fuente,
                f"Sin capítulos completos en disco pese a ultimo_capitulo={ultimo_guardado}. "
                f"Se fuerza descarga completa.")
        ultimo = 0.0
    else:
        ultimo = ultimo_guardado

    log.info("")
    log.info(f"  📖 {nombre} [{fuente}] — último: cap {ultimo if ultimo else 'ninguno'}"
             + (f"  ({caps_en_disco} en disco)" if caps_en_disco else ""))

    scraper = crear_scraper(fuente)
    if not scraper:
        if reporte:
            reporte.error(nombre, fuente, "No se pudo crear el scraper para esta fuente")
        return 0

    try:
        caps = scraper.obtener_capitulos(slug)
    except SitioRotoError as e:
        log.error(f"  🔧 POSIBLE SITIO ROTO en {nombre} [{fuente}]: {e}")
        if reporte:
            reporte.error(nombre, fuente, f"🔧 SITIO ROTO (revisar selectores): {e}")
        return 0
    except Exception as e:
        log.error(f"  Error obteniendo capítulos de {nombre}: {type(e).__name__}: {e}")
        if reporte:
            reporte.error(nombre, fuente, f"Error obteniendo capítulos ({type(e).__name__}): {e}")
        return 0

    if not caps:
        log.info(f"  Sin capítulos disponibles para {nombre}")
        if reporte:
            reporte.advertencia(nombre, fuente, "No se encontraron capítulos disponibles")
        return 0

    # Cap 0 suele ser un placeholder de "fecha de lanzamiento" sin
    # contenido real del manga — se ignora siempre, en cualquier sitio.
    caps = [c for c in caps if c["numero"] != 0]
    if not caps:
        return 0

    # Asegurar que cada cap lleva la fuente para los filtros adaptativos,
    # y su prioridad efectiva (manual si se forzó, automática según el
    # sitio si no) para que descargar_capitulo() la guarde en
    # registro_progreso.json junto con el resultado.
    prioridad_manga  = prioridad_efectiva(manga_cfg)
    tipo_contenido   = manga_cfg.get("tipo_contenido")   # "manga" | None
    for c in caps:
        c.setdefault("fuente", fuente)
        c.setdefault("prioridad_fuente", prioridad_manga)
        if tipo_contenido:
            c.setdefault("tipo_contenido", tipo_contenido)

    # Detectar huecos: cualquier capítulo que el SITIO dice que existe,
    # con numero <= ultimo (es decir, "ya lo deberíamos tener"), pero
    # que no está completo en disco. Cubre tanto el último capítulo
    # como cualquiera en el medio del rango (ej: lo borraste a mano,
    # o quedó corrupto y nunca se marcó como error en su momento).
    # Se compara contra el catálogo REAL del sitio (no un rango de
    # números asumido) para no generar falsos positivos en mangas con
    # numeración no secuencial (capítulos especiales, .5, saltos, etc.)
    if ultimo > 0:
        huecos = [c["numero"] for c in caps
                  if c["numero"] <= ultimo
                  and not capitulo_esta_completo(carpeta_manga, c["numero"])]
        if huecos:
            huecos.sort()
            nombres_huecos = ", ".join(nombre_capitulo(h) for h in huecos)
            log.warning(f"  ⚠  {nombre} — {len(huecos)} capítulo(s) por debajo "
                       f"del último ({ultimo}) no están completos en disco: "
                       f"{nombres_huecos}. Se marcan para reintentar.")
            if reporte:
                reporte.advertencia(nombre, fuente,
                    f"{len(huecos)} hueco(s) detectado(s) (borrados a mano o "
                    f"corruptos): {nombres_huecos} — se reintentarán.")
            for h in huecos:
                marcar_capitulo_con_error(manga_cfg, h)

    nuevos = [c for c in caps if c["numero"] > ultimo]

    # Sumar los capítulos marcados con error en escaneos anteriores
    # (fallo total: nunca llegaron a tener carpeta, así que
    # registro_progreso.json no los puede recordar por su cuenta).
    pendientes_error = capitulos_pendientes_por_error(manga_cfg)
    if pendientes_error:
        ya_incluidos = {c["numero"] for c in nuevos}
        caps_por_num = {c["numero"]: c for c in caps}
        agregados = 0
        for num_err in pendientes_error:
            if num_err in ya_incluidos:
                continue
            cap_err = caps_por_num.get(num_err)
            if cap_err:
                nuevos.append(cap_err)
                agregados += 1
        if agregados:
            log.info(f"  +{agregados} capítulo(s) marcados con error en "
                     f"escaneos anteriores, reintentando")
        nuevos.sort(key=lambda x: x["numero"])

    if not nuevos:
        log.info(f"  ✓ {nombre} al día (hasta cap {caps[-1]['numero']})")
        return 0

    if ultimo == 0 and ultimo_guardado > 0:
        log.info(f"  → Descarga completa para {nombre}: {len(nuevos)} capítulo(s)")
    else:
        log.info(f"  → {len(nuevos)} capítulo(s) nuevo(s) para {nombre}")
    return _descargar_caps_nuevos(nuevos, manga_cfg, carpeta_manga, scraper, reporte)


# ══════════════════════════════════════════════════════════════════════
# §13b  REPORTE HTML ACUMULATIVO
# ══════════════════════════════════════════════════════════════════════
#
# Genera reporte.html con el historial completo de todos los escaneos,
# más reciente arriba. No se borra entre ejecuciones: cada escaneo se
# agrega como un nuevo bloque colapsable, y se mantiene un resumen
# acumulado global de toda la vida del scraper.
#
# El estado persistente (lista de escaneos pasados) vive embebido
# DENTRO del propio reporte.html (como JSON en un <script>), no en un
# archivo aparte — se lee de ahí al arrancar y se reescribe junto con
# el HTML en cada ciclo. REPORTE_PATH y HISTORIAL_PATH_LEGACY están
# definidas en §1 CONFIGURACIÓN, al inicio del archivo.
#
# MAX_ESCANEOS_HISTORIAL, MAX_BYTES_HISTORIAL y MAX_LARGO_MENSAJE
# también están en §1.

def _truncar_mensaje(msg: str) -> str:
    """Evita que un único mensaje de error/excepción gigante (por ejemplo
    un traceback completo pegado por error) infle el historial."""
    msg = str(msg)
    if len(msg) > MAX_LARGO_MENSAJE:
        return msg[:MAX_LARGO_MENSAJE] + f"... (truncado, {len(msg)} chars originales)"
    return msg

class ReporteEscaneo:
    """
    Acumula durante UN ciclo de escaneo: capítulos exitosos, parciales,
    advertencias y errores, por manga. Al finalizar, se llama a
    guardar_y_generar_html() para persistir el historial completo.
    """

    def __init__(self):
        self.inicio = datetime.now()
        # manga_nombre -> {"fuente":..., "exitos":[...], "parciales":[...],
        #                   "advertencias":[...], "errores":[...]}
        self._mangas: dict[str, dict] = {}

    def _manga(self, nombre: str, fuente: str) -> dict:
        if nombre not in self._mangas:
            self._mangas[nombre] = {
                "fuente": fuente, "exitos": [], "parciales": [],
                "advertencias": [], "errores": [], "bloqueos": [],
            }
        return self._mangas[nombre]

    def exito(self, nombre: str, fuente: str, capitulo: str, imagenes: int):
        self._manga(nombre, fuente)["exitos"].append(
            {"capitulo": capitulo, "imagenes": imagenes})

    def parcial(self, nombre: str, fuente: str, capitulo: str,
                validas: int, esperadas: int):
        self._manga(nombre, fuente)["parciales"].append(
            {"capitulo": capitulo, "validas": validas, "esperadas": esperadas})

    def advertencia(self, nombre: str, fuente: str, mensaje: str):
        self._manga(nombre, fuente)["advertencias"].append(_truncar_mensaje(mensaje))

    def error(self, nombre: str, fuente: str, mensaje: str):
        self._manga(nombre, fuente)["errores"].append(_truncar_mensaje(mensaje))

    def bloqueo(self, nombre: str, fuente: str, mensaje: str):
        """
        Registra una pausa anti-bloqueo (BlockDetector) detectada durante
        la descarga. Se muestra aparte de errores/advertencias en el
        reporte porque significa "el sitio nos está rate-limitando",
        no "algo está roto" — pero conviene saberlo igual si se repite
        seguido en varios escaneos.
        """
        self._manga(nombre, fuente)["bloqueos"].append(_truncar_mensaje(mensaje))

    def _resumen(self) -> dict:
        caps_ok    = sum(len(m["exitos"])    for m in self._mangas.values())
        caps_parc  = sum(len(m["parciales"]) for m in self._mangas.values())
        caps_err   = sum(len(m["errores"])   for m in self._mangas.values())
        n_bloqueos = sum(len(m["bloqueos"])  for m in self._mangas.values())
        imgs_total = sum(e["imagenes"] for m in self._mangas.values() for e in m["exitos"])
        imgs_total += sum(p["validas"] for m in self._mangas.values() for p in m["parciales"])
        return {
            "fecha":          self.inicio.isoformat(),
            "duracion_seg":   round((datetime.now() - self.inicio).total_seconds()),
            "mangas_tocados": len(self._mangas),
            "caps_ok":        caps_ok,
            "caps_parciales": caps_parc,
            "caps_error":     caps_err,
            "bloqueos":       n_bloqueos,
            "imagenes":       imgs_total,
            "mangas":         self._mangas,
        }

    def guardar_y_generar_html(self):
        """
        Persiste este escaneo en el historial y regenera reporte.html.

        Aplica dos topes, lo que se alcance primero:
        - Cantidad de escaneos (MAX_ESCANEOS_HISTORIAL)
        - Tamaño en disco del JSON resultante (MAX_BYTES_HISTORIAL) —
          esto protege contra el caso de pocos escaneos pero con muchos
          mangas y mensajes de error largos, que igual podría inflar
          el archivo más de lo razonable antes de llegar al tope de 200.
        """
        historial = _cargar_historial()
        historial.insert(0, self._resumen())  # más reciente primero

        if len(historial) > MAX_ESCANEOS_HISTORIAL:
            historial = historial[:MAX_ESCANEOS_HISTORIAL]

        # Recortar por tamaño: si se pasa de MAX_BYTES_HISTORIAL, se van
        # descartando los escaneos más viejos (al final de la lista)
        # hasta volver a entrar dentro del límite.
        while len(historial) > 1:
            tamano = len(json.dumps(historial, ensure_ascii=False).encode("utf-8"))
            if tamano <= MAX_BYTES_HISTORIAL:
                break
            historial.pop()  # descarta el más viejo (último de la lista)

        # _generar_html ya se encarga de embeber el historial dentro del
        # propio reporte.html — no hay un _guardar_historial() aparte.
        _generar_html(historial)


# Marcadores únicos que delimitan el historial embebido dentro del
# propio reporte.html (ver _generar_html). Usar un id propio en vez de
# buscar "cualquier <script type=application/json>" evita confundirse
# si en algún momento se agrega otro bloque de datos a la página.
_MARCA_HISTORIAL_INICIO = '<script type="application/json" id="m4rto-historial">'
_MARCA_HISTORIAL_FIN    = "</script>"

def _cargar_historial() -> list[dict]:
    """
    Lee el historial embebido en reporte.html. Si reporte.html todavía
    no existe (primera corrida) pero queda un historial_reportes.json
    de una versión anterior a esta unificación, lo usa una sola vez
    como semilla y lo borra — así no se pierde el historial acumulado
    al actualizar el scraper.
    """
    if REPORTE_PATH.exists():
        try:
            html_actual = REPORTE_PATH.read_text(encoding="utf-8")
            i = html_actual.find(_MARCA_HISTORIAL_INICIO)
            if i != -1:
                i += len(_MARCA_HISTORIAL_INICIO)
                j = html_actual.find(_MARCA_HISTORIAL_FIN, i)
                if j != -1:
                    crudo = html_actual[i:j].strip().replace("<\\/", "</")
                    return json.loads(crudo)
        except Exception as e:
            log.warning(f"No se pudo leer el historial embebido en "
                       f"{REPORTE_PATH.name}, se empieza de cero: {e}")
            return []

    if HISTORIAL_PATH_LEGACY.exists():
        try:
            historial = json.loads(HISTORIAL_PATH_LEGACY.read_text(encoding="utf-8"))
            HISTORIAL_PATH_LEGACY.unlink(missing_ok=True)
            log.info(f"📦 Historial migrado desde {HISTORIAL_PATH_LEGACY.name} "
                     f"a {REPORTE_PATH.name} (archivo viejo eliminado)")
            return historial
        except Exception as e:
            log.warning(f"No se pudo migrar {HISTORIAL_PATH_LEGACY.name}: {e}")

    return []

def _fmt_duracion(seg: int) -> str:
    m, s = divmod(int(seg), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def _generar_html(historial: list[dict]):
    """Regenera reporte.html completo a partir del historial acumulado."""
    # Precalculado afuera del f-string: en Python <3.12 no se permite un
    # backslash dentro de la parte {expresión} de un f-string.
    _historial_json_embebido = json.dumps(historial, ensure_ascii=False).replace("</", "<\\/")
    total_caps_ok   = sum(e["caps_ok"]        for e in historial)
    total_caps_parc = sum(e["caps_parciales"] for e in historial)
    total_caps_err  = sum(e["caps_error"]     for e in historial)
    total_bloqueos  = sum(e.get("bloqueos", 0) for e in historial)  # .get: compat con historial viejo
    total_imgs      = sum(e["imagenes"]       for e in historial)
    total_escaneos  = len(historial)

    # Salud por manga: última vez que recibió un capítulo exitoso o parcial
    salud: dict[str, dict] = {}
    for esc in historial:
        fecha = esc["fecha"]
        for nombre, info in esc["mangas"].items():
            tuvo_contenido = bool(info["exitos"]) or bool(info["parciales"])
            if nombre not in salud:
                salud[nombre] = {"fuente": info["fuente"], "ultima_fecha": None,
                                 "ultimo_error": None}
            if tuvo_contenido and salud[nombre]["ultima_fecha"] is None:
                salud[nombre]["ultima_fecha"] = fecha
            if info["errores"] and salud[nombre]["ultimo_error"] is None:
                salud[nombre]["ultimo_error"] = fecha

    def _esc(s: str) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                       .replace(">", "&gt;").replace('"', "&quot;"))

    bloques_html = []
    for idx, esc in enumerate(historial):
        fecha_dt = datetime.fromisoformat(esc["fecha"])
        fecha_fmt = fecha_dt.strftime("%Y-%m-%d %H:%M")
        abierto = "open" if idx == 0 else ""

        filas = []
        for nombre, info in sorted(esc["mangas"].items()):
            n_ok      = len(info["exitos"])
            n_parc    = len(info["parciales"])
            n_err     = len(info["errores"])
            n_adv     = len(info["advertencias"])
            n_bloq    = len(info.get("bloqueos", []))  # .get: compat con historial viejo
            n_imgs    = sum(e["imagenes"] for e in info["exitos"]) + \
                        sum(p["validas"] for p in info["parciales"])

            caps_detalle = []
            for e in info["exitos"]:
                caps_detalle.append(f'<span class="cap-ok">✅ {_esc(e["capitulo"])} '
                                    f'({e["imagenes"]} img)</span>')
            for p in info["parciales"]:
                caps_detalle.append(f'<span class="cap-parcial">⚠️ {_esc(p["capitulo"])} '
                                    f'parcial ({p["validas"]}/{p["esperadas"]})</span>')

            mensajes = []
            for a in info["advertencias"]:
                mensajes.append(f'<div class="msg-adv">⚠️ {_esc(a)}</div>')
            for er in info["errores"]:
                mensajes.append(f'<div class="msg-err">❌ {_esc(er)}</div>')
            for b in info.get("bloqueos", []):
                mensajes.append(f'<div class="msg-bloqueo">🚫 {_esc(b)}</div>')

            clase_fila = "fila-error" if n_err else ("fila-parcial" if n_parc else "")

            filas.append(f'''
            <tr class="{clase_fila}">
              <td>{_esc(nombre)}</td>
              <td>{_esc(info["fuente"])}</td>
              <td class="ok-n">{n_ok}</td>
              <td class="parc-n">{n_parc}</td>
              <td class="adv-n">{n_adv if n_adv else "—"}</td>
              <td class="fail-n">{n_err}</td>
              <td class="bloq-n">{n_bloq if n_bloq else "—"}</td>
              <td>{n_imgs}</td>
              <td class="small">{"".join(caps_detalle) if caps_detalle else "—"}</td>
              <td class="small">{"".join(mensajes) if mensajes else "—"}</td>
            </tr>''')

        bloques_html.append(f'''
        <details class="escaneo" {abierto}>
          <summary>
            <span class="fecha">📅 {fecha_fmt}</span>
            <span class="resumen-mini">
              {esc["mangas_tocados"]} manga(s) ·
              <span class="ok-n">{esc["caps_ok"]} ok</span> ·
              <span class="parc-n">{esc["caps_parciales"]} parciales</span> ·
              <span class="fail-n">{esc["caps_error"]} errores</span> ·
              {f'<span class="bloq-n">{esc.get("bloqueos",0)} bloqueos</span> · ' if esc.get("bloqueos",0) else ""}
              {esc["imagenes"]} imgs ·
              {_fmt_duracion(esc["duracion_seg"])}
            </span>
          </summary>
          <table>
            <thead><tr>
              <th>Manga</th><th>Sitio</th><th>✅ Ok</th><th>⚠️ Parcial</th>
              <th>⚠️ Advertencias</th><th>❌ Error</th><th>🚫 Bloqueos</th><th>🖼️ Imgs</th><th>Capítulos</th><th>Mensajes</th>
            </tr></thead>
            <tbody>{"".join(filas) if filas else "<tr><td colspan=10 class='small'>Sin actividad en este escaneo</td></tr>"}</tbody>
          </table>
        </details>''')

    # Tabla de salud por manga
    filas_salud = []
    ahora = datetime.now()
    for nombre, info in sorted(salud.items()):
        if info["ultima_fecha"]:
            dt = datetime.fromisoformat(info["ultima_fecha"])
            dias = (ahora - dt).days
            txt_fecha = dt.strftime("%Y-%m-%d")
            clase = "salud-mal" if dias > 14 else ("salud-tibia" if dias > 7 else "salud-ok")
            txt_dias = f"hace {dias}d" if dias > 0 else "hoy"
        else:
            txt_fecha = "—"
            txt_dias  = "sin descargas registradas"
            clase = "salud-mal"
        err_txt = ""
        if info["ultimo_error"]:
            err_dt = datetime.fromisoformat(info["ultimo_error"])
            err_txt = f'<span class="fail-n">último error: {err_dt.strftime("%Y-%m-%d %H:%M")}</span>'
        filas_salud.append(f'''
        <tr class="{clase}">
          <td>{_esc(nombre)}</td>
          <td>{_esc(info["fuente"])}</td>
          <td>{txt_fecha} <span class="small">({txt_dias})</span></td>
          <td class="small">{err_txt or "—"}</td>
        </tr>''')

    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>M4RTO Scraper — Reporte de escaneos</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background:#0f0f0f; color:#e0e0e0;
         padding:20px; max-width:1200px; margin:0 auto; }}
  h1   {{ color:#a78bfa; }} h2 {{ color:#7dd3fc; margin-top:30px; }}
  .summary {{ display:flex; gap:20px; flex-wrap:wrap; margin:16px 0; }}
  .card {{ background:#1e1e2e; border-radius:10px; padding:16px 24px;
          min-width:120px; text-align:center; }}
  .card .val {{ font-size:2em; font-weight:bold; color:#a78bfa; }}
  .card .lbl {{ font-size:.8em; color:#888; margin-top:4px; }}
  table {{ border-collapse:collapse; width:100%; margin-top:12px; }}
  th    {{ background:#1e1e2e; color:#7dd3fc; padding:10px 14px; text-align:left; }}
  td    {{ padding:9px 14px; border-bottom:1px solid #2a2a3a; vertical-align:top; }}
  .ok-n    {{ color:#4ade80; font-weight:bold; }}
  .parc-n  {{ color:#facc15; font-weight:bold; }}
  .adv-n   {{ color:#fcd34d; font-weight:bold; }}
  .fail-n  {{ color:#f87171; font-weight:bold; }}
  .bloq-n  {{ color:#fb923c; font-weight:bold; }}
  .small   {{ font-size:.8em; color:#aaa; max-width:320px; word-break:break-word; }}
  .fila-error td   {{ background:#1a0a0a; }}
  .fila-parcial td {{ background:#1a160a; }}
  tr:hover td {{ background:#23233a; }}
  .cap-ok      {{ display:inline-block; margin:2px 6px 2px 0; color:#4ade80; }}
  .cap-parcial {{ display:inline-block; margin:2px 6px 2px 0; color:#facc15; }}
  .msg-adv     {{ color:#facc15; margin:2px 0; }}
  .msg-err     {{ color:#f87171; margin:2px 0; }}
  .msg-bloqueo {{ color:#fb923c; margin:2px 0; }}
  details.escaneo {{ background:#161622; border-radius:10px; margin:14px 0;
                     padding:10px 16px; }}
  details.escaneo summary {{ cursor:pointer; padding:8px 4px; list-style:none; }}
  details.escaneo summary::-webkit-details-marker {{ display:none; }}
  details.escaneo summary .fecha {{ color:#a78bfa; font-weight:bold; margin-right:16px; }}
  details.escaneo summary .resumen-mini {{ color:#999; font-size:.9em; }}
  details.escaneo[open] summary {{ border-bottom:1px solid #2a2a3a; margin-bottom:10px; }}
  .salud-ok   td:nth-child(3) {{ color:#4ade80; }}
  .salud-tibia td:nth-child(3) {{ color:#facc15; }}
  .salud-mal  td:nth-child(3) {{ color:#f87171; }}
  .footer {{ margin-top:30px; color:#555; font-size:.85em; text-align:center; }}
</style>
</head>
<body>
<h1>🎌 M4RTO Scraper — Reporte de escaneos</h1>
<p class="small">Generado: {ahora.strftime("%Y-%m-%d %H:%M:%S")} ·
   {total_escaneos} escaneo(s) en el historial</p>

<h2>📊 Resumen acumulado (histórico completo)</h2>
<div class="summary">
  <div class="card"><div class="val">{len(salud)}</div><div class="lbl">Mangas en seguimiento</div></div>
  <div class="card"><div class="val ok-n">{total_caps_ok}</div><div class="lbl">Caps ✅ completos</div></div>
  <div class="card"><div class="val parc-n">{total_caps_parc}</div><div class="lbl">Caps ⚠️ parciales</div></div>
  <div class="card"><div class="val fail-n">{total_caps_err}</div><div class="lbl">Errores ❌</div></div>
  <div class="card"><div class="val bloq-n">{total_bloqueos}</div><div class="lbl">Pausas anti-bloqueo 🚫</div></div>
  <div class="card"><div class="val">{total_imgs}</div><div class="lbl">Imágenes 🖼️</div></div>
</div>

<h2>💓 Salud por manga</h2>
<table>
  <thead><tr><th>Manga</th><th>Sitio</th><th>Última descarga</th><th>Último error</th></tr></thead>
  <tbody>{"".join(filas_salud) if filas_salud else "<tr><td colspan=4 class='small'>Sin datos todavía</td></tr>"}</tbody>
</table>

<h2>📋 Historial de escaneos</h2>
{"".join(bloques_html) if bloques_html else "<p class='small'>Todavía no se registró ningún escaneo.</p>"}

<div class="footer">M4RTO Scraper — reporte.html se regenera en cada escaneo, el historial no se borra (vive embebido en este mismo archivo)</div>
{_MARCA_HISTORIAL_INICIO}
{_historial_json_embebido}
{_MARCA_HISTORIAL_FIN}
</body></html>'''

    tmp = REPORTE_PATH.with_suffix(".tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(REPORTE_PATH)



def ciclo_escaneo():
    """Un ciclo completo de escaneo de todos los mangas activos."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info("╔" + "═"*58 + "╗")
    log.info(f"║  🔍 ESCANEO  {ahora:<44}║")
    log.info("╚" + "═"*58 + "╝")

    data = cargar_seguimiento()
    if data is None:
        log.error("  No se pudo cargar seguimiento.json — se salta "
                 "este ciclo, se reintentará en el próximo escaneo")
        return

    carpeta_base = obtener_carpeta_base()
    mangas       = [m for m in data.get("mangas", []) if m.get("activo", True)]
    reporte      = ReporteEscaneo()

    # Si algún manga tiene 'url_manga' (URL completa pegada del navegador),
    # extraer el slug automáticamente antes de procesar nada.
    hubo_cambios_url = False
    for manga_cfg in mangas:
        if normalizar_url_manga(manga_cfg):
            hubo_cambios_url = True
    if hubo_cambios_url:
        guardar_seguimiento(data)

    log.info(f"  📂 {carpeta_base}")
    log.info(f"  📚 {len(mangas)} manga(s) activos")

    # Avisos de cordura (no frenan nada) sobre configuraciones de
    # seguimiento.json que probablemente sean un error humano.
    validar_multi_fuente(mangas)

    total_nuevos = 0

    # ── Olympus: cada manga consulta su propia serie completa ────────
    mangas_olympus = [m for m in mangas if m.get("fuente") == "olympus"]
    if mangas_olympus:
        log.info(f"\n  ┌─ 🏛  Olympus ({len(mangas_olympus)} manga(s))")
        try:
            scraper_olympus = OlympusScraper()
            n = escanear_olympus(mangas_olympus, carpeta_base, scraper_olympus, reporte,
                                 guardado_incremental=lambda: guardar_seguimiento(data))
            total_nuevos += n
            log.info(f"  └─ ✓ {n} capítulo(s) nuevos")
        except Exception as e:
            log.error(f"  └─ ✗ Error Olympus: {e}")
            reporte.error("Olympus (general)", "olympus", f"Error general del bloque: {e}")
        finally:
            # Guardar lo que se haya avanzado, incluso si el bloque entero
            # tiró una excepción a mitad de camino (no perder progreso ya
            # confirmado de los mangas que sí se terminaron de procesar).
            guardar_seguimiento(data)

    # ── Resto de sitios: flujo individual ────────────────────────────
    mangas_otros = [m for m in mangas if m.get("fuente") != "olympus"]
    sitios = {}
    for m in mangas_otros:
        sitios.setdefault(m.get("fuente","?"), []).append(m)

    # Orden de escaneo: de mejor a peor calidad/confiabilidad (mismo
    # criterio y misma lista que la prioridad automática — ver
    # ORDEN_FUENTES arriba, así nunca se desincronizan). Si más
    # adelante un sitio de mejor calidad falla un capítulo puntual, esto
    # asegura que ya corrió ANTES que los de menor prioridad en el mismo
    # ciclo (relevante para el sistema de prioridad multi-fuente). Olympus
    # va siempre primero (bloque separado arriba, nunca aparece acá).
    sitios = dict(sorted(
        sitios.items(),
        key=lambda kv: ORDEN_FUENTES.index(kv[0]) if kv[0] in ORDEN_FUENTES else len(ORDEN_FUENTES)
    ))

    for fuente, lista in sitios.items():
        icono = {"nexus":"🔗","temple":"🏯","dragon":"🐉","manhwaweb":"📚","ikigai":"🌸"}.get(fuente,"📖")
        log.info(f"\n  ┌─ {icono}  {fuente.title()} ({len(lista)} manga(s))")
        n_fuente = 0
        for manga_cfg in lista:
            try:
                n = escanear_manga(manga_cfg, carpeta_base, reporte)
                total_nuevos += n
                n_fuente     += n
            except Exception as e:
                nombre_m = manga_cfg.get('nombre_carpeta','?')
                log.error(f"  │  ✗ Error en {nombre_m}: {e}")
                reporte.error(nombre_m, fuente, f"Error inesperado en el escaneo: {e}")
            finally:
                # Guardado incremental: si el proceso se interrumpe a mitad
                # de un manga (cierre manual, corte de luz, etc.), los
                # capítulos ya confirmados de los mangas ANTERIORES de este
                # mismo ciclo no se pierden — quedan grabados en disco ya,
                # no recién al terminar los 20 mangas restantes.
                guardar_seguimiento(data)
        log.info(f"  └─ ✓ {n_fuente} capítulo(s) nuevos")

    # Guardado final (redundante con los incrementales, pero no estorba —
    # asegura que el último estado quede grabado incluso si algo de la
    # estructura de 'data' se tocó después del último guardado parcial).
    guardar_seguimiento(data)

    # Generar/actualizar reporte.html con el historial completo
    try:
        reporte.guardar_y_generar_html()
        log.info(f"  📄 Reporte actualizado: {REPORTE_PATH}")
    except Exception as e:
        log.error(f"  No se pudo generar el reporte HTML: {e}")

    log.info("")
    log.info("╔" + "═"*58 + "╗")
    if total_nuevos > 0:
        log.info(f"║  ✅ {total_nuevos} capítulo(s) nuevos descargados{' '*(28-len(str(total_nuevos)))}║")
    else:
        log.info("║  ✅ Todo al día — sin capítulos nuevos              ║")
    log.info("╚" + "═"*58 + "╝")

# ══════════════════════════════════════════════════════════════════════
# §14  SCHEDULER
# ══════════════════════════════════════════════════════════════════════

def scheduler():
    """Corre el escaneo al inicio y luego cada INTERVALO_HORAS horas."""
    while True:
        try:
            ciclo_escaneo()
        except Exception as e:
            log.error(f"Error crítico en ciclo de escaneo ({type(e).__name__}): {e}")

        proxima = INTERVALO_HORAS * 3600
        log.info(f"⏰ Próximo escaneo en {INTERVALO_HORAS} horas")
        time.sleep(proxima)

# ══════════════════════════════════════════════════════════════════════
# §15  MAIN
# ══════════════════════════════════════════════════════════════════════

def _adquirir_lock() -> bool:
    """
    Crea scraper.lock para evitar que corran dos instancias del scraper
    a la vez — riesgo real: las dos escribirían a los mismos
    seguimiento.json/registro_progreso.json sin coordinarse entre sí.

    Si ya existe un lock más viejo que LOCK_MAX_HORAS, se asume que
    quedó de una corrida anterior que se colgó o crasheó sin limpiar
    (corte de luz, proceso matado a la fuerza, etc.) y se pisa — para
    no terminar bloqueado para siempre por un lock fantasma.

    Retorna True si es seguro seguir, False si hay otra instancia
    genuinamente corriendo ahora mismo.
    """
    if LOCK_PATH.exists():
        try:
            edad_seg = time.time() - LOCK_PATH.stat().st_mtime
        except OSError:
            edad_seg = LOCK_MAX_HORAS * 3600 + 1  # ilegible → tratar como viejo

        if edad_seg < LOCK_MAX_HORAS * 3600:
            try:
                pid_viejo = LOCK_PATH.read_text(encoding="utf-8").strip()
            except OSError:
                pid_viejo = "?"
            log.error(f"Ya parece haber otra instancia corriendo (lock de hace "
                     f"{int(edad_seg/60)} min, PID guardado: {pid_viejo}). Si "
                     f"estás seguro de que no hay ninguna otra corriendo, borrá "
                     f"'{LOCK_PATH.name}' a mano y volvé a intentar.")
            return False
        else:
            log.warning(f"Encontrado '{LOCK_PATH.name}' de hace "
                       f"{edad_seg/3600:.1f}h (más de {LOCK_MAX_HORAS}h) — se "
                       f"asume de una corrida anterior que se colgó o crasheó "
                       f"sin limpiar. Se ignora y se continúa.")

    try:
        LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as e:
        log.warning(f"No se pudo crear el lock file ({e}) — se continúa "
                   f"igual, pero sin esta protección.")
    return True


def _liberar_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def main():
    log.info("╔══════════════════════════════════════════╗")
    log.info("║       📚  M4RTO SCRAPER  v1.0            ║")
    log.info("╚══════════════════════════════════════════╝")
    log.info(f"Configuración: {SEGUIMIENTO_PATH}")
    log.info(f"Intervalo: cada {INTERVALO_HORAS} horas")

    if not _adquirir_lock():
        sys.exit(1)

    # Limpieza de archivos obsoletos de versiones anteriores (ya no se
    # usan — ver §1 NOTIF_PATH_LEGACY). El historial_reportes.json viejo
    # se migra solo, lazy, dentro de _cargar_historial() en el primer
    # ciclo, así que no hace falta tocarlo acá.
    if NOTIF_PATH_LEGACY.exists():
        try:
            NOTIF_PATH_LEGACY.unlink()
            log.info(f"🧹 Eliminado {NOTIF_PATH_LEGACY.name} (sistema de notificaciones descontinuado)")
        except Exception as e:
            log.warning(f"No se pudo eliminar {NOTIF_PATH_LEGACY.name}: {e}")

    try:
        if not SEGUIMIENTO_PATH.exists():
            log.error(f"No se encontró seguimiento.json en {SCRIPT_DIR}")
            log.error("Creá el archivo con tus mangas antes de iniciar el scraper.")
            sys.exit(1)

        # Modo --una-vez: ejecutar una sola vez y salir (útil para pruebas)
        if "--una-vez" in sys.argv:
            log.info("Modo: una sola ejecución")
            try:
                ciclo_escaneo()
            except Exception as e:
                log.error(f"Error crítico en el escaneo ({type(e).__name__}): {e}")
                sys.exit(1)
            return

        # Modo normal: loop infinito con intervalo
        scheduler()
    finally:
        _liberar_lock()

if __name__ == "__main__":
    main()
