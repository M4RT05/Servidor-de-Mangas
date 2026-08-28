"""
╔══════════════════════════════════════════════════════════════════════╗
║ M4RTO SCRAPER  v1.9                                                  ║
║                                                                      ║
║  Sitios soportados:                                                  ║
║    • Olympus Scanlation   (API JSON)                                 ║
║    • Nexus Scanlation     (API JSON, imágenes con descramble)        ║
║    • Temple Scan          (WordPress/Madara)                         ║
║    • ManhwasWEB           (API JSON)                                 ║
║    • Dragon Translation   (WordPress/Madara)                         ║
║    • Ikigai Mangas        (SSR Qwik, dominios rotativos)             ║
║    • LeerCapitulo         (plataforma propia, imágenes vía Selenium) ║
║    • Tauro Scan           (WordPress/Madara)                         ║
║    • ZonaTMO              (plataforma propia, grupos de scanlation)  ║
║                                                                      ║
║  Instalar deps: pip install requests beautifulsoup4 Pillow tqdm      ║
║                 pycryptodome selenium                                ║
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
import unicodedata
import statistics
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin

# Al correr con la salida conectada a una consola real de Windows, Python
# detecta sin problema un encoding compatible con emojis y bordes
# decorativos (═║╔╗, etc). Pero cuando la salida está REDIRIGIDA — a un
# pipe, como hace Node.js al lanzar este script desde el panel de la app
# web, o a un archivo con ">" — Windows cae al codepage ANSI heredado
# (cp1252 en la mayoría de las instalaciones) en vez de UTF-8, y ESE
# codepage no tiene la mayoría de esos caracteres: cada log con un emoji
# revienta con UnicodeEncodeError. Nunca pasa corriéndolo a mano en una
# terminal — pasa recién al lanzarlo desde la app. errors="replace" es
# además una red de seguridad: si en el futuro aparece algún carácter que
# ni así se pueda mostrar, se cambia por un "?" en vez de tirar abajo el
# logging entero.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ══════════════════════════════════════════════════════════════════════
# §0  AUTO-INSTALADOR
# ══════════════════════════════════════════════════════════════════════

_PAQUETES = {
    "requests":  "requests",
    "bs4":       "beautifulsoup4",
    "PIL":       "Pillow",
    "tqdm":      "tqdm",
    "Crypto":    "pycryptodome",  # usado por ManhwasWeb para capítulos cifrados
    "selenium":  "selenium",      # usado por LeerCapitulo (siempre) y como
                                   # fallback vía navegador real en otras
                                   # fuentes — antes faltaba acá, así que
                                   # nunca se instalaba solo con correr
                                   # el scraper la primera vez.
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
ESTADO_VIVO_PATH  = SCRIPT_DIR / "estado_vivo.json"   # estado en vivo para la app web
DETENER_FLAG_PATH = SCRIPT_DIR / "detener.flag"        # presencia = "pedido de detener"

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
INTERVALO_HORAS   = 1

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

# Cuántas veces se reutiliza el mismo driver de Selenium (mismo
# navegador/pestaña) antes de cerrarlo y lanzar uno fresco, dentro del
# mismo manga. Reutilizar ahorra el arranque de Brave en cada capítulo
# (~6-8s), pero una pestaña que navega demasiadas veces seguidas sin
# reiniciar puede ir acumulando basura (memoria, conexiones de
# anuncios/trackers de las páginas del lector, etc.) hasta trabarse —
# esto es una sospecha, no una certeza confirmada, pero coincide con
# cuelgues de Selenium vistos en corridas largas. Reciclar cada tantas
# navegaciones es un punto medio: no se paga el arranque en CADA
# capítulo, pero tampoco se deja crecer una sola sesión indefinidamente
# durante un manga de 40+ capítulos.
MAX_NAVEGACIONES_POR_DRIVER = 8

# Si Selenium no encuentra ninguna imagen en un capítulo (o solo las
# encuentra por el selector de respaldo genérico, señal de que algo
# está raro), vuelca el HTML de la página acá para poder revisarlo a
# mano. No forma parte del reporte de escaneos porque es un volcado
# crudo de depuración, no un resumen — se sobrescribe en cada caso
# nuevo, no se acumula.
DEBUG_SELENIUM_PATH = SCRIPT_DIR / "debug_selenium.html"

# Cuando Selenium SOLO encuentra imágenes vía el selector de respaldo
# genérico (los selectores específicos del lector no encontraron
# nada), es señal de que la página no es realmente el capítulo sino
# una página de error/caída (ej: banner "404", ícono de "cerrar",
# ambos recogidos igual por ser <img> genéricas). Si además la
# cantidad de imágenes encontradas es baja, es casi seguro que se
# trata de eso y no de un capítulo real con markup atípico — un
# capítulo real casi siempre trae bastantes más páginas que esto.
# Por debajo de este umbral, se descarta el resultado completo
# (se trata como "sin imágenes" → error/reintento) en vez de
# devolverlo para descarga. Por encima, se asume que es un capítulo
# real con un lector no estándar y se deja pasar (con warning igual).
UMBRAL_FALLBACK_SOSPECHOSO = 5

# ── Abandonar requests plano por manga ──────────────────────────────
# Algunos sitios (Madara) le dan contenido distinto a una sesión de
# requests que a un navegador real — pasó con Dragon después de su
# rediseño: el HTML SÍ trae las imágenes cuando lo pide un navegador,
# pero la sesión de requests del scraper nunca las encuentra, capítulo
# tras capítulo. Insistir con requests en cada capítulo de ese manga
# es puro tiempo tirado (aunque el intento en sí sea rápido, no es
# gratis, y son decenas de capítulos). Tras esta cantidad de fallos
# SEGUIDOS dentro del mismo manga, se deja de intentar por requests y
# se va directo a Selenium el resto de los capítulos de ESE manga —
# el contador es por instancia de scraper, así que el próximo escaneo
# (scraper nuevo) vuelve a probar con requests desde cero, por si el
# sitio se arregló solo mientras tanto.
UMBRAL_ABANDONAR_REQUESTS = 2

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
#
# ORDEN_FUENTES_DEFAULT es el orden de fábrica, fijo. ORDEN_FUENTES es la
# variable que el resto del código realmente lee (arranca igual al
# default) — aplicar_orden_personalizado() la reasigna una vez al empezar
# cada ciclo si seguimiento.json trae un 'orden_fuentes' propio (se
# configura desde el panel del scraper en el navegador, abajo del
# Administrador de fuentes). Queda como variable de módulo en vez de
# pasarse como parámetro por todos lados para no tener que tocar cada
# función que ya la usa (prioridad_automatica_de, el sort del orden de
# escaneo) — todas la leen por nombre en el momento en que corren, así
# que ven el valor ya actualizado del ciclo actual sin cambiar nada más.
ORDEN_FUENTES_DEFAULT = ["olympus", "nexus", "temple", "dragon", "ikigai", "taurus", "leercapitulo", "manhwaweb", "tmo"]
ORDEN_FUENTES = list(ORDEN_FUENTES_DEFAULT)


def aplicar_orden_personalizado(data: dict) -> None:
    """
    Si seguimiento.json trae un array top-level 'orden_fuentes' (ej.
    ["nexus", "olympus", "temple", ...]), lo usa para pisar ORDEN_FUENTES
    para ESTE ciclo — afecta tanto el orden de escaneo como la prioridad
    automática de cualquier manga multi-fuente que no tenga
    'prioridad_fuente' forzado a mano. 100% opcional: si la clave no
    está, está vacía, o no es una lista, se usa el orden de fábrica sin
    tocar nada.

    Permisivo con listas incompletas o con nombres desconocidos: las
    fuentes válidas que aparecen se usan en el orden dado, y cualquier
    fuente de ORDEN_FUENTES_DEFAULT que falte en la lista se agrega al
    final (en su propio orden relativo) — así un 'orden_fuentes' viejo
    que no incluya una fuente agregada después (ej. si mañana se suma un
    sitio nuevo) no la deja afuera del escaneo, solo la manda al final.
    """
    global ORDEN_FUENTES
    personalizado = data.get("orden_fuentes")
    if not isinstance(personalizado, list) or not personalizado:
        ORDEN_FUENTES = list(ORDEN_FUENTES_DEFAULT)
        return

    validas = [f for f in personalizado if f in ORDEN_FUENTES_DEFAULT]
    faltantes = [f for f in ORDEN_FUENTES_DEFAULT if f not in validas]
    nuevo_orden = validas + faltantes

    if nuevo_orden != ORDEN_FUENTES:
        log.info(f"  🔀 Orden de fuentes personalizado: {' > '.join(nuevo_orden)}")
    ORDEN_FUENTES = nuevo_orden

# Perfiles de filtro de imagen por sitio (dimensiones mínimas, ratio
# máximo, umbral de "ícono cuadrado", tolerancia de ancho respecto al
# dominante del capítulo, mínimo de páginas antes de activar el modo
# relajado). Antes vivía como dict local dentro de descargar_capitulo,
# reconstruido en cada llamada — subido a nivel de módulo para que sea
# más fácil encontrarlo y ajustarlo sin tener que buscarlo adentro de
# un método de 200 líneas.
#
# tol_pct +5pp en todos los perfiles (2026-08-18, a pedido explícito):
# margen de ancho un poco más generoso contra el ancho dominante del
# capítulo ('fuera-perfil'), para que pasen páginas legítimas algo más
# anchas (splash pages, láminas a color) sin tener que tocar ratio_max
# ni cuadrado_max — esos dos quedan como estaban a propósito, son la
# defensa real contra portadas/avatares/imágenes de comentarios, que
# acá no se quiere aflojar. Aplica a las 9 fuentes por igual (este
# dict es por sitio, no por manga).
FILTROS = {
    "olympus":   {"ancho_min": 200, "alto_min": 200, "ratio_max": 3.5, "cuadrado_max": 500, "tol_pct": 20, "fallback_min": 2},
    "temple":    {"ancho_min": 150, "alto_min": 150, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 35, "fallback_min": 2},
    "dragon":    {"ancho_min": 150, "alto_min": 150, "ratio_max": 4.0, "cuadrado_max": 500, "tol_pct": 25, "fallback_min": 2},
    "manhwaweb": {"ancho_min": 300, "alto_min": 300, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 35, "fallback_min": 2},
    "nexus":     {"ancho_min": 300, "alto_min": 300, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 35, "fallback_min": 2},
    # Ikigai ya descarta los banners de publicidad por ruta de URL
    # exacta en obtener_imagenes (más confiable, ver esa función) —
    # este perfil es solo una red de seguridad secundaria.
    "ikigai":    {"ancho_min": 300, "alto_min": 300, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 30, "fallback_min": 2},
    # Valores de partida (sin datos reales de resolución todavía — el
    # sitio no expone dimensiones antes de descargar). tol_pct relajado
    # a propósito porque no sabemos aún qué tan parejo es el ancho entre
    # páginas. Ajustar después de la primera corrida real con --una-vez.
    "leercapitulo": {"ancho_min": 150, "alto_min": 150, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 35, "fallback_min": 2},
    # Mismo perfil que Dragon: también es Madara/WP-manga estándar, sin
    # datos propios de resolución todavía — ajustar tras la primera
    # corrida real con --una-vez si hace falta.
    "taurus":     {"ancho_min": 150, "alto_min": 150, "ratio_max": 4.0, "cuadrado_max": 500, "tol_pct": 25, "fallback_min": 2},
    # Mismo perfil que manhwaweb (pedido explícito: tmo arranca con el
    # perfil de manhwaweb por defecto, y cambia al modo flexible de
    # 'tipo_contenido: "manga"' cuando se especifique por manga). En la
    # práctica esta tabla es solo red de seguridad secundaria — la
    # extracción real de imágenes filtra por patrón de URL exacto
    # (storage.zonatmo.org/chapters/<id>/<n>.webp), no por dimensión.
    "tmo":        {"ancho_min": 300, "alto_min": 300, "ratio_max": 4.0, "cuadrado_max": 400, "tol_pct": 35, "fallback_min": 2},
}


# ── Detector de capítulos sospechosos por conteo de páginas ───────────
# Chequeo pasivo, solo para el log — nunca bloquea, reintenta ni borra
# nada por su cuenta, mismo espíritu que el chequeo de orden de Olympus
# o el de Ikigai (ver auditoría 1.4): la idea es que un capítulo con
# muchas menos páginas que sus vecinos salte a la vista EN el log de
# una corrida grande (88 manga, cientos de capítulos), en vez de
# descubrirse recién cuando alguien lo abre a leer meses después — que
# es exactamente cómo se pasaron por alto los huecos de los scripts
# viejos que motivaron todo este repaso.
#
# Guarda, por manga (clave = ruta de carpeta), el conteo de páginas
# guardadas de los últimos capítulos YA procesados en ESTA corrida
# (no persiste entre corridas — se reconstruye desde cero cada vez que
# se arranca el scraper, así que no hace falta mantener un archivo
# aparte ni preocuparse de que quede desactualizado).
HISTORIAL_PAGINAS_MANGA: dict[str, list[int]] = {}
MIN_HISTORIAL_SOSPECHA    = 3     # no avisa hasta tener al menos esta cantidad de capítulos previos como base
VENTANA_HISTORIAL_SOSPECHA = 10   # compara contra la mediana de los últimos N, no de la carrera completa —
                                   # así un manga que cambia de formato a mitad de camino no queda comparado
                                   # para siempre contra capítulos de una era distinta
UMBRAL_SOSPECHA_PCT = 0.5         # avisa si el capítulo actual tiene menos de esta fracción de la mediana reciente

def _chequear_paginas_sospechosas(carpeta_manga: Path, numero, cuenta_final: int):
    """
    Compara cuenta_final contra la mediana de los últimos capítulos de
    este mismo manga ya procesados en esta corrida. Si el capítulo
    actual queda muy por debajo, imprime una alarma visual bien
    marcada — el capítulo se guarda igual, esto es puramente
    informativo para que después sea fácil de encontrar en el log.

    Devuelve (disparo: bool, mediana: float|None) además de imprimir,
    para que descargar_capitulo() pueda dejarlo anotado en el registro
    de progreso del capítulo y así, más tarde, el resumen final de
    ciclo_escaneo() (ver ReporteEscaneo, pensado para alguien que no
    mira la terminal en vivo) lo muestre agrupado por manga en vez de
    depender de que alguien haya visto pasar el 🚩 en el momento.
    """
    clave     = str(carpeta_manga)
    historial = HISTORIAL_PAGINAS_MANGA.setdefault(clave, [])
    disparo, mediana = False, None
    if len(historial) >= MIN_HISTORIAL_SOSPECHA:
        base    = historial[-VENTANA_HISTORIAL_SOSPECHA:]
        mediana = statistics.median(base)
        if mediana > 0 and cuenta_final < mediana * UMBRAL_SOSPECHA_PCT:
            disparo = True
            consola(f"\n  {'🚩'*3}  SOSPECHOSO: cap {numero} tiene solo {cuenta_final} "
                       f"página(s), muy por debajo de la mediana reciente de este manga "
                       f"({mediana:.0f}, sobre los últimos {len(base)} capítulos) — "
                       f"revisar manualmente  {'🚩'*3}")
            log.warning(f"  [SOSPECHOSO] {carpeta_manga.name} cap {numero}: {cuenta_final} "
                       f"páginas vs mediana reciente {mediana:.0f}")
    historial.append(cuenta_final)
    return disparo, mediana

# Confirmado con dos casos reales (2026-08-22, Dragon Translation): un
# archivo de 110 bytes y uno de 0 bytes, los dos servidos con
# Content-Type: image/jpeg y status 200 — ninguna imagen real de manga
# pesa eso, es un archivo genuinamente roto/vacío subido así al
# servidor de origen. No es algo que el scraper pueda arreglar
# reintentando (se confirmó que se repite idéntico entre corridas
# separadas por horas) — lo único que vale la pena es que el log lo
# diga así de claro, en vez de un genérico "no se pudo decodificar"
# que obliga a repetir el mismo diagnóstico manual (Invoke-WebRequest +
# mirar Content-Type y tamaño) cada vez que vuelve a pasar.
UMBRAL_ARCHIVO_ROTO_BYTES = 1024

def _motivo_imagen_invalida(datos, error: Exception) -> str:
    """Arma el texto de log para cuando se bajaron bytes pero Pillow no
    los pudo abrir como imagen — distingue el caso 'archivo roto/vacío
    en origen' del genérico 'no se pudo decodificar'."""
    n = len(datos) if datos else 0
    if n < UMBRAL_ARCHIVO_ROTO_BYTES:
        return (f"archivo roto/vacío en el servidor de origen ({n} bytes — "
                f"ninguna imagen real pesa tan poco; esto no se arregla "
                f"reintentando, hay que esperar a que el sitio lo resuba)")
    return f"no son una imagen válida ({type(error).__name__})"

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

        # Dos fuentes DISTINTAS con la misma prioridad efectiva.
        #
        # La prioridad AUTOMÁTICA (derivada de ORDEN_FUENTES) nunca puede
        # empatar entre dos fuentes distintas — cada una tiene un índice
        # único. Así que cualquier empate que aparezca acá SIEMPRE fue
        # puesto a mano por alguien en 'prioridad_fuente'. Dos casos:
        #
        #   - Si TODAS las entradas empatadas lo fijaron a mano → es
        #     "doble prioridad" deliberada, una configuración soportada.
        #     Se informa cómo se resuelve (gana quien descargue primero,
        #     y esa versión queda fija — ver comparación de prioridad en
        #     _descargar_caps_nuevos), no se sugiere "arreglar" nada.
        #   - Si alguna quedó SIN fijar (coincide con la de otra por
        #     casualidad) → sí conviene avisar, porque probablemente no
        #     fue intencional.
        por_prioridad: dict[int, list[dict]] = {}
        for e in entradas:
            p = prioridad_efectiva(e)
            por_prioridad.setdefault(p, []).append(e)
        for p, es in por_prioridad.items():
            fs = sorted({e.get("fuente", "?") for e in es})
            if len(fs) <= 1:
                continue
            todas_manuales = all(e.get("prioridad_fuente") is not None for e in es)
            if todas_manuales:
                log.info(f"  ℹ  '{carpeta}' — {', '.join(fs)} comparten prioridad "
                         f"manual ({p}): doble prioridad soportada. Si un capítulo "
                         f"nuevo aparece en más de una al mismo tiempo, se queda "
                         f"la que se descargue primero y esa versión no se "
                         f"reemplaza después.")
            else:
                log.warning(f"  ⚠  '{carpeta}' tiene fuentes distintas "
                           f"({', '.join(fs)}) con la MISMA prioridad ({p}) sin "
                           f"que todas la hayan fijado a mano — probablemente sin "
                           f"querer. Si es intencional, fijá 'prioridad_fuente' "
                           f"manual en las {len(fs)} entradas para dejarlo claro.")


def filtrar_fuentes_desactivadas(mangas: list[dict], data: dict) -> list[dict]:
    """
    Corta por completo los mangas de cualquier fuente marcada en
    'false' dentro de seguimiento.json:

        {
          "fuentes_activas": { "temple": false },
          "mangas": [ ... ]
        }

    Toda fuente que NO aparezca en 'fuentes_activas' se asume activa
    (true) — mismo criterio que 'activo' por manga y que ORDEN_FUENTES:
    omitir algo en la config nunca lo desactiva por accidente.

    Vive en seguimiento.json (no como constante en el .py) a propósito:
    se relee entero en cada ciclo (ver cargar_seguimiento(), llamada al
    principio de ciclo_escaneo()), así que activar/desactivar una
    fuente no requiere editar código ni reiniciar el proceso — el
    próximo ciclo ya lo toma solo.

    No borra ni toca nada en disco: los capítulos ya descargados de una
    fuente desactivada quedan intactos, y 'ultimo_capitulo' de esos
    mangas simplemente no avanza mientras la fuente esté apagada — al
    reactivarla, sigue exactamente donde había quedado.
    """
    fuentes_activas_cfg = data.get("fuentes_activas", {})
    fuentes_off = sorted(f for f, activa in fuentes_activas_cfg.items() if not activa)
    if not fuentes_off:
        return mangas

    conteo = Counter(m.get("fuente") for m in mangas if m.get("fuente") in fuentes_off)
    for f in fuentes_off:
        cant = conteo.get(f, 0)
        if cant:
            log.info(f"  ⏸  Fuente '{f}' desactivada (fuentes_activas en "
                     f"seguimiento.json) — {cant} manga(s) omitido(s) este ciclo, "
                     f"nada se descarga de ahí hasta que la vuelvas a poner en true")
        else:
            log.info(f"  ⏸  Fuente '{f}' desactivada (fuentes_activas en "
                     f"seguimiento.json) — sin mangas de esa fuente ahora mismo")

    return [m for m in mangas if m.get("fuente") not in fuentes_off]


# ── Olympus: páginas de novedades a escanear para recuperar slugs ───
# Fallback SECUNDARIO de recuperación de slug (después del catálogo
# completo /api/series/list, que es más rápido y es el primer intento).
# Se dispara tanto si el slug guardado da 404 como si la API de
# capítulos falla por error de conexión persistente — en ambos casos
# puede significar "el slug venció". Empíricamente (2026-07-11):
# Olympus reordena /capitulos?page=N cada vez que un manga saca
# capítulo nuevo, empujándolo a la página 1; con el total de páginas
# actual (57, antes >800), la página 25 ya cubre ~9 meses de
# antigüedad — de sobra para cualquier manga que se siga actualizando.
# El listado de QUÉ descargar de cada manga viene siempre de su
# página de serie completa, no de estas páginas de novedades.
OLYMPUS_MAX_PAGINAS_NOVEDADES = 25

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
class _FormatoCondicional(logging.Formatter):
    """Igual que un Formatter normal, salvo que si el record viene marcado
    con extra={'crudo': True} devuelve el mensaje tal cual, sin timestamp
    ni nivel. Se usa para las líneas de "arte" (headers de capítulo, tabla
    de resoluciones, banners) que antes iban directo por tqdm.write() y
    por eso nunca llegaban a scraper.log ni a la consola en vivo de la web
    — ahora pasan por el logger igual que todo lo demás, pero conservan su
    formato original en vez de heredar el de un log line normal."""
    def format(self, record):
        if getattr(record, "crudo", False):
            return record.getMessage()
        return super().format(record)

_fmt = _FormatoCondicional("%(asctime)s  %(levelname)-8s  %(message)s",
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


def consola(msg=""):
    """Reemplazo de tqdm.write() para las líneas de reporte "bonito"
    (headers de capítulo, tabla de resoluciones, candidatos, velocidad,
    perfil de imagen, etc). Antes esas líneas se imprimían directo por
    tqdm.write() y nunca pasaban por el logger, así que no quedaban en
    scraper.log ni llegaban a la consola en vivo de la web — solo se
    veían en la terminal del PC.

    Ahora pasan por log.info(..., extra={'crudo': True}):
      - _tqdm_handler sigue llamando tqdm.write() puertas adentro, así
        que la terminal del PC se ve exactamente igual que antes, bien
        coordinada con las barras de progreso.
      - _file_handler (RotatingFileHandler, con su lock y rotación ya
        resueltos) ahora también escribe la línea a scraper.log, sin el
        timestamp/nivel de un log normal gracias a _FormatoCondicional.
      - Node tailea scraper.log línea por línea para el SSE, así que la
        web termina mostrando lo mismo que la terminal, sin tocar nada
        del lado de Node ni del cliente.
    """
    log.info(msg, extra={"crudo": True})

# ══════════════════════════════════════════════════════════════════════
# §2b  CONTROL EN VIVO (estado para la app web + parada solicitada)
# ══════════════════════════════════════════════════════════════════════
#
# estado_vivo.json refleja en todo momento qué está haciendo el scraper,
# para que server/routes/scraperControl.js lo pueda leer sin tener que
# adivinar nada sobre PIDs de Windows. Se actualiza sin importar cómo
# se arrancó este proceso — corrido a mano desde VS Code o disparado
# desde la app web escriben exactamente el mismo archivo, así que la
# app siempre ve el estado real.
#
# detener.flag es la señal de "pará": cualquier proceso puede crearlo
# (la app web lo hace desde /api/admin/scraper/stop), y este proceso lo
# chequea en puntos naturales (entre capítulo, entre manga, durante la
# espera del modo continuo) y sale limpio por el MISMO camino que ya usa
# Ctrl+C hoy — mismo `finally: _liberar_lock()`, mismo cierre de
# Selenium. Se usa un archivo en vez de mandar una señal del SO a
# propósito: en Windows, un SIGINT/SIGTERM mandado desde Node.js NO
# llega como señal capturable — el proceso se mata de un hachazo, sin
# pasar por ningún finally (documentado así por el propio Node.js). Un
# archivo-bandera es más lento que una señal real, pero funciona igual
# sin importar el sistema operativo.

class DetenerScraperError(BaseException):
    """Señal interna de 'pedido de detener'. Hereda de BaseException
    (NO de Exception) a propósito, igual que KeyboardInterrupt — así
    los `except Exception` que ya existen por todo este archivo (para
    que un manga con error no tire abajo el ciclo entero) no la atrapan
    sin querer y siguen de largo al próximo manga. Se propaga limpio
    hasta main(), sin tocar ninguno de esos except."""
    pass


class ControlEjecucion:
    """Escribe estado_vivo.json y chequea detener.flag. Pensado para
    llamarse seguido (una vez por capítulo, en loops de cientos de
    capítulos) sin generar de más: el archivo solo se reescribe como
    máximo una vez por segundo salvo en transiciones de estado
    importantes (iniciar/esperar/terminar), que siempre se escriben
    al toque para que la app no las vea con demora."""

    def __init__(self):
        self._ultimo_escrito = 0.0
        self._manga_actual   = 0
        self._total_mangas   = 0
        self._nombre_manga   = ""
        self._cap_actual     = 0
        self._cap_total      = 0
        self._modo           = None    # "once" | "continuous" — fijo para toda la corrida
        self._esperando      = False   # True solo durante la espera entre ciclos del modo continuo
        self._since          = None
        self._proximo_ciclo  = None

    # ── Parada solicitada ────────────────────────────────────────────
    def debe_detenerse(self) -> bool:
        return DETENER_FLAG_PATH.exists()

    def chequear_y_lanzar(self):
        """Llamar en los puntos de corte naturales del loop principal
        (entre capítulo, entre manga, durante la espera del scheduler)."""
        if self.debe_detenerse():
            raise DetenerScraperError()

    # ── Ciclo de vida ────────────────────────────────────────────────
    def iniciar(self, modo: str):
        """Se llama UNA vez al arrancar la corrida completa: para
        --una-vez, al principio de main(); para el modo continuo, una
        sola vez antes del while de scheduler() (no en cada ciclo, así
        'since' refleja cuándo se prendió el modo continuo, no el
        último ciclo puntual)."""
        self._modo            = modo
        self._since           = datetime.now().isoformat()
        self._esperando       = False
        self._manga_actual    = 0
        self._total_mangas    = 0
        self._nombre_manga    = ""
        self._cap_actual      = 0
        self._cap_total       = 0
        self._proximo_ciclo   = None
        self._escribir(forzar=True)

    def iniciar_ciclo(self, total_mangas: int):
        """Se llama al principio de CADA ciclo_escaneo() — en modo
        continuo eso pasa una vez por hora, y cada vez el contador de
        manga_actual arranca de nuevo desde 0 para ese ciclo puntual."""
        self._esperando    = False
        self._manga_actual = 0
        self._total_mangas = total_mangas
        self._nombre_manga = ""
        self._cap_actual    = 0
        self._cap_total     = 0
        self._escribir(forzar=True)

    def avanzar_manga(self, nombre: str):
        self._manga_actual += 1
        self._nombre_manga  = nombre
        self._cap_actual    = 0
        self._cap_total     = 0
        self._escribir(forzar=True)

    def avanzar_capitulo(self, actual: int, total: int):
        self._cap_actual = actual
        self._cap_total  = total
        self._escribir()

    def marcar_esperando(self, proximo_ciclo_ts: float):
        self._esperando     = True
        self._proximo_ciclo = datetime.fromtimestamp(proximo_ciclo_ts).isoformat()
        self._escribir(forzar=True)

    def finalizar(self, detenido_manualmente: bool = False):
        """Se llama SIEMPRE al salir de main() — haya terminado solo,
        se haya pedido detener, o haya reventado con un error. Mismo
        principio que _liberar_lock(): nunca puede quedar 'corriendo'
        de forma fantasma en estado_vivo.json."""
        try:
            DETENER_FLAG_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        self._escribir_archivo({
            "status": "idle",
            "pid": os.getpid(),
            "since": None,
            "heartbeat": datetime.now().isoformat(),
            "progress": None,
            "nextCycleAt": None,
            "lastStoppedManually": detenido_manualmente,
        })

    # ── Escritura ────────────────────────────────────────────────────
    def _estado_actual(self) -> str:
        if self._esperando:
            return "waiting_next_cycle"
        if self._modo == "once":
            return "running_once"
        if self._modo == "continuous":
            return "running_continuous"
        return "idle"

    def _escribir(self, forzar: bool = False):
        ahora = time.time()
        if not forzar and (ahora - self._ultimo_escrito) < 1.0:
            return
        self._ultimo_escrito = ahora
        self._escribir_archivo({
            "status": self._estado_actual(),
            "pid": os.getpid(),
            "since": self._since,
            "heartbeat": datetime.now().isoformat(),
            "progress": {
                "mangaActual": self._manga_actual,
                "totalMangas": self._total_mangas,
                "nombreManga": self._nombre_manga,
                "capituloActual": self._cap_actual,
                "capituloTotal": self._cap_total,
            } if (self._modo in ("once", "continuous") and not self._esperando) else None,
            "nextCycleAt": self._proximo_ciclo,
        })

    def _escribir_archivo(self, payload: dict):
        # Mismo patrón atómico que guardar_seguimiento(): escribir a un
        # .tmp y reemplazar, para que Node nunca lea un JSON a mitad de
        # escribir.
        #
        # 2026-08-23: reintento corto agregado tras confirmar que el
        # WinError 5 (acceso denegado) se repetía más de una vez en la
        # práctica, no era un caso aislado. En Windows esto típicamente
        # pasa porque otro proceso (el server Node leyendo el archivo,
        # un antivirus, el indexador de Windows) tiene el archivo
        # abierto en el instante exacto del reemplazo — casi siempre se
        # libera solo en milisegundos, así que unos pocos reintentos
        # con una pausa mínima alcanzan sin agregar demora real al
        # escaneo (esto se llama como mucho una vez por segundo).
        tmp = ESTADO_VIVO_PATH.with_suffix(".tmp")
        intentos = 3
        for intento in range(1, intentos + 1):
            try:
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                tmp.replace(ESTADO_VIVO_PATH)
                return
            except OSError as e:
                if intento == intentos:
                    log.warning(f"No se pudo escribir estado_vivo.json tras "
                               f"{intentos} intentos: {e}")
                else:
                    time.sleep(0.1 * intento)


CONTROL = ControlEjecucion()

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
      Olympus:      https://olympusxyz.com/series/comic-{slug}
      Temple:       https://{dominio}/manga/{slug}/  (o variantes con prefijo)
      Dragon:       https://dragontranslation.org/manga/{slug}/
      ManhwasWEB:   https://manhwaweb.com/manhwa/{slug}
      Nexus:        https://nexusscanlation.com/series/{slug}
      LeerCapitulo: https://www.leercapitulo.co/manga/{id}/{slug-largo}/
      Tauro:        https://lectortaurus.com/manga/{slug}/
      TMO:          https://zonatmo.org/library/manga/{id}/{slug}

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
    elif fuente in ("temple", "dragon", "taurus"):
        # .../manga/{slug}/  o  .../serie/{slug}/  etc — tomar el
        # último segmento no vacío del path como slug.
        partes = [p for p in path.split("/") if p]
        if partes:
            # Si el primer segmento es un prefijo conocido, lo descartamos
            if partes[0] in ("manga", "serie", "manhwa", "comic", "webtoon") and len(partes) > 1:
                slug_extraido = partes[1]
            else:
                slug_extraido = partes[-1]
    elif fuente == "leercapitulo":
        # .../manga/{id}/{slug-largo}/  — acá el "slug" que usamos
        # internamente son DOS segmentos juntos (id + slug legible),
        # no uno solo como en Temple/Dragon, así que se unen con "/".
        partes = [p for p in path.split("/") if p]
        if partes and partes[0] == "manga" and len(partes) > 1:
            slug_extraido = "/".join(partes[1:])
    elif fuente == "tmo":
        # .../library/manga/{id}/{slug}  — mismo criterio que
        # LeerCapitulo: el slug interno son los dos segmentos después
        # del prefijo, unidos con "/" (id + nombre legible).
        partes = [p for p in path.split("/") if p]
        if len(partes) >= 3 and partes[0] == "library" and partes[1] == "manga":
            slug_extraido = "/".join(partes[2:])

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

_CARACTERES_INVALIDOS_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def sanitizar_nombre_carpeta(nombre: str) -> str:
    """
    Limpia un nombre para que sea válido y estable como carpeta en
    Windows. Existe porque 'nombre_carpeta' viene tal cual del
    seguimiento.json (cargado a mano o desde el panel web) y nunca se
    valida antes de usarse para construir rutas.

    Cubre dos clases de bug real ya vistas:
    1. Espacios (o puntos) al final del nombre: Windows los recorta
       silenciosamente al CREAR la carpeta, pero el string original
       (con el espacio) se sigue usando para las rutas de los
       capítulos dentro de ella → [WinError 3] ruta no encontrada,
       porque esa ruta con espacio nunca existió de verdad en disco.
    2. Caracteres no permitidos en nombres de archivo/carpeta de
       Windows (: " / \\ | ? * < > y de control) — si un título
       scrapeado o cargado a mano trae alguno (ej. 'Título: Parte 2'),
       el mkdir() falla directo, sin el recorte silencioso del caso 1.

    Se reemplazan por '-' en vez de borrarlos, para no juntar dos
    palabras que quedarían pegadas.
    """
    if not nombre:
        return nombre
    limpio = _CARACTERES_INVALIDOS_WINDOWS.sub("-", nombre)
    limpio = re.sub(r"\s+", " ", limpio).strip()
    limpio = limpio.rstrip(". ")  # Windows tampoco permite terminar en '.'
    return limpio or nombre  # red de seguridad: nunca devolver ""

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
                        fuente: str = None, prioridad_fuente=None, sospechoso=None):
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

    'sospechoso', si no es None, es la mediana reciente de páginas de
    este manga contra la que el capítulo actual quedó muy por debajo
    (ver _chequear_paginas_sospechosas) — se guarda acá para que el
    resumen final de ciclo_escaneo() lo pueda mostrar agrupado por
    manga sin depender de haber visto pasar el 🚩 en vivo.
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
        "sospechoso_mediana": sospechoso,
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
        # Driver de Selenium reutilizado entre capítulos de un mismo manga
        # (ver _checkout_driver_selenium / cerrar_driver_selenium) — evita
        # relanzar el navegador en cada capítulo. El lock evita que dos
        # hilos lo usen a la vez (puede pasar si un capítulo anterior
        # quedó "colgado" por timeout y su hilo daemon sigue vivo en
        # segundo plano — ver _descargar_con_timeout).
        self._driver_selenium_activo = None
        self._driver_selenium_lock   = threading.Lock()
        # Cuántos capítulos lleva navegados el driver activo sin
        # reciclarse (ver MAX_NAVEGACIONES_POR_DRIVER en config).
        self._navegaciones_driver_activo = 0

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

        # Motivo específico del último 'return False' de esta llamada —
        # lo lee _descargar_con_timeout() después de que el hilo termina,
        # para que el resumen final (ver ReporteEscaneo) pueda decir POR
        # QUÉ falló un capítulo en vez del genérico "no se pudo
        # descargar" de siempre, sin tener que cambiar la firma de esta
        # función (que devuelve bool en un montón de lugares del código
        # y no vale la pena tocar todos esos call sites).
        self._ultimo_motivo_fallo = None

        # Filtros por sitio (definidos a nivel de módulo, junto a ORDEN_FUENTES)
        if fuente not in FILTROS:
            log.warning(f"  ⚠  '{fuente}' no tiene perfil propio en FILTROS — "
                       f"usando el de 'dragon' por defecto. Convendría agregarle "
                       f"uno específico una vez que se vea qué resoluciones trae.")
        F = FILTROS.get(fuente, FILTROS["dragon"])

        consola(f"\n{'═'*65}")
        consola(f"  📖  CAPÍTULO {num_str}  [{self.nombre}]")
        consola(f"{'─'*65}")
        consola(f"  🔗 {url}")

        # Obtener URLs de imágenes
        urls_imgs = self.obtener_imagenes(url, slug, num)
        if not urls_imgs:
            consola("  ❌ Sin imágenes candidatas")
            self._ultimo_motivo_fallo = "no se encontró ninguna imagen candidata en la página del capítulo"
            return False

        # Deduplicar
        visto_u = set(); urls_unicas = []
        for u in urls_imgs:
            if u not in visto_u:
                visto_u.add(u); urls_unicas.append(u)
        n_dup = len(urls_imgs) - len(urls_unicas)
        consola(f"  🔎 Candidatos: {len(urls_unicas)}"
                   + (f"  (🗑 {n_dup} duplicados)" if n_dup else ""))

        destino.mkdir(parents=True, exist_ok=True)

        # Si esta carpeta ya tenía imágenes numeradas de un intento anterior
        # (capítulo que quedó "parcial" y se reintenta en un escaneo
        # posterior), hay que limpiarlas ANTES de volver a guardar. Antes no
        # se hacía: como el contador de nombres siempre arranca de nuevo en
        # 001, si este intento guarda menos páginas que el anterior (u otra
        # extensión para la misma página), lo viejo nunca se pisa y queda
        # como archivo huérfano — esto puede inflar el conteo de imágenes
        # de un capítulo muy por encima de las páginas reales, con archivos
        # sueltos que no corresponden a la versión actual del capítulo.
        _huerfanas = [f for ext in ("jpg", "jpeg", "png", "webp", "gif")
                      for f in destino.glob(f"*.{ext}")]
        if _huerfanas:
            consola(f"  🧹 Borrando {len(_huerfanas)} imagen(es) de un intento anterior antes de reintentar...")
            for f in _huerfanas:
                try:
                    f.unlink()
                except OSError as e:
                    log.warning(f"  [⚠] No se pudo borrar '{f.name}' de un intento anterior: {e}")

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

        consola(f"\n  📶 {speed.mbps} MB/s  │  {speed.total_mb} MB")

        # ── Cargar imágenes en memoria y calcular dimensiones ─────────
        imgs_ok = {}
        fallidas_desc = 0
        fallidas_idx  = []  # qué posiciones fallaron (no solo cuántas) —
                             # lo usa el detector de huecos del medio más abajo
        fallidas_por_archivo_roto = 0  # cuántas de las de arriba son
                                        # específicamente archivo-roto-en-
                                        # origen (bytes < UMBRAL_ARCHIVO_
                                        # ROTO_BYTES) — para el motivo
                                        # agregado si el capítulo entero
                                        # termina fallando (ver más abajo)
        for idx in sorted(resultados):
            img_url, datos, ok = resultados[idx]
            if not ok or datos is None:
                fallidas_desc += 1
                fallidas_idx.append(idx)
                continue
            try:
                imagen = Image.open(BytesIO(datos))
                ancho, alto = imagen.size
                imgs_ok[idx] = {"url": img_url, "bytes": datos,
                                 "ancho": ancho, "alto": alto}
            except Exception as e:
                # Distinto de un fallo de red (ok=False arriba): acá SÍ
                # se bajaron bytes. _motivo_imagen_invalida distingue el
                # caso "archivo roto/vacío en origen" (confirmado con
                # casos reales) del genérico "no se pudo decodificar".
                if datos is not None and len(datos) < UMBRAL_ARCHIVO_ROTO_BYTES:
                    fallidas_por_archivo_roto += 1
                log.warning(f"  [{self.nombre}] Cap {cap.get('numero')}: bytes descargados "
                           f"pero {_motivo_imagen_invalida(datos, e)} en {img_url}")
                fallidas_desc += 1
                fallidas_idx.append(idx)

        if not imgs_ok:
            consola("  ❌ Ninguna imagen válida")
            if fallidas_por_archivo_roto == len(fallidas_idx) and fallidas_idx:
                self._ultimo_motivo_fallo = (
                    f"las {len(fallidas_idx)} imágenes del capítulo están rotas/vacías "
                    f"en el servidor de origen — no se arregla reintentando")
            else:
                self._ultimo_motivo_fallo = (
                    f"ninguna de las {len(fallidas_idx)} imágenes candidatas se pudo "
                    f"descargar ni decodificar")
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
        consola(f"  📐 Perfil: {tipo}{modo_label}  ancho_dom={ancho_dom}px  "
                   f"ratio_med={ratio_med:.2f}  ({len(anchos_uni)} anchos distintos)")

        # ── Filtrado: decidir qué se acepta (SIN escribir a disco todavía) ──
        # Antes esto guardaba cada imagen al mismo tiempo que se decidía si
        # pasaba el filtro, numerándola con un contador que solo avanzaba
        # con cada guardado exitoso. El fallback de más abajo (modo
        # relajado) reusaba ese mismo contador ya avanzado — una página
        # rechazada en la primera pasada y recuperada después en el
        # fallback SIEMPRE terminaba con un número de archivo más alto que
        # páginas que en la posición real de la página venían DESPUÉS de
        # ella pero que sí habían pasado el filtro a la primera. El
        # capítulo quedaba guardado con las páginas fuera de orden de
        # lectura. Ahora se decide qué imágenes se guardan (acá y en el
        # fallback) sin tocar el disco todavía, y recién al final se
        # ordenan TODAS por su posición real en la página (idx) antes de
        # numerarlas y escribirlas — sin importar en qué pasada se haya
        # decidido aceptar cada una.
        consola("")

        rechazadas_meta = []   # (idx, meta, motivo)
        aceptadas_meta  = []   # (idx, meta) — pasaron el filtro (normal o fallback)
        RUIDO_FN  = ["banner","logo-","zzz-","promo","discord","patreon","default_profile"]
        RUIDO_URL = ["storage/teams","storage/comics/covers","/ads/","/icon/","/avatar/","/social/"]

        def _motivo_ruido(img_url: str) -> str | None:
            """
            Devuelve el patrón EXACTO que hizo matchear el filtro de ruido
            (ej. 'ruido-nombre:discord' o 'ruido-ruta:/ads/'), o None si la
            URL no es ruido. Antes esto era _es_ruido(), que solo devolvía
            True/False — el log decía simplemente 'ruido-url' sin aclarar
            cuál de las ~13 palabras de las dos listas fue la responsable,
            así que diagnosticar un rechazo de este tipo obligaba a leer
            el código para adivinar. Ahora el motivo mismo lo dice.
            """
            fname   = img_url.split("/")[-1].lower()
            url_low = img_url.lower()
            for x in RUIDO_FN:
                if x in fname:
                    return f"ruido-nombre:{x}"
            for x in RUIDO_URL:
                if x in url_low:
                    return f"ruido-ruta:{x}"
            return None

        for idx in sorted(imgs_ok):
            meta    = imgs_ok[idx]
            img_url = meta["url"]
            ancho   = meta["ancho"]
            alto    = meta["alto"]
            ratio   = ancho / alto if alto > 0 else 0
            # El número de acá es la posición REAL en la página (idx+1),
            # no un contador de guardado — sirve para ubicar cada línea
            # del log mientras se está decidiendo, sea que la imagen
            # termine aceptada o rechazada.
            pref = f"  {idx+1:>4}  {ancho}x{alto:<7}  "

            motivo_ruido = _motivo_ruido(img_url)
            if motivo_ruido:
                consola(f"{pref}❌ Rechazada ({motivo_ruido})")
                rechazadas_meta.append((idx, meta, motivo_ruido))
                continue

            motivo = None
            if es_manga_flex:
                # Modo manga flexible: sin límite de ancho máximo, sin
                # ratio_max, sin fuera-perfil — un manga puede tener
                # paneles a color, páginas dobles o capítulos escaneados
                # con otro equipo y todos son páginas reales que hay que
                # guardar. Se mantienen SOLO dos rechazos, los dos que
                # NO dependen de comparar contra el resto del capítulo
                # (que es justo lo que este modo existe para evitar)
                # sino que son una propiedad de la imagen en sí misma:
                #   - imagen rotundamente inútil (rota, minúscula, pixel
                #     de tracking)
                #   - ícono/avatar cuadrado y chico — una página real de
                #     manga prácticamente nunca es cuadrada, ni siquiera
                #     las láminas a color o páginas dobles (esas dan un
                #     ratio ANCHO, no cuadrado). Importa sobre todo para
                #     Temple/Dragon/Taurus/LeerCapitulo, que tienen un
                #     fallback de extracción genérico (escanea TODOS los
                #     <img> de la página si el extractor normal falla) —
                #     ahí, sin este chequeo, un avatar de un widget de
                #     comentarios que no matcheara ninguna palabra de
                #     _motivo_ruido() se colaba entero en modo flexible.
                if ancho < 100 or alto < 100:
                    motivo = f"muy-pequeña:{ancho}x{alto}"
                elif 0.8 < ratio < 1.2 and ancho < F["cuadrado_max"] and alto < F["cuadrado_max"]:
                    motivo = f"icono-cuadrado:{ancho}x{alto}"
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
                consola(f"{pref}❌ Rechazada ({motivo})")
                rechazadas_meta.append((idx, meta, motivo))
                continue

            # Nada que imprimir acá si pasa — se muestra una sola vez,
            # más abajo, en la lista de "Guardando..." (antes se
            # imprimía acá con "pasa filtro" Y de nuevo al guardar con
            # "OK → NNN.webp", duplicando cada línea aceptada).
            aceptadas_meta.append((idx, meta))

        rechazadas = len(rechazadas_meta)

        # ── Fallback si quedan muy pocas imágenes ────────────────────
        if rechazadas_meta and len(aceptadas_meta) < F["fallback_min"]:
            consola(f"\n  ⚠  Solo {len(aceptadas_meta)} imgs — activando modo RELAJADO "
                       f"({len(rechazadas_meta)} a revisar)...")
            recuperadas = 0
            aun_rechazadas = []
            for idx, meta, motivo_orig in rechazadas_meta:
                img_url = meta["url"]
                ancho = meta["ancho"]; alto = meta["alto"]
                ratio = ancho / alto if alto > 0 else 0
                pref  = f"  {idx+1:>4}  {ancho}x{alto:<7}  "
                # El ruido (ads/logos/discord/etc.) sigue filtrado incluso
                # en modo relajado — antes acá solo se revisaba el ratio
                # extremo, así que una imagen de ruido con proporciones
                # "normales" (ej. un ícono cuadrado de Discord) se podía
                # colar como si fuera una página real apenas se activaba
                # el fallback. Solo se relajan los filtros de tamaño y de
                # comparación contra el perfil del capítulo — y ni
                # siquiera el de tamaño del todo: 'muy-pequeña' tampoco
                # se recupera acá (mismo criterio que el detector de
                # huecos más abajo). A diferencia de 'fuera-perfil' o
                # 'banner' (comparaciones de opinión contra el resto del
                # capítulo, que sí pueden estar equivocadas), una imagen
                # que no llega ni al piso mínimo de tamaño es casi con
                # certeza un archivo roto o un pixel de tracking — recién
                # importa de verdad en modo manga flexible
                # (tipo_contenido:"manga"), donde 'muy-pequeña' es el
                # ÚNICO motivo de rechazo posible: sin este chequeo, un
                # capítulo modo manga con muy pocas páginas válidas
                # podía terminar "recuperando" cualquier cosa por debajo
                # de 100px solo porque no tenía ratio extremo.
                if motivo_orig.startswith("ruido-"):
                    consola(f"{pref}❌ Sigue rechazada ({motivo_orig})")
                    aun_rechazadas.append((idx, meta, motivo_orig))
                    continue
                if motivo_orig.startswith("muy-pequeña"):
                    consola(f"{pref}❌ Sigue rechazada (muy-pequeña)")
                    aun_rechazadas.append((idx, meta, motivo_orig))
                    continue
                # Mismo criterio: 'icono-cuadrado' tampoco es una
                # comparación de opinión contra el resto del capítulo
                # (a diferencia de 'fuera-perfil'/'banner', que sí
                # pueden estar equivocados) — es la forma típica de un
                # avatar/ícono, y el modo relajado existe para casos
                # donde casi todo se rechazó, exactamente cuando más
                # fácil es que se cuele algo así si no se lo excluye acá.
                if motivo_orig.startswith("icono-cuadrado"):
                    consola(f"{pref}❌ Sigue rechazada ({motivo_orig})")
                    aun_rechazadas.append((idx, meta, motivo_orig))
                    continue
                if ratio > F["ratio_max"] * 1.5:
                    consola(f"{pref}❌ Sigue rechazada (ratio-extremo)")
                    aun_rechazadas.append((idx, meta, motivo_orig))
                    continue
                consola(f"{pref}♻  Recuperada (orig: {motivo_orig})")
                aceptadas_meta.append((idx, meta))
                recuperadas += 1
            rechazadas_meta = aun_rechazadas
            rechazadas = len(rechazadas_meta)
            if recuperadas:
                consola(f"  ♻  Recuperadas en fallback: {recuperadas}")

        # ── Detector de huecos ──────────────────────────────────────────
        # A diferencia del fallback de arriba (que solo se activa cuando
        # el capítulo ENTERO queda con muy pocas páginas), esto corre
        # SIEMPRE, sin importar cuántas páginas se hayan aceptado en
        # total — un capítulo de 100 páginas al que le faltan 2 nunca
        # dispara el fallback de arriba (98 está muy por encima del
        # mínimo), pero sí es exactamente el caso que esto cubre.
        #
        # (2026-08-21: antes esto solo miraba el "medio" del capítulo,
        # excluyendo a propósito las primeras EDGE_INICIO y últimas
        # EDGE_FINAL páginas — la idea original era no "rescatar" un
        # banner/portada real que a veces se rechaza con razón al
        # principio o al final. Un caso real (manga con 4-6 anchos
        # legítimos distintos por capítulo) mostró que esa exclusión por
        # posición tiraba páginas reales que justo caían cerca del final
        # — la página 11 y 12 de un capítulo de 12, nunca elegibles para
        # recuperar por estar en la "zona de borde", aunque fueran
        # paneles reales del manga. La posición nunca fue una señal
        # confiable de "esto no es una página real" — lo que sí protege
        # de verdad (ratio extremo, icono-cuadrado, muy-pequeña, ruido de
        # URL/nombre) no depende de en qué posición cae la imagen, así
        # que sacamos la restricción de posición del todo: ahora se
        # revisa TODA página que no haya quedado aceptada, sin importar
        # dónde esté, y se le da la misma última oportunidad:
        #   - si el rechazo fue del filtro de dimensión -> se reevalúa
        #     con el mismo criterio relajado de arriba (el ruido de
        #     URL/nombre se sigue respetando igual, nunca se relaja)
        #   - si nunca se pudo descargar -> un intento más de bajarla,
        #     reusando la misma función de descarga (respeta el
        #     detector de bloqueo por dominio, no es un mecanismo
        #     aparte)
        # Lo que se recupere acá entra a aceptadas_meta como cualquier
        # otra y sigue el mismo ordenado-por-idx de más abajo — no hace
        # falta que este bloque se preocupe por el orden final.
        idx_aceptados = {idx for idx, _ in aceptadas_meta}
        huecos_rechazo  = list(rechazadas_meta)
        huecos_descarga = [idx for idx in fallidas_idx if idx not in idx_aceptados]

        if huecos_rechazo or huecos_descarga:
            consola(f"\n  🔎 Detector de huecos: {len(huecos_rechazo) + len(huecos_descarga)} "
                       f"posición(es) sin resolver — reintentando...")

            aun_sin_resolver = []
            recuperadas_filtro = 0
            for idx, meta, motivo_orig in huecos_rechazo:
                ancho = meta["ancho"]; alto = meta["alto"]
                ratio = ancho / alto if alto > 0 else 0
                pref  = f"  {idx+1:>4}  {ancho}x{alto:<7}  "
                # 'muy-pequeña' nunca se recupera, en ninguna posición —
                # a diferencia de 'fuera-perfil' o 'banner' (que son
                # comparaciones de opinión contra el resto del capítulo,
                # y por eso SÍ pueden estar equivocadas), una imagen que
                # no llega ni al piso mínimo de tamaño es casi con
                # certeza un archivo roto o un pixel de tracking, no una
                # página real que midió raro. Esto importa sobre todo en
                # modo manga flexible (tipo_contenido:"manga"), donde
                # 'muy-pequeña' es el ÚNICO motivo de rechazo posible —
                # sin este chequeo, el detector iba a "recuperar" ahí
                # cualquier cosa por debajo de 100px solo porque no
                # tenía ratio extremo.
                if motivo_orig.startswith("ruido-") or motivo_orig.startswith("muy-pequeña") \
                        or motivo_orig.startswith("icono-cuadrado") or ratio > F["ratio_max"] * 1.5:
                    consola(f"{pref}❌ Sigue rechazada (posición {idx+1})")
                    aun_sin_resolver.append(idx + 1)
                    continue
                consola(f"{pref}♻  Recuperada (orig: {motivo_orig})")
                aceptadas_meta.append((idx, meta))
                recuperadas_filtro += 1

            recuperadas_descarga = 0
            for idx in huecos_descarga:
                img_url = resultados[idx][0]
                pref = f"  {idx+1:>4}  {'—':<9}  "
                motivo_ruido_medio = _motivo_ruido(img_url)
                if motivo_ruido_medio:
                    consola(f"{pref}❌ No se reintenta ({motivo_ruido_medio}, posición {idx+1})")
                    aun_sin_resolver.append(idx + 1)
                    continue
                consola(f"{pref}🔄 Reintentando descarga (posición {idx+1})...")
                _, _, datos_r, ok_r = _bajar_raw((idx, img_url))
                if not ok_r or datos_r is None:
                    consola(f"{pref}❌ Se sigue sin poder descargar")
                    aun_sin_resolver.append(idx + 1)
                    continue
                try:
                    imagen_r = Image.open(BytesIO(datos_r))
                    ancho_r, alto_r = imagen_r.size
                except Exception as e:
                    motivo_inv = _motivo_imagen_invalida(datos_r, e)
                    consola(f"{pref}❌ Se descargó pero {motivo_inv}")
                    log.warning(f"  [{self.nombre}] Cap {cap.get('numero')}: bytes descargados "
                               f"pero {motivo_inv} en {img_url}")
                    aun_sin_resolver.append(idx + 1)
                    continue
                consola(f"{pref}♻  Recuperada (había fallado la descarga)")
                aceptadas_meta.append((idx, {"url": img_url, "bytes": datos_r,
                                              "ancho": ancho_r, "alto": alto_r}))
                recuperadas_descarga += 1

            # rechazadas/fallidas_desc quedaban contando como "perdidas" a
            # las que el detector recién recuperó — se ajustan acá para
            # que el resumen final y esperadas_reales (más abajo, decide
            # completado/parcial) reflejen solo lo que sigue siendo un
            # problema de verdad después de este intento extra.
            if recuperadas_filtro or recuperadas_descarga:
                consola(f"  ♻  Recuperadas: {recuperadas_filtro + recuperadas_descarga} "
                           f"({recuperadas_filtro} del filtro, {recuperadas_descarga} de la descarga)")
            rechazadas    -= recuperadas_filtro
            fallidas_desc -= recuperadas_descarga

            if aun_sin_resolver:
                consola(f"  ⚠  Quedaron {len(aun_sin_resolver)} posición(es) sin poder "
                           f"recuperar: página(s) {', '.join(str(p) for p in aun_sin_resolver)} "
                           f"de {len(urls_unicas)} — revisar el capítulo a mano si molesta.")

        # ── Guardar TODAS las aceptadas juntas, ordenadas por su posición
        # REAL en la página (idx) — no por el orden en que se decidieron
        # (normal o fallback). Acá es donde se arma el número de archivo
        # final, así que es acá donde importa el orden correcto.
        aceptadas_meta.sort(key=lambda t: t[0])
        guardadas = corrompidas = 0
        contador  = 1
        if aceptadas_meta:
            consola(f"\n  Guardando {len(aceptadas_meta)} imagen(es) en orden final de lectura...")
        for idx, meta in aceptadas_meta:
            img_url = meta["url"]; datos = meta["bytes"]
            ancho = meta["ancho"]; alto = meta["alto"]
            pref  = f"  {contador:>4}  {ancho}x{alto:<7}  "
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
                    consola(f"{pref}✅ OK → {nombre_img}")
                except Exception as e:
                    consola(f"{pref}⚠  CORROMPIDA: {e}")
                    corrompidas += 1
                    ruta.unlink(missing_ok=True)
                    continue
                guardadas += 1
                contador  += 1
            except Exception as e:
                consola(f"{pref}❌ {e}")

        consola(f"  {'─'*62}")

        if guardadas == 0:
            consola(f"\n  ❌ Cap {num_str}: sin imágenes guardadas")
            shutil.rmtree(destino, ignore_errors=True)
            self._ultimo_motivo_fallo = (
                f"las {rechazadas + corrompidas} imágenes candidatas se rechazaron "
                f"por el filtro o quedaron corruptas al guardar — ninguna llegó a disco")
            return False

        extras = []
        if fallidas_desc: extras.append(f"⚠ {fallidas_desc} sin descargar")
        if corrompidas:   extras.append(f"⚠ {corrompidas} corrompidas")
        if rechazadas:    extras.append(f"🚫 {rechazadas} filtradas")
        if self.detector_bloqueo.veces_pausado:
            extras.append(f"🚫 {self.detector_bloqueo.veces_pausado} pausa(s) anti-bloqueo")
        consola(f"\n  ✅ Cap {num_str}: {guardadas} imgs"
                   + ("  |  " + "  ".join(extras) if extras else ""))

        # Registrar el resultado: 'completado' si no hubo nada que se
        # cayera por descarga/corrupción (las filtradas por ruido/perfil
        # no cuentan como "esperadas" — son basura que el sitio mezcla,
        # no páginas reales del capítulo).
        esperadas_reales = len(urls_unicas) - rechazadas
        sospechoso_disparo, sospechoso_mediana = _chequear_paginas_sospechosas(
            carpeta_manga, num, guardadas)
        estado = registrar_capitulo(carpeta_manga, num,
                                    esperadas=max(esperadas_reales, guardadas),
                                    validas=guardadas,
                                    fuente=cap.get("fuente"),
                                    prioridad_fuente=cap.get("prioridad_fuente"),
                                    sospechoso=(sospechoso_mediana if sospechoso_disparo else None))
        if estado == "parcial":
            consola("  ⚠  Registrado como PARCIAL — se reintentará "
                      "lo faltante en el próximo escaneo")

        return True

    def procesar_imagen(self, img_url: str, datos: bytes) -> bytes:
        """
        Hook de post-procesado de bytes ya descargados (antes de calcular
        dimensiones y guardar). Por defecto no hace nada — los scrapers que
        necesiten transformar la imagen (ej: descramble) lo sobreescriben.
        """
        return datos

    # ── Bootstrap de Selenium (compartido) ────────────────────────────
    # Extraído de MadaraScraper para que cualquier scraper que necesite
    # ejecutar JS en un navegador real (ej: LeerCapitulo) lo reuse sin
    # duplicar la detección de Brave, descarga de ChromeDriver, limpieza
    # de perfil stale, etc. El comportamiento es idéntico al que tenía
    # MadaraScraper antes de este refactor.

    @staticmethod
    def _limpiar_perfil_brave_stale() -> None:
        """
        Limpia residuos del perfil dedicado de Selenium (BRAVE_PROFILE_DIR)
        antes de lanzar una nueva sesión.

        Motivo: si una corrida anterior terminó abruptamente (script
        cortado, Brave crasheado, proceso matado a la fuerza) puede
        quedar un proceso brave.exe huérfano y/o archivos de lock
        (SingletonLock/SingletonCookie/SingletonSocket) sosteniendo el
        perfil. Mientras eso exista, CUALQUIER intento de abrir Brave
        con ese mismo --user-data-dir crashea al instante con:
            "session not created: Chrome failed to start: crashed.
             (session not created: DevToolsActivePort file doesn't exist)"

        Solo apunta a procesos cuya línea de comando referencia
        BRAVE_PROFILE_DIR — nunca toca el Brave personal del usuario.
        Es best-effort: cualquier fallo acá se ignora y el flujo normal
        de _crear_driver_selenium sigue su curso.
        """
        # 1) Terminar procesos huérfanos atados a este perfil específico
        try:
            ps_cmd = (
                "Get-CimInstance Win32_Process | "
                f"Where-Object {{ $_.CommandLine -like '*{BRAVE_PROFILE_DIR}*' }} | "
                "Select-Object -ExpandProperty ProcessId"
            )
            resultado = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10
            )
            pids = [p.strip() for p in resultado.stdout.splitlines() if p.strip().isdigit()]
            for pid in pids:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=5
                    )
                    log.info(f"  [Selenium] Proceso huérfano del perfil terminado (PID {pid})")
                except Exception:
                    pass
            if pids:
                # Dar tiempo al SO para liberar el handle del lock antes de reintentar
                time.sleep(1.5)
        except Exception as e:
            log.debug(f"  [Selenium] No se pudo verificar procesos huérfanos: {e}")

        # 2) Borrar archivos de lock residuales del perfil
        for nombre in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = Path(BRAVE_PROFILE_DIR) / nombre
            try:
                if lock_path.exists():
                    lock_path.unlink()
                    log.info(f"  [Selenium] Lock residual eliminado: {nombre}")
            except Exception as e:
                log.debug(f"  [Selenium] No se pudo eliminar {nombre}: {e}")

    def _crear_driver_selenium(self):
        """
        Arma y lanza una instancia de Brave (o Chrome como fallback)
        con el perfil de trabajo dedicado (BRAVE_PROFILE_DIR), que
        persiste entre sesiones — guarda cookies, extensiones, config.
        Descarga el ChromeDriver correcto automáticamente.

        Retorna el driver ya lanzado y con navigator.webdriver ocultado,
        o None si no se pudo lanzar (queda logueado el motivo).
        """
        import zipfile
        import urllib.request

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.chrome.service import Service as ChromeService
        except ImportError:
            log.error("  Instalar: pip install selenium")
            return None

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
        opts.add_argument("--disable-logging")
        opts.add_argument("--log-level=3")
        # Puerto de debug FIJO en vez del efímero (0) que usa ChromeDriver
        # por default — con puerto 0 el lanzamiento crashea sistemáticamente
        # en esta combinación de Brave/Windows (ver notas en README/changelog
        # del scraper); con puerto fijo funciona de forma consistente.
        opts.add_argument("--remote-debugging-port=9515")
        opts.add_argument(f"user-agent={HEADERS_BASE['User-Agent']}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging", "disable-features", "test-type", "allow-pre-commit-input"])
        opts.add_experimental_option("useAutomationExtension", False)

        # Limpieza preventiva del perfil: evita que un lock residual de
        # una corrida anterior (crash, Ctrl+C, kill a la fuerza) haga
        # fallar el lanzamiento antes de siquiera intentarlo.
        self._limpiar_perfil_brave_stale()

        driver = None
        MAX_INTENTOS_LANZAMIENTO = 2
        for intento in range(1, MAX_INTENTOS_LANZAMIENTO + 1):
            try:
                if driver_path:
                    driver = webdriver.Chrome(service=ChromeService(driver_path), options=opts)
                else:
                    driver = webdriver.Chrome(options=opts)
                break  # lanzamiento exitoso, seguir con el resto del método
            except Exception as e:
                driver = None
                lock_residual = "DevToolsActivePort" in str(e)
                if lock_residual and intento < MAX_INTENTOS_LANZAMIENTO:
                    log.warning(
                        f"  [Selenium] Brave no arrancó (perfil bloqueado por una "
                        f"sesión anterior) — limpiando y reintentando "
                        f"({intento}/{MAX_INTENTOS_LANZAMIENTO})..."
                    )
                    self._limpiar_perfil_brave_stale()
                    time.sleep(2)
                    continue
                log.error(f"  [Selenium] Error: {e}")
                return None

        try:
            driver.execute_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
        except Exception:
            pass

        return driver

    def _checkout_driver_selenium(self):
        """
        Reserva un driver de Selenium para uso EXCLUSIVO del hilo que
        llama, y lo deja reservado (lock tomado) hasta que ese mismo
        hilo llame a _devolver_driver_selenium() al terminar.

        Devuelve (driver, es_compartido):

        - es_compartido=True → es el driver cacheado en
          self._driver_selenium_activo, el mismo que se reutiliza
          entre capítulos para no relanzar Brave en cada uno (ver
          _devolver_driver_selenium). Mientras el lock esté tomado,
          NINGÚN otro hilo puede agarrar este mismo driver. Cada
          MAX_NAVEGACIONES_POR_DRIVER usos, se recicla preventivamente
          (se cierra y se lanza uno fresco) en vez de dejarlo crecer
          indefinidamente durante todo el manga.

        - es_compartido=False → el driver compartido ya estaba tomado
          por otro hilo en este instante. Esto pasa en un caso puntual:
          un capítulo anterior quedó "colgado" (superó su timeout) y
          fue abandonado por _descargar_con_timeout, pero su hilo
          daemon sigue vivo en segundo plano todavía usando el driver
          compartido. En vez de esperarlo (bloquearía este capítulo
          también) o compartirlo (dos hilos mandando comandos a la vez
          a la misma sesión de Selenium podrían mezclar imágenes entre
          capítulos), se lanza un driver aparte, exclusivo y
          descartable, solo para este intento.
        """
        if self._driver_selenium_lock.acquire(blocking=False):
            driver = self._driver_selenium_activo
            if driver is not None:
                try:
                    _ = driver.current_url  # ping barato: si murió, tira excepción
                except Exception:
                    log.warning("  [Selenium] El driver reutilizado ya no "
                               "responde — se relanza uno nuevo")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = None
                    self._driver_selenium_activo = None
                    self._navegaciones_driver_activo = 0

            if driver is not None and self._navegaciones_driver_activo >= MAX_NAVEGACIONES_POR_DRIVER:
                # Reciclado preventivo: no esperamos a que se rompa solo,
                # lo cerramos y arrancamos uno fresco cada tantas
                # navegaciones (ver MAX_NAVEGACIONES_POR_DRIVER en config).
                log.info(f"  [Selenium] Driver reciclado tras "
                        f"{self._navegaciones_driver_activo} navegaciones "
                        f"seguidas (preventivo)")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
                self._driver_selenium_activo = None
                self._navegaciones_driver_activo = 0

            if driver is None:
                driver = self._crear_driver_selenium()
                self._driver_selenium_activo = driver

            self._navegaciones_driver_activo += 1
            return driver, True

        log.warning("  [Selenium] El driver compartido está en uso por otro "
                   "capítulo ahora mismo (probablemente uno colgado que sigue "
                   "corriendo en segundo plano) — se lanza uno aparte, "
                   "exclusivo, para este capítulo en vez de compartirlo")
        return self._crear_driver_selenium(), False

    def _devolver_driver_selenium(self, driver, es_compartido: bool, murio: bool = False):
        """
        Contraparte de _checkout_driver_selenium(), SIEMPRE debe
        llamarse (en un finally) después de terminar de usar el driver
        que devolvió, sin importar si terminó bien o mal.

        - es_compartido=True: libera el lock para que el próximo
          capítulo pueda tomarlo. Si murio=True (se rompió durante el
          uso), además se descarta del caché para que el próximo
          capítulo lance uno nuevo en vez de seguir reutilizando uno
          roto.
        - es_compartido=False: era un driver aparte (por contención),
          nunca se cachea — se cierra siempre acá, haya salido bien o mal.
        """
        if es_compartido:
            if murio:
                self._driver_selenium_activo = None
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass
            self._driver_selenium_lock.release()
        else:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def cerrar_driver_selenium(self):
        """
        Cierra el navegador reutilizado, si hay uno abierto y libre.
        Se llama una sola vez al terminar de procesar TODOS los
        capítulos pendientes de un manga (no entre capítulo y
        capítulo) — así no queda Brave abierto de más entre mangas, ni
        se acumulan pestañas/RAM durante una corrida larga con muchos
        mangas en seguimiento.json.

        Si el lock está tomado en este momento (un capítulo colgado
        todavía lo está usando en segundo plano), no se fuerza el
        cierre — se deja que ese hilo abandonado siga su curso solo;
        forzar driver.quit() acá le rompería la sesión por debajo y
        podría dejar basura a medio escribir en disco.
        """
        if not self._driver_selenium_lock.acquire(blocking=False):
            log.debug("  [Selenium] Driver compartido en uso todavía (capítulo "
                     "colgado en segundo plano) — no se fuerza el cierre")
            return
        try:
            driver = self._driver_selenium_activo
            self._driver_selenium_activo = None
            self._navegaciones_driver_activo = 0
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
        finally:
            self._driver_selenium_lock.release()

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
        siempre redirige al dominio real y producía URLs rotas.

        El backend real, confirmado directamente capturando los
        requests del navegador (2026-07-07), es panel.olympusxyz.com
        — antes era dashboard.olympusxyz.com, el sitio cambió el
        subdominio de su API sin previo aviso (el dominio principal
        olympusxyz.com nunca cambió; solo el subdominio del backend).

        Nota: olympus.pages.dev en ese momento redirigía a un dominio
        "olympusbiblioteca.com" que resultó ser una página de aviso
        de "nos mudamos" — NO el backend real. Por eso no conviene
        resolver el dominio vía ese redirect.
        """
        if self._api_base:
            return self._api_base
        dominio = urlparse(self.FALLBACK_BASE).netloc  # olympusxyz.com
        self._api_base = f"https://panel.{dominio}/api"
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

    @staticmethod
    def _normalizar_nombre(texto: str) -> str:
        """
        Normaliza un título para comparación exacta ignorando tildes,
        mayúsculas, comas, dos puntos y demás puntuación — se queda
        solo con letras, números y espacios.
        'La Regresión 100: ¡Del Jugador!' → 'la regresion 100 del jugador'
        """
        if not texto:
            return ""
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = re.sub(r"[^a-zA-Z0-9\s]", "", texto)
        return re.sub(r"\s+", " ", texto).strip().lower()

    def _obtener_catalogo_completo(self) -> list[dict]:
        """
        Trae el catálogo completo de series de Olympus en una sola
        llamada: GET {WEB_BASE}/api/series/list (proxeado por el
        frontend, sin necesitar el subdominio panel.* ni headers de
        firma — solo Accept + Referer).

        Devuelve una lista de {id, name, slug} (851 entradas al
        2026-07-08, comics + novelas). No está paginado.

        Usado como PRIMER intento para recuperar el slug vigente de un
        manga cuando el guardado en seguimiento.json ya no resuelve
        (404, o falla la conexión persistentemente), sin depender de
        que ese manga haya tenido actividad reciente en las páginas de
        novedades — cubre TODO el catálogo, activo o no. Si esto no
        encuentra nada, _buscar_slug_en_novedades() es el fallback
        secundario (más lento, pero cubre el caso de que esta request
        en sí falle o el manga no esté en esta vista por algún motivo).
        """
        url = f"{self.WEB_BASE}/api/series/list"
        headers = {"Accept": "application/json", "Referer": f"{self.WEB_BASE}/"}
        r = hacer_get(url, self.session, headers=headers)
        if not r or r.status_code != 200:
            log.warning(f"  [Olympus] No se pudo obtener el catálogo completo "
                       f"de series (status={r.status_code if r else 'sin respuesta'})")
            return []
        try:
            data = r.json().get("data", [])
        except Exception as e:
            log.warning(f"  [Olympus] Catálogo de series: respuesta no es JSON válido: {e}")
            return []
        return data

    def _buscar_slug_en_catalogo(self, manga_cfg: dict | None,
                                  nombre_exacto: str) -> str | None:
        """
        Recupera el slug vigente de un manga buscando en el catálogo
        completo (/api/series/list), en dos pasos:

          1. Por manga_id (si ya lo teníamos guardado) — comparación
             numérica exacta, sin ambigüedad posible, ya que el ID
             es estable y no cambia aunque el slug sí.
          2. Si no hay manga_id o no aparece, por nombre EXACTO
             normalizado (sin tildes/puntuación/mayúsculas) contra
             el campo 'name' de cada entrada del catálogo. Si hay
             0 o más de 1 coincidencia, no elige nada automático.
        """
        catalogo = self._obtener_catalogo_completo()
        if not catalogo:
            return None

        manga_id = str((manga_cfg or {}).get("manga_id", "")).strip()

        if manga_id:
            for c in catalogo:
                if str(c.get("id", "")) == manga_id:
                    log.info(f"  [Olympus] '{nombre_exacto}' recuperado por "
                             f"manga_id={manga_id} → slug={c.get('slug')}")
                    return c.get("slug")

        objetivo = self._normalizar_nombre(nombre_exacto)
        if not objetivo:
            return None

        coincidencias = [c for c in catalogo
                         if self._normalizar_nombre(c.get("name", "")) == objetivo]

        if len(coincidencias) == 1:
            slug_encontrado = coincidencias[0].get("slug")
            log.info(f"  [Olympus] '{nombre_exacto}' recuperado por nombre "
                     f"exacto → slug={slug_encontrado}")
            return slug_encontrado

        if len(coincidencias) > 1:
            log.warning(f"  [Olympus] '{nombre_exacto}' — "
                       f"{len(coincidencias)} coincidencias exactas por "
                       f"nombre en el catálogo (¿título duplicado?), no se "
                       f"puede elegir automáticamente: "
                       f"{[c.get('slug') for c in coincidencias]}")
        else:
            log.warning(f"  [Olympus] '{nombre_exacto}' — no se encontró "
                       f"ninguna coincidencia exacta en el catálogo completo "
                       f"({len(catalogo)} series revisadas).")

        return None

    def _buscar_slug_en_novedades(self, nombre_exacto: str) -> str | None:
        """
        Fallback SECUNDARIO de recuperación de slug — solo se llama si
        _buscar_slug_en_catalogo() no encontró nada (o el catálogo
        completo en sí falló). Recorre hasta OLYMPUS_MAX_PAGINAS_NOVEDADES
        páginas de /capitulos (últimos capítulos publicados) buscando el
        manga por nombre EXACTO normalizado.

        Más lento que el catálogo (son ~25 requests en vez de 1), pero
        cubre el caso de que el manga no haya aparecido en /api/series/list
        por lo que sea (ej. esa request falló, o el manga no está en esa
        vista por algún motivo). No matchea por manga_id porque esta vista
        no expone el ID numérico del manga, solo su slug.
        """
        objetivo = self._normalizar_nombre(nombre_exacto)
        if not objetivo:
            return None

        novedades = self.obtener_novedades()
        if not novedades:
            return None

        # Un mismo manga aparece varias veces en novedades (una por cada
        # capítulo reciente) — quedarnos con un slug/título por manga.
        vistos_slug = {}
        for n in novedades:
            slug_n = n.get("manga_slug", "")
            if slug_n and slug_n not in vistos_slug:
                vistos_slug[slug_n] = n.get("manga_titulo", "")

        coincidencias = [s for s, titulo in vistos_slug.items()
                         if self._normalizar_nombre(titulo) == objetivo]

        if len(coincidencias) == 1:
            log.info(f"  [Olympus] '{nombre_exacto}' recuperado por nombre "
                     f"exacto en novedades (últimas "
                     f"{self.MAX_PAGINAS} páginas) → slug={coincidencias[0]}")
            return coincidencias[0]

        if len(coincidencias) > 1:
            log.warning(f"  [Olympus] '{nombre_exacto}' — "
                       f"{len(coincidencias)} coincidencias exactas por "
                       f"nombre en novedades, no se puede elegir "
                       f"automáticamente: {coincidencias}")
        else:
            log.warning(f"  [Olympus] '{nombre_exacto}' — no se encontró "
                       f"tampoco en las últimas {self.MAX_PAGINAS} páginas "
                       f"de novedades.")

        return None

    def _recuperar_slug_vencido(self, manga_cfg: dict | None,
                                 nombre_legible: str) -> str | None:
        """
        Intenta recuperar el slug vigente de un manga cuyo slug guardado
        ya no resuelve, en dos pasos (del más rápido/confiable al más
        lento/último recurso):

          1. Catálogo completo (_buscar_slug_en_catalogo): 1 sola
             request, matchea por manga_id (si lo tenemos guardado) o
             por nombre exacto. Cubre TODO el catálogo, activo o no.
          2. Novedades (_buscar_slug_en_novedades): más lento (recorre
             hasta MAX_PAGINAS páginas de /capitulos), pero es una
             segunda red de seguridad para cuando el catálogo no tuvo
             la entrada o esa request en sí falló. Solo matchea por
             nombre (no hay manga_id en esta vista).

        Devuelve None si ninguno de los dos encuentra nada (o hay
        ambigüedad) — ahí hace falta intervención manual.
        """
        slug_nuevo = self._buscar_slug_en_catalogo(manga_cfg, nombre_legible)
        if slug_nuevo:
            return slug_nuevo

        log.info(f"  [Olympus] '{nombre_legible}' — no se encontró por "
                 f"catálogo completo, probando en las últimas "
                 f"{self.MAX_PAGINAS} páginas de novedades...")
        return self._buscar_slug_en_novedades(nombre_legible)

    def obtener_capitulos(self, slug: str, manga_cfg: dict = None,
                          reporte_global: "ReporteEscaneo" = None) -> list[dict]:
        """
        Lista TODOS los capítulos de un manga consultando la API real
        del backend (ver _resolver_dominio() — panel.olympusxyz.com,
        cambió de subdominio el 2026-07-07), no el HTML de la página
        de la serie — esa página es una SPA Nuxt/Vue, el HTML estático
        no contiene la lista de capítulos (solo aparece un link
        "Primer capítulo" sin número, que rompía el parseo anterior).

        Endpoint real (descubierto via Network tab del navegador):
          GET https://panel.olympusxyz.com/api/series/{slug}/chapters
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

        # Señal de "slug vencido": puede ser un 404 explícito (confirmado,
        # fuerte) o un fallo total de conexión (timeout/error de red tras
        # los reintentos de _pedir_pagina). En la práctica ambos pueden
        # significar lo mismo si el manga_id no cambió — antes solo el 404
        # disparaba la recuperación automática, así que un manga cuyo slug
        # fallaba con error de conexión en vez de 404 nunca se intentaba
        # recuperar, aunque tuviéramos el manga_id guardado (caso real:
        # 'Sin fin skills', confirmado con un 404 limpio a mano por fuera
        # del scraper — la corrida automática solo vio el error de conexión).
        slug_404       = (r is not None and r.status_code == 404)
        fallo_conexion = (r is None)

        if slug_404 or fallo_conexion:
            nombre_legible = (manga_cfg.get("nombre_carpeta", slug)
                              if manga_cfg else slug)

            if fallo_conexion:
                log.warning(f"  [Olympus] No se pudo consultar la API para "
                           f"'{slug_usar}' tras varios reintentos (problema "
                           f"de servidor o de red) — probando recuperar el "
                           f"slug vigente por las dudas de que haya vencido...")
            else:
                log.warning(f"  [Olympus] Slug '{slug_usar}' no resuelve "
                           f"(404) — buscando '{nombre_legible}'...")

            slug_nuevo = self._recuperar_slug_vencido(manga_cfg, nombre_legible)
            if slug_nuevo:
                slug_usar = slug_nuevo
                # Persistir el slug recuperado YA, independientemente de si
                # el reintento de abajo tiene éxito — lo encontramos por
                # manga_id o nombre exacto, es confiable. Si no se persiste
                # acá y el reintento inmediato falla por otro hipo de red,
                # el próximo escaneo volvería a arrancar del slug viejo
                # (muerto) en vez de partir ya del recuperado.
                # IMPORTANTE: si el manga tiene 'url_manga' (URL completa
                # pegada a mano), hay que actualizarla TAMBIÉN acá. Si no,
                # normalizar_url_manga() —que corre al principio de CADA
                # escaneo, antes de procesar cualquier manga— va a ver que
                # 'url_manga' (con el slug viejo) no coincide con el
                # 'slug' recién recuperado, y va a PISAR el slug bueno con
                # el viejo (muerto) extraído de la URL desactualizada.
                # Eso forzaría este mismo ciclo de fallo+recuperación en
                # TODOS los escaneos futuros, no solo cuando el slug
                # rote de verdad — hay que mantener los dos campos en
                # sync siempre que se actualice uno de los dos.
                if manga_cfg is not None:
                    manga_cfg["slug"] = f"comic-{slug_usar}"
                    if manga_cfg.get("url_manga"):
                        manga_cfg["url_manga"] = (
                            f"{self.WEB_BASE}/series/comic-{slug_usar}"
                        )
                r = _pedir_pagina(slug_usar, 1)

            if not r or r.status_code != 200:
                # Si la consulta con el slug recuperado (o el original)
                # dio un error de SERVIDOR (no 404), probablemente el slug
                # esté bien y haya sido mala suerte de timing — avisar
                # distinto de "definitivamente hace falta actualizar a
                # mano", para no generar pánico por algo que se resuelve solo.
                if slug_nuevo and r and r.status_code >= 500:
                    log.warning(
                        f"  [Olympus] '{nombre_legible}' — se encontró el "
                        f"slug vigente ({slug_nuevo}, ya persistido) pero "
                        f"el servidor respondió {r.status_code} en el "
                        f"reintento. Probablemente transitorio — se "
                        f"reintentará en el próximo escaneo."
                    )
                    return []

                if fallo_conexion and not slug_nuevo:
                    # No hubo 404 confirmado Y tampoco se pudo recuperar
                    # nada por catálogo ni novedades — puede ser de verdad
                    # solo un problema de red transitorio, sin que el slug
                    # esté vencido. No escalar a "acción manual", solo
                    # reintentar en el próximo escaneo.
                    log.warning(
                        f"  [Olympus] '{nombre_legible}' — no se pudo "
                        f"confirmar si el slug venció (no hubo 404, fue "
                        f"error de conexión) ni recuperarlo por las dudas — "
                        f"se reintentará en el próximo escaneo."
                    )
                    return []

                # Acá sí es un caso confirmado: o hubo 404 explícito, o se
                # encontró un slug nuevo pero ni siquiera ese resolvió bien
                # (raro). En ambos casos, ni catálogo ni novedades dieron
                # una coincidencia utilizable — requiere acción manual.
                log.error(
                    f"  ⚠️  [Olympus] '{nombre_legible}' — el slug guardado "
                    f"venció y no se pudo recuperar solo (ni por manga_id "
                    f"ni por nombre exacto en el catálogo completo, ni en "
                    f"las últimas {self.MAX_PAGINAS} páginas de novedades — "
                    f"revisá el log de arriba: puede ser que no haya "
                    f"coincidencia, o que haya más de una y sea ambiguo). "
                    f"ACCIÓN MANUAL NECESARIA: buscá el manga en "
                    f"https://olympusxyz.com/ y actualizá el campo 'slug' "
                    f"(o agregá 'url_manga' con la URL completa) en "
                    f"seguimiento.json para este manga. Si el título en "
                    f"'nombre_carpeta' no coincide EXACTO (con tildes) con "
                    f"el nombre real del manga en el sitio, corregilo — la "
                    f"recuperación por nombre necesita coincidencia exacta."
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
                # Deduplicar preservando el orden de aparición en el HTML.
                # El HTML es SSR y las <img> ya vienen en el orden REAL de
                # lectura — ese es el orden que hay que respetar y devolver
                # tal cual, SIN reordenar por número de archivo.
                #
                # Antes acá se hacía un .sort() por el número extraído del
                # nombre del archivo ({pagina}_{subpagina}.webp). Se sacó:
                # confirmado con casos reales (cap. largos, 40+ imgs) que
                # ese número no es 1:1 con el orden real de lectura — puede
                # haber resubidas de páginas, lotes mezclados o colisiones
                # de numeración entre subdominios de CDN — y reordenar por
                # ahí terminaba desordenando la segunda mitad del capítulo.
                vistos = set()
                urls_unicas = []
                for u in urls_html:
                    if u not in vistos:
                        vistos.add(u)
                        urls_unicas.append(u)

                # Chequeo pasivo, solo diagnóstico: NO reordena nada. Compara
                # qué orden habría dado el número de archivo (viejo criterio)
                # contra el orden real del HTML. Si difieren, es señal de que
                # ESTE capítulo puntual podría venir con el HTML desordenado
                # (algo que no debería pasar en un sitio SSR, pero mejor
                # tener la alarma que descubrirlo leyendo el manga).
                def _clave_orden(u: str):
                    mo = re.search(r"/(\d+)_(\d+)\.webp", u)
                    if mo:
                        return (int(mo.group(1)), int(mo.group(2)))
                    mo2 = re.search(r"/(\d+)\.webp", u)
                    if mo2:
                        return (int(mo2.group(1)), 0)
                    return (9999, 9999)
                orden_por_numero = sorted(urls_unicas, key=_clave_orden)
                if orden_por_numero != urls_unicas:
                    log.warning(
                        f"  [Olympus] ⚠ cap_id={cap_id}: el orden de aparición "
                        f"en el HTML no coincide con el orden que sugiere el "
                        f"número de archivo. Se está usando el orden del HTML "
                        f"(es el correcto por defecto) — si el capítulo queda "
                        f"desordenado igual, revisar manualmente este cap_id."
                    )

                log.info(f"  [Olympus] {len(urls_unicas)} imágenes extraídas "
                         f"directamente del HTML del capítulo (cap_id={cap_id})")
                return urls_unicas

        log.info("  [Olympus] El HTML del capítulo no trajo imágenes "
                 "reconocibles, probando patrones de CDN como respaldo...")

        # ── Estrategia 2 (respaldo): adivinar el patrón vía CDN ──────
        manga_id = self._manga_id_cache.get(cap_id, "")

        if not manga_id:
            try:
                r = self.session.get(f"{api}/capitulo/{cap_id}", timeout=TIMEOUT,
                                     headers={"Accept": "application/json",
                                              "Origin": self.WEB_BASE,
                                              "Referer": f"{self.WEB_BASE}/"})
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
        # Contador de fallos SEGUIDOS de la estrategia por requests para
        # este manga (ver UMBRAL_ABANDONAR_REQUESTS) — se resetea a 0
        # apenas requests vuelve a funcionar para algún capítulo.
        self._fallos_requests_seguidos = 0

    def obtener_capitulos(self, slug: str) -> list[dict]:
        """
        Obtiene la lista de capítulos de un manga Madara.

        Estrategia en orden de prioridad:
        0. Parsear el bloque JSON embebido (script#mk-chapters-data) que
           usan algunos child themes de Madara (ej: Dragon Translation,
           tema "madara-child-mk") para pintar la lista de capítulos por
           JS del lado del cliente. Cuando existe es la fuente MÁS
           confiable: viene server-side en el HTML inicial, sin depender
           de AJAX ni de ningún nonce.
        1. Parsear capítulos directamente desde el HTML de la página del manga
           (li.wp-manga-chapter a / ul.version-chap li a, etc. — más
           confiable que el AJAX, pero algunos sitios ya no renderizan
           esta lista en el HTML inicial)
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
        soup = BeautifulSoup(r_manga.text, "html.parser")

        # Estrategia 0: JSON embebido (script#mk-chapters-data), si el
        # sitio usa ese child theme
        caps = self._parsear_caps_json(soup, slug)

        # Estrategia 1: parsear HTML directamente (li.wp-manga-chapter, etc.)
        if not caps:
            caps = self._parsear_caps_html(soup, slug)

        # Estrategia 2: AJAX fallback si nada de lo anterior dio resultados
        if not caps:
            caps = self._caps_via_ajax(slug, r_manga)

        if not caps:
            # La página del manga SÍ se encontró (200) pero ninguna de las
            # 3 estrategias devolvió ningún capítulo. Esto es distinto
            # a "el manga está al día" — sugiere que los selectores que
            # usamos ya no matchean nada en el HTML actual del sitio.
            raise SitioRotoError(
                f"La página de '{slug}' respondió 200 pero no se encontró "
                f"NINGÚN capítulo ni por JSON, ni por HTML, ni por AJAX — "
                f"posible cambio de estructura en el sitio (selectores rotos)"
            )

        caps.sort(key=lambda x: x["numero"])
        return caps

    def _parsear_caps_json(self, soup: BeautifulSoup, slug: str) -> list[dict]:
        """
        Extrae capítulos desde el bloque JSON embebido que usan algunos
        child themes de Madara (ej: Dragon Translation, tema
        "madara-child-mk") para renderizar la lista de capítulos por JS
        del lado del cliente (script#mk-chapters-data, con un JS aparte
        tipo mk-chapters.js que solo pinta la página visible). El JSON
        en sí trae TODOS los capítulos server-side, así que no hace
        falta paginar ni tocar AJAX para leerlo — solo parsearlo.

        A veces el sitio tiene capítulos duplicados: mismo número
        ("num") pero "id" y url distintos (ej: capitulo-201 y
        capitulo-201_1 — parece un post repetido por error, no una
        corrección posterior, ya que ambos suelen tener la misma fecha).
        Nos quedamos con el de "id" más alto y logueamos el descarte,
        en vez de dejar que dos entradas con el mismo número lleguen
        más adelante en el pipeline (donde igual se pisarían en
        silencio, ej. en el dict caps_por_num de escanear_manga()).
        """
        script = soup.select_one("script#mk-chapters-data")
        if not script or not script.string:
            return []

        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            return []

        items = data.get("items") or []
        if not items:
            return []

        por_numero: dict = {}
        for it in items:
            num_raw = it.get("num")
            url     = (it.get("url") or "").strip()
            if num_raw is None or not url:
                continue
            try:
                num = float(num_raw)
            except ValueError:
                continue

            id_nuevo   = it.get("id", -1)
            existente  = por_numero.get(num)
            if existente is not None:
                id_existente = existente["_id"]
                log.warning(f"  [{self.nombre}] Capítulo {num} duplicado en "
                           f"el sitio ({existente['url']}  vs  {url}) — se "
                           f"usa el de id más alto "
                           f"({max(id_existente, id_nuevo)})")
                if id_nuevo <= id_existente:
                    continue  # se queda el que ya estaba

            por_numero[num] = {
                "numero": num,
                "url":    url,
                "titulo": (it.get("name") or "").strip() or f"Capitulo {num_raw}",
                "slug":   slug,
                "_id":    id_nuevo,
            }

        caps = list(por_numero.values())
        for c in caps:
            c.pop("_id", None)
        return caps

    def _parsear_caps_html(self, soup: BeautifulSoup, slug: str) -> list[dict]:
        """
        Extrae capítulos desde el HTML de la página del manga.

        Duplicados (mismo número de capítulo, dos <li> distintos):
        mismo criterio que _parsear_caps_json (id más alto gana +
        warning), leyendo `data-chapter-id` del <li> padre — atributo
        confirmado contra HTML real de Tauro (2026-08-18, vía
        Invoke-WebRequest; OJO: NO es `data-id`, es `data-chapter-id`).
        No confirmado si los otros child-themes que caen en este
        selector genérico usan el mismo nombre de atributo, así que si
        no está presente en alguno de los dos duplicados, no hay forma
        confiable de decidir cuál es el correcto — se cae al criterio
        anterior (gana el último encontrado en el HTML), pero ahora
        con warning explícito en vez de resolverse en silencio más
        abajo en escanear_manga().
        """
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

        por_numero: dict = {}
        for a in enlaces:
            href = a.get("href", "").strip()
            if not href or "javascript" in href:
                continue
            texto = a.get_text(" ", strip=True)
            m = re.search(r"(\d+(?:\.\d+)?)", texto)
            if not m:
                continue
            num = float(m.group(1))
            url = href if href.startswith("http") else f"{self.base_url}{href}"

            li_padre = a.find_parent("li")
            id_raw = li_padre.get("data-chapter-id") if li_padre else None
            try:
                id_nuevo = int(id_raw) if id_raw is not None else None
            except (TypeError, ValueError):
                id_nuevo = None

            existente = por_numero.get(num)
            if existente is not None:
                if id_nuevo is not None and existente["_id"] is not None:
                    log.warning(f"  [{self.nombre}] Capítulo {num} duplicado en "
                               f"el HTML ({existente['url']}  vs  {url}) — se "
                               f"usa el de id más alto "
                               f"({max(existente['_id'], id_nuevo)})")
                    if id_nuevo <= existente["_id"]:
                        continue  # se queda el que ya estaba
                else:
                    log.warning(f"  [{self.nombre}] Capítulo {num} duplicado en "
                               f"el HTML ({existente['url']}  vs  {url}) — sin "
                               f"data-chapter-id confiable en alguno de los "
                               f"dos, se usa el último encontrado")

            por_numero[num] = {
                "numero": num,
                "url":    url,
                "titulo": texto,
                "slug":   slug,
                "_id":    id_nuevo,
            }

        caps = list(por_numero.values())
        for c in caps:
            c.pop("_id", None)
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

        Si requests falla UMBRAL_ABANDONAR_REQUESTS veces seguidas
        para este manga, se deja de intentar por requests el resto de
        sus capítulos y se va directo a Selenium (ver la constante
        arriba en la sección de configuración) — para no pagar en cada
        capítulo el costo de un intento que ya sabemos que va a fallar.
        """
        intentar_requests = self._fallos_requests_seguidos < UMBRAL_ABANDONAR_REQUESTS

        if intentar_requests:
            # Estrategia 1: requests normal
            r = hacer_get(cap_url, self.session)
            if r:
                soup = BeautifulSoup(r.text, "html.parser")

                # 1a. Protector AES
                protector = soup.select_one("#chapter-protector-data")
                if protector:
                    urls = self._desencriptar_protector(protector.get_text(strip=True), r.text)
                    if urls:
                        self._fallos_requests_seguidos = 0
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
                    self._fallos_requests_seguidos = 0
                    return self._ordenar_por_numero_archivo(urls)

                # 1c. Detectar redirección JS (form POST a dominio externo)
                form = soup.select_one("form[action]")
                if form:
                    action = form.get("action", "")
                    # Si el form apunta a un dominio diferente al base → Selenium
                    from urllib.parse import urlparse as _up
                    if action and _up(action).netloc != _up(self.base_url).netloc:
                        log.info(f"  [{self.nombre}] Redirección JS detectada → usando Selenium")
                        self._fallos_requests_seguidos = 0
                        return self._obtener_imagenes_selenium(cap_url, esperar_redireccion=True)

            self._fallos_requests_seguidos += 1
            if self._fallos_requests_seguidos == UMBRAL_ABANDONAR_REQUESTS:
                log.info(f"  [{self.nombre}] {UMBRAL_ABANDONAR_REQUESTS} capítulo(s) "
                        f"seguido(s) sin imágenes vía requests — se deja de intentar "
                        f"por requests para el resto de este manga (directo a Selenium)")
            log.info(f"  [{self.nombre}] Sin imágenes via requests → intentando Selenium")
        else:
            log.info(f"  [{self.nombre}] → Selenium directo (requests ya descartado "
                    f"para este manga)")

        # Estrategia 2: Selenium como fallback general (no se espera
        # redirección acá — si hubiera una, ya se habría detectado y
        # manejado en el paso 1c de arriba)
        return self._obtener_imagenes_selenium(cap_url, esperar_redireccion=False)

    def _obtener_imagenes_selenium(self, cap_url: str, esperar_redireccion: bool = True) -> list[str]:
        """
        Lanza el driver vía el bootstrap compartido (ScraperBase.
        _crear_driver_selenium) y ejecuta el flujo específico de Madara:
        esperar redirección JS a dominio externo (ej: Temple → blayvia.com),
        esperar carga del lector, y extraer imágenes por selector + regex.

        esperar_redireccion: si True (default, y siempre que se llame
        desde el paso 1c donde SÍ se detectó un <form> apuntando a otro
        dominio), espera hasta 20s a que cambie la URL. Si False (cuando
        se llama como fallback genérico sin haber detectado ninguna
        redirección), nos salteamos esa espera — no tiene sentido
        esperar 20s a que cambie una URL que nunca va a cambiar. Antes
        esto se esperaba siempre sin importar el motivo, lo que le
        agregaba ~20s muertos a CADA capítulo en sitios como Dragon que
        no redirigen a otro dominio pero igual necesitan Selenium por
        otro motivo (ej: bloqueo/diferenciación anti-bot en requests
        planas).
        """
        import re as _re
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        driver, es_compartido = self._checkout_driver_selenium()
        if not driver:
            if es_compartido:
                self._devolver_driver_selenium(driver, es_compartido)
            return []

        try:
            driver.get(cap_url)
            url_inicial = driver.current_url
            log.info(f"  [Selenium] {url_inicial}")

            # Esperar redirección JS (máx 20 seg) — solo si se espera una
            if esperar_redireccion:
                try:
                    WebDriverWait(driver, 20).until(
                        lambda d: d.current_url != url_inicial and "data:," not in d.current_url
                    )
                except Exception:
                    pass

            # Esperar carga del lector — sondeo con estabilización, no
            # una espera fija ni un simple ">0" (ver historial: la
            # primera versión de este fix usaba WebDriverWait a secas
            # con la condición "aparece al menos 1 elemento", y datos
            # reales del 2026-08-23 mostraron que esa condición se
            # cumplía en ~4 segundos — demasiado rápido para ser el
            # capítulo real, algo suelto satisfacía la condición antes
            # de que el DOM terminara de poblarse, y la extracción
            # seguía cayendo al regex de todos modos). Mismo patrón que
            # ya usa TauroScraper._selenium_tauro: se corta apenas la
            # cantidad de imágenes deja de crecer durante 2 chequeos
            # seguidos, en vez de conformarse con que aparezca una sola.
            _SELECTOR_LECTOR = (
                "img.manga-page-img, .chapter-images img, .reading-content img, "
                "img[src*=WP-manga], img[data-src*=WP-manga], "
                "img[data-lazy-src*=WP-manga], img[data-original*=WP-manga]"
            )
            MAX_ESPERA_SEG    = 15
            ESTABLE_REQUERIDO = 2
            prev_count = -1
            estable    = 0
            inicio     = time.time()
            while time.time() - inicio < MAX_ESPERA_SEG:
                count = len(driver.find_elements(By.CSS_SELECTOR, _SELECTOR_LECTOR))
                if count > 0 and count == prev_count:
                    estable += 1
                    if estable >= ESTABLE_REQUERIDO:
                        break
                else:
                    estable = 0
                prev_count = count
                time.sleep(0.5)

            page_src = driver.page_source
            urls     = []
            vistos   = set()

            # 1. Selector directo manga-page-img — se incluyen las 4
            # variantes de atributo lazy-load (src/data-src/data-lazy-src/
            # data-original) directamente en el selector CSS, no solo en el
            # loop de abajo, para que Selenium las encuentre en UNA sola
            # pasada por el DOM real. Antes esto se dividía en dos pasadas
            # separadas (selector acá + regex sobre el HTML crudo más abajo)
            # y lo que solo aparecía en la segunda pasada perdía su posición
            # real y quedaba pegado al final de la lista (bug de orden,
            # auditoría 1.2) — unificar en una sola pasada por DOM lo evita
            # de raíz en vez de parchear la posición después.
            elementos_lector = driver.find_elements(By.CSS_SELECTOR, _SELECTOR_LECTOR)
            for img in elementos_lector:
                for attr in ["src", "data-src", "data-lazy-src", "data-original"]:
                    src = (img.get_attribute(attr) or "").strip()
                    if src and src not in vistos and src.startswith("http") and self._es_imagen_valida(src):
                        urls.append(src)
                        vistos.add(src)

            # ¿Existe el contenedor del lector aunque sea sin <img> con src
            # válido adentro? (ej: contenedor presente pero todo lazy-load
            # con atributos distintos a los que buscamos). Si existe, NO es
            # una página de error/redirect — es el reader real, aunque haya
            # fallado la extracción por algún motivo puntual. Esta señal es
            # más confiable que el conteo de imágenes para no confundir un
            # capítulo corto/especial real con una página caída.
            contenedor_lector_presente = bool(elementos_lector) or bool(
                driver.find_elements(By.CSS_SELECTOR,
                    ".reading-content, .chapter-images, .wp-manga-chapter-img"
                )
            )

            # 2. Regex para WP-manga — con el selector de arriba ya
            # ampliado, esto solo debería agregar algo si la URL está
            # embebida fuera de un atributo de <img> (ej: dentro de un
            # <script>, JSON inline, o estilo CSS de background-image),
            # un caso genuinamente raro. Como ya no es "otra pasada
            # equivalente" sino un último recurso, lo que agregue acá
            # se avisa por log — la posición real de esa imagen en el
            # capítulo no está garantizada al no venir de un elemento
            # del DOM en orden.
            antes_del_regex = len(urls)
            for u in _re.findall(r"https?://[^\s<>]+/WP-manga/data/[^\s<>]+", page_src, _re.IGNORECASE):
                u = u.strip(".,;)'\"")
                if u not in vistos:
                    urls.append(u)
                    vistos.add(u)
            if len(urls) > antes_del_regex:
                log.warning(f"  [Selenium] {len(urls) - antes_del_regex} imagen(es) "
                           f"encontradas solo por regex sobre el HTML crudo (no estaban "
                           f"en ningún atributo de <img> del DOM) — su posición en el "
                           f"capítulo no está garantizada salvo que _ordenar_por_numero_"
                           f"archivo() pueda reordenar por nombre de archivo puro.")

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
                elif contenedor_lector_presente:
                    # El contenedor del lector SÍ está en el DOM — es la
                    # página real del capítulo, solo que corto/atípico o
                    # con markup levemente distinto. No se descarta.
                    log.warning(f"  [Selenium] {len(urls)} imagen(es) vía fallback "
                               f"genérico, pero el contenedor del lector SÍ está "
                               f"presente en el DOM — se asume capítulo real "
                               f"(posiblemente corto o especial), no página de error. "
                               f"HTML volcado en {debug_path} para revisar igual.")
                elif len(urls) <= UMBRAL_FALLBACK_SOSPECHOSO:
                    # Ni rastro del contenedor del lector en el DOM Y pocas
                    # imágenes encontradas solo por el fallback genérico:
                    # con altísima probabilidad es una página de error
                    # (404, "cerrar", banner decorativo) y NO el
                    # capítulo real. Se descarta por completo en vez
                    # de dejarlo pasar — que quede marcado como error
                    # y se reintente en el próximo escaneo, en vez de
                    # guardarse en disco como si fuera un capítulo
                    # válido de 1-2 páginas.
                    log.warning(f"  [Selenium] {len(urls)} imagen(es) encontradas "
                               f"SOLO por el fallback genérico (sin contenedor del "
                               f"lector en el DOM) y por debajo del umbral "
                               f"({UMBRAL_FALLBACK_SOSPECHOSO}) — se descarta como "
                               f"página de error, no como capítulo real. "
                               f"HTML volcado en {debug_path} para revisar.")
                    return []
                else:
                    log.warning(f"  [Selenium] {len(urls)} imagen(es) encontradas "
                               f"SOLO por el fallback genérico (sin contenedor del "
                               f"lector en el DOM) — sospechoso, pero la cantidad "
                               f"supera el umbral así que se deja pasar como capítulo "
                               f"real con lector no estándar. "
                               f"HTML volcado en {debug_path} para revisar.")

            log.info(f"  [Selenium] {len(urls)} imágenes — {driver.current_url}")
            return self._ordenar_por_numero_archivo(urls)

        except Exception as e:
            log.error(f"  [Selenium] Error: {e}")
            return []

        finally:
            # Se ejecuta para CUALQUIER salida de esta función (el
            # descarte anticipado de arriba, el retorno exitoso, o la
            # excepción de este mismo bloque) — siempre hay que
            # devolver el driver una sola vez.
            murio = False
            try:
                _ = driver.current_url
            except Exception:
                murio = True
            self._devolver_driver_selenium(driver, es_compartido, murio=murio)

    def _ordenar_por_numero_archivo(self, urls: list[str]) -> list[str]:
        """
        Red de seguridad extra sobre el orden de las páginas: por defecto
        confiamos en el orden en que las imágenes aparecen en el DOM
        (es como se guardan después, 001.jpg/002.jpg/... según esa
        posición). Eso es correcto mientras el DOM esté en el mismo
        orden de lectura — que es el caso normal — pero no lo
        verificamos contra nada más.

        Acá agregamos una verificación barata: si el nombre de archivo
        de TODAS las imágenes termina en un número reconocible —
        puramente numérico (ej: ".../capitulo-211/017.jpg" → 17) o con
        el patrón "Nombre-(N).ext" que usa Temple (ej:
        "Unico-(1).webp" → 1, típico de WordPress cuando auto-numera
        archivos para evitar colisión de nombres) — y esos números son
        todos distintos, reordenamos por ese número en vez de por la
        posición en el DOM. Si el sitio en algún momento entrega el
        HTML con las imágenes desordenadas (pasó en otros sitios
        Madara), esto lo corrige solo en vez de guardar las páginas
        mezcladas.

        Si algún nombre no matchea NINGUNO de los dos patrones (nombres
        con hash, CDN externo, etc.) no se toca nada — se devuelve tal
        cual vino, para no arriesgar romper sitios donde el nombre de
        archivo no es un número de página.
        """
        if len(urls) < 2:
            return urls

        pares = []
        for u in urls:
            nombre = u.split("/")[-1].split("?")[0]
            m = re.match(r"^0*(\d+)\.\w+$", nombre) or re.search(r"\((\d+)\)\.\w+$", nombre)
            if not m:
                return urls  # nombre no numérico → no tocar el orden
            pares.append((int(m.group(1)), u))

        numeros = [n for n, _ in pares]
        if len(set(numeros)) != len(numeros):
            return urls  # números repetidos → ambiguo, no tocar el orden

        pares.sort(key=lambda p: p[0])
        return [u for _, u in pares]

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
# §10-B  TAURO SCAN
# ══════════════════════════════════════════════════════════════════════

class TauroScraper(MadaraScraper):
    """
    Tauro Scan — WordPress/Madara. URL fija.

    Particularidad del sitio: tiene capítulos "programados" (bloqueados
    para no-VIP hasta una fecha de liberación gratuita) marcados en el
    HTML con la clase `scheduled` en el <li> (y `editor-access` si el
    usuario ya lo desbloqueó). No hay evidencia real todavía de cómo
    responde el servidor al pedir la URL de un capítulo bloqueado
    directamente (puede que la herencia de MadaraScraper.obtener_imagenes
    ya lo resuelva solo -> 0 imágenes -> queda como error -> se reintenta
    en el próximo escaneo, igual que un capítulo caído). Si en la
    práctica no fuera así (ej. trae imágenes de un aviso "hazte VIP" en
    vez de las reales), hay que agregar acá un chequeo explícito de la
    clase `scheduled` antes de intentar bajarlo.
    """

    def __init__(self):
        super().__init__("https://lectortaurus.com")

    @property
    def nombre(self) -> str:
        return "Tauro Scan"

    def _parsear_caps_html(self, soup: BeautifulSoup, slug: str) -> list[dict]:
        """
        Override del parseo genérico de MadaraScraper.

        En Tauro cada <li class="wp-manga-chapter"> trae TRES <a>:
        el título ("Capítulo 106"), la fecha de publicación
        ("hace 1 día" / "03/07/2026") y el contador de vistas ("1814").
        El selector genérico `li.wp-manga-chapter a` de la clase base
        los agarra los tres, y el regex de número le pega al primer
        dígito que encuentra en CUALQUIERA de los tres textos (ej. el
        "1" de "hace 1 día" termina poniendo numero=1 a un capítulo
        que en realidad es el 106). Acá tomamos únicamente el <a>
        directamente dentro de div.parm-extras (el título), ignorando
        los que están anidados en div.parm-extras2 (fecha/likes/vistas).

        Duplicados: mismo criterio que MadaraScraper._parsear_caps_html
        (id más alto gana + warning), leyendo `data-chapter-id` del
        propio <li> — confirmado con HTML real de Tauro (2026-08-18).
        """
        por_numero: dict = {}
        for li in soup.select("li.wp-manga-chapter"):
            a = li.select_one("div.parm-extras > a")
            if not a:
                continue
            href = a.get("href", "").strip()
            if not href or "javascript" in href:
                continue
            texto = a.get_text(" ", strip=True)
            m = re.search(r"(\d+(?:\.\d+)?)", texto)
            if not m:
                continue
            num = float(m.group(1))
            url = href if href.startswith("http") else f"{self.base_url}{href}"

            id_raw = li.get("data-chapter-id")
            try:
                id_nuevo = int(id_raw) if id_raw is not None else None
            except (TypeError, ValueError):
                id_nuevo = None

            existente = por_numero.get(num)
            if existente is not None:
                if id_nuevo is not None and existente["_id"] is not None:
                    log.warning(f"  [{self.nombre}] Capítulo {num} duplicado en "
                               f"el HTML ({existente['url']}  vs  {url}) — se "
                               f"usa el de id más alto "
                               f"({max(existente['_id'], id_nuevo)})")
                    if id_nuevo <= existente["_id"]:
                        continue  # se queda el que ya estaba
                else:
                    log.warning(f"  [{self.nombre}] Capítulo {num} duplicado en "
                               f"el HTML ({existente['url']}  vs  {url}) — sin "
                               f"data-chapter-id confiable en alguno de los "
                               f"dos, se usa el último encontrado")

            por_numero[num] = {
                "numero": num,
                "url":    url,
                "titulo": texto,
                "slug":   slug,
                "_id":    id_nuevo,
            }

        caps = list(por_numero.values())
        for c in caps:
            c.pop("_id", None)
        return caps

    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        """
        Confirmado contra HTML real (cap 1: 34/34 imágenes, orden
        correcto 001__001, 001__002... según registro_progreso.json):
        el lector de Tauro SÍ viene resuelto por SSR, como <img
        class="wp-manga-chapter-img"> dentro de <div class="page-break">
        — exactamente el mismo patrón que ya cubre la Estrategia 1b de
        MadaraScraper. En ese HTML de prueba las 34 apariciones de
        /WP-manga/data/ en TODA la página eran esas 34 <img> del lector
        (ninguna miniatura/sugerido colado), pero en vez de confiar en
        que eso se mantenga así para siempre, acá se selecciona
        directo por los <img> reales (scoped), no por regex a ciegas
        sobre el texto completo de la página — así no importa si algún
        otro manga de este sitio tiene una carátula o carrusel de
        sugeridos usando el mismo path /WP-manga/data/ en otro lado del
        HTML, nunca se va a colar.
        """
        r = hacer_get(cap_url, self.session)
        if r:
            soup = BeautifulSoup(r.text, "html.parser")
            urls, vistos = [], set()
            for img in soup.select("div.page-break img, .wp-manga-chapter-img"):
                src = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
                src = src.strip()
                if src and src not in vistos and src.startswith("http") and self._es_imagen_valida(src):
                    urls.append(src)
                    vistos.add(src)
            if urls:
                log.info(f"  [{self.nombre}] {len(urls)} imágenes vía requests (SSR)")
                return self._ordenar_por_numero_archivo(urls)

        # Selenium propio (sondeo adaptativo — ver _selenium_tauro) antes
        # de caer al flujo genérico de la clase base, que sí sirve como
        # último recurso para casos raros que el camino rápido de Tauro
        # no contempla (protector AES, redirección JS, etc.)
        log.info(f"  [{self.nombre}] Sin imágenes vía requests → Selenium")
        urls = self._selenium_tauro(cap_url)
        if urls:
            return urls

        return super().obtener_imagenes(cap_url, slug, numero)

    def _selenium_tauro(self, cap_url: str) -> list[str]:
        """
        Fallback de Selenium propio de Tauro, con sondeo de
        estabilización en vez de esperas fijas.

        Nota de diseño: este método NO se llama `_obtener_imagenes_selenium`
        (el nombre que usa MadaraScraper) a propósito. Si lo tuviera, al
        pisar el método de la clase base con una firma distinta —la base
        acepta `esperar_redireccion`, este no—, cualquier llamada que
        llegue desde `super().obtener_imagenes()` (que sigue resolviendo
        `self._obtener_imagenes_selenium(...)` contra ESTA clase por el
        polimorfismo normal de Python) reventaría con TypeError por el
        argumento que este método no acepta. Con nombre propio esa
        colisión no existe: la clase base usa su propio método sin
        problema si hace falta como último recurso, y este método
        optimizado de Tauro se prueba primero, antes de llegar ahí.
        """
        from selenium.webdriver.common.by import By

        driver = self._crear_driver_selenium()
        if not driver:
            return []

        try:
            driver.get(cap_url)
            log.info(f"  [Selenium] {driver.current_url}")

            # Sondeo de estabilización: se corta apenas la cantidad de
            # imágenes del lector deja de crecer durante 2 chequeos
            # seguidos, en vez de esperar un tiempo fijo a ciegas.
            MAX_ESPERA_SEG    = 15
            ESTABLE_REQUERIDO = 2
            prev_count = -1
            estable    = 0
            inicio     = time.time()
            while time.time() - inicio < MAX_ESPERA_SEG:
                count = len(driver.find_elements(By.CSS_SELECTOR,
                    "img.manga-page-img, .chapter-images img, .reading-content img, "
                    "img[src*=WP-manga], img[data-src*=WP-manga], "
                    "img[data-lazy-src*=WP-manga], img[data-original*=WP-manga]"
                ))
                if count > 0 and count == prev_count:
                    estable += 1
                    if estable >= ESTABLE_REQUERIDO:
                        break
                else:
                    estable = 0
                prev_count = count
                time.sleep(0.5)

            page_src = driver.page_source
            urls, vistos = [], set()

            # Selector ampliado con las 4 variantes de atributo lazy-load
            # en una sola pasada por DOM — ver comentario largo en
            # MadaraScraper._obtener_imagenes_selenium (mismo fix, mismo
            # motivo: auditoría 1.2).
            elementos_lector = driver.find_elements(By.CSS_SELECTOR,
                "img.manga-page-img, .chapter-images img, .reading-content img, "
                "img[src*=WP-manga], img[data-src*=WP-manga], "
                "img[data-lazy-src*=WP-manga], img[data-original*=WP-manga]"
            )
            for img in elementos_lector:
                for attr in ["src", "data-src", "data-lazy-src", "data-original"]:
                    src = (img.get_attribute(attr) or "").strip()
                    if src and src not in vistos and src.startswith("http") and self._es_imagen_valida(src):
                        urls.append(src)
                        vistos.add(src)

            contenedor_lector_presente = bool(elementos_lector) or bool(
                driver.find_elements(By.CSS_SELECTOR,
                    ".reading-content, .chapter-images, .wp-manga-chapter-img"
                )
            )

            # Último recurso — ver comentario en MadaraScraper para el
            # porqué de avisar acá ahora que el selector de arriba ya
            # cubre casi todos los casos que esto solía capturar.
            antes_del_regex = len(urls)
            for u in re.findall(r"https?://[^\s<>]+/WP-manga/data/[^\s<>]+", page_src, re.IGNORECASE):
                u = u.strip(".,;)'\"")
                if u not in vistos:
                    urls.append(u)
                    vistos.add(u)
            if len(urls) > antes_del_regex:
                log.warning(f"  [{self.nombre}] {len(urls) - antes_del_regex} imagen(es) "
                           f"encontradas solo por regex sobre el HTML crudo (no estaban "
                           f"en ningún atributo de <img> del DOM) — su posición en el "
                           f"capítulo no está garantizada salvo que _ordenar_por_numero_"
                           f"archivo() pueda reordenar por nombre de archivo puro.")

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
                debug_path = DEBUG_SELENIUM_PATH
                debug_path.write_text(page_src, encoding="utf-8")
                if not urls:
                    log.warning(f"  [Selenium] Sin imágenes. HTML en {debug_path}")
                elif contenedor_lector_presente:
                    log.warning(f"  [Selenium] {len(urls)} imagen(es) vía fallback "
                               f"genérico, pero el contenedor del lector SÍ está "
                               f"presente en el DOM — se asume capítulo real. "
                               f"HTML volcado en {debug_path} para revisar igual.")
                elif len(urls) <= UMBRAL_FALLBACK_SOSPECHOSO:
                    log.warning(f"  [Selenium] {len(urls)} imagen(es) encontradas "
                               f"SOLO por el fallback genérico (sin contenedor del "
                               f"lector en el DOM) y por debajo del umbral "
                               f"({UMBRAL_FALLBACK_SOSPECHOSO}) — se descarta como "
                               f"página de error, no como capítulo real. "
                               f"HTML volcado en {debug_path} para revisar.")
                    return []
                else:
                    log.warning(f"  [Selenium] {len(urls)} imagen(es) encontradas "
                               f"SOLO por el fallback genérico (sin contenedor del "
                               f"lector en el DOM) — sospechoso, pero la cantidad "
                               f"supera el umbral así que se deja pasar. "
                               f"HTML volcado en {debug_path} para revisar.")

            log.info(f"  [Selenium] {len(urls)} imágenes — {driver.current_url}")
            return self._ordenar_por_numero_archivo(urls)

        except Exception as e:
            log.error(f"  [Selenium] Error: {e}")
            return []
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

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

        Si el mismo número de capítulo aparece más de una vez (dos
        páginas de listado que se solapan, o el mismo listado
        procesado dos veces) y apuntan a URLs distintas, se queda con
        el primero encontrado y avisa por log — mismo criterio de
        "avisar cuando pasa algo raro, no resolverlo en silencio" que
        ya usan Madara y Nexus para sus propios duplicados.
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
            url_cap = urljoin(f"https://{dominio_base}/", href)
            if num in caps:
                if caps[num]["url"] != url_cap:
                    log.warning(f"  [{self.nombre}] Capítulo {num} duplicado en el "
                               f"listado ({caps[num]['url']}  vs  {url_cap}) — se usa "
                               f"el primero encontrado")
                continue
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
        resultado = [u for u in candidatas if u.startswith(prefijo)]

        # Chequeo pasivo de orden — mismo patrón defensivo que ya usa
        # Olympus: NUNCA reordena nada, se sigue confiando en el orden
        # real del DOM (SSR, igual que Olympus). Es solo una alarma para
        # el log. No tenemos un capítulo real de Ikigai a mano para
        # confirmar el patrón exacto de nombre de archivo después de
        # "series/{id}/{id}/", así que la extracción es deliberadamente
        # conservadora: si no puede sacar un número de página de CADA
        # imagen (o si hay números repetidos), no dice nada — no se
        # asume ningún patrón que no esté confirmado.
        def _num_pagina_ikigai(u: str):
            nombre = u.split("/")[-1].split("?")[0]
            m = re.search(r"(\d+)\.\w+$", nombre)
            return int(m.group(1)) if m else None

        claves = [_num_pagina_ikigai(u) for u in resultado]
        if all(c is not None for c in claves) and len(set(claves)) == len(claves):
            orden_por_nombre = [u for _, u in sorted(zip(claves, resultado))]
            if orden_por_nombre != resultado:
                log.warning(f"  [Ikigai] Cap {numero}: el orden de aparición en el DOM "
                           f"no coincide con el orden que sugiere el número al final "
                           f"del nombre de archivo. Se sigue usando el orden del DOM "
                           f"(correcto por defecto, es SSR) — si el capítulo queda "
                           f"desordenado en disco, revisar manualmente {cap_url}")

        return resultado


# ══════════════════════════════════════════════════════════════════════
# §11d  LEERCAPITULO
# ══════════════════════════════════════════════════════════════════════

class LeerCapituloScraper(ScraperBase):
    """
    LeerCapitulo (leercapitulo.co).

    Plataforma propia (NO Madara/WordPress — sin rastro de wp-manga en
    el HTML). Lista de capítulos: HTML directo, sin AJAX. Imágenes: el
    contenido real viaja en un blob ofuscado (#array_data, ~62 símbolos
    de charset, N segmentos = N páginas del capítulo) que se decodifica
    client-side por JS pesadamente ofuscado (usa el Deobfuscator de
    "synchrony", visto en el APK de Mihon/Tachiyomi de este sitio). En
    vez de reimplementar ese algoritmo, se deja que un navegador real
    (Selenium) ejecute el JS y se lee el resultado ya renderizado en
    el DOM (`.comic_wraCon img`) — igual de robusto y mucho menos
    frágil que reversear un cifrado que puede cambiar sin aviso.

    slug: se guarda el path completo tal cual aparece en la URL,
    "{id}/{slug-largo}" (ej: "7mbadjh023/pensaste-que-podrias-..."),
    igual que Olympus combina ID + slug. No hace falta separarlos en
    dos campos — se usa directo para armar tanto la URL del manga
    como la de cada capítulo.
    """

    BASE_URL = "https://www.leercapitulo.co"

    def __init__(self):
        super().__init__()
        self.session.headers.update({"Referer": self.BASE_URL + "/"})

    @property
    def nombre(self) -> str:
        return "LeerCapitulo"

    def obtener_capitulos(self, slug: str) -> list[dict]:
        """
        Parsea la lista de capítulos directo del HTML de la página del
        manga — confirmado que viene completa ahí (selector
        `.chapter-list a.xanh`), sin necesidad de AJAX.
        """
        url = f"{self.BASE_URL}/manga/{slug}/"
        r = hacer_get(url, self.session)
        if not r:
            raise SitioRotoError(
                f"No se pudo obtener la página del manga '{slug}' en "
                f"LeerCapitulo — el manga pudo haber sido removido o "
                f"la URL/slug guardado en seguimiento.json cambió"
            )

        soup = BeautifulSoup(r.text, "html.parser")
        enlaces = soup.select(".chapter-list a.xanh")
        if not enlaces:
            raise SitioRotoError(
                f"La página de '{slug}' respondió 200 pero no se encontró "
                f"NINGÚN capítulo con el selector '.chapter-list a.xanh' — "
                f"posible cambio de estructura en el sitio"
            )

        caps = []
        for a in enlaces:
            href = (a.get("href") or "").strip()
            if not href:
                continue
            texto = a.get_text(" ", strip=True) or a.get("title", "")
            m = re.search(r"(\d+(?:\.\d+)?)", texto)
            if not m:
                continue
            num = float(m.group(1))
            caps.append({
                "numero": num,
                "url":    href if href.startswith("http") else f"{self.BASE_URL}{href}",
                "titulo": texto,
                "slug":   slug,
            })

        caps.sort(key=lambda x: x["numero"])
        return caps

    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        """
        Las imágenes SIEMPRE están detrás del JS ofuscado (#array_data
        nunca aparece resuelto en el HTML crudo) — no tiene sentido
        intentar primero con requests como en Madara, se va directo a
        Selenium.

        El sitio tiene dos modos de lectura (dropdown `.loadImgType`):
        "Uno por uno" (default — solo renderiza la página actual en
        .comic_wraCon, el resto espera que el usuario haga clic en
        "Próximo") y "Todo en uno" (renderiza TODAS las páginas de
        una). Sin forzar el segundo modo, solo se ve 1 imagen por
        capítulo sea cual sea su cantidad real de páginas.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait, Select

        driver = self._crear_driver_selenium()
        if not driver:
            return []

        try:
            driver.get(cap_url)

            # ── Forzar modo "Todo en uno" ──────────────────────────
            # Sin esto el reader queda en modo paginado y solo se ve
            # la página 1 del capítulo.
            try:
                WebDriverWait(driver, 10).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR, "select.loadImgType")
                )
                select_el = driver.find_element(By.CSS_SELECTOR, "select.loadImgType")
                Select(select_el).select_by_value("1")
                log.info("  [LeerCapitulo] Modo 'Todo en uno' activado")
            except Exception as e:
                log.warning(f"  [LeerCapitulo] No se pudo forzar modo 'Todo en "
                           f"uno' (¿cambió el selector?): {e} — puede que solo "
                           f"se recupere 1 página")

            # ── Esperar a que .comic_wraCon termine de llenarse ────
            # No sabemos de antemano cuántas páginas tiene el capítulo,
            # así que se sondea la cantidad de <img> cada 1s y se corta
            # cuando se mantiene estable 2 veces seguidas (o al llegar
            # al tope de espera).
            MAX_ESPERA_SEG    = 25
            ESTABLE_REQUERIDO = 2
            prev_count = -1
            estable    = 0
            inicio     = time.time()
            while time.time() - inicio < MAX_ESPERA_SEG:
                count = len(driver.find_elements(By.CSS_SELECTOR, ".comic_wraCon img"))
                if count > 0 and count == prev_count:
                    estable += 1
                    if estable >= ESTABLE_REQUERIDO:
                        break
                else:
                    estable = 0
                prev_count = count
                time.sleep(1)

            urls   = []
            vistos = set()
            for img in driver.find_elements(By.CSS_SELECTOR, ".comic_wraCon img"):
                for attr in ["src", "data-src", "data-lazy-src"]:
                    src = (img.get_attribute(attr) or "").strip()
                    if src and src not in vistos and src.startswith("http"):
                        urls.append(src)
                        vistos.add(src)

            if not urls:
                # Fallback genérico + volcado de debug, igual que Madara,
                # por si .comic_wraCon cambia de nombre/clase en el sitio.
                for img in driver.find_elements(By.CSS_SELECTOR, "img"):
                    for attr in ["src", "data-src", "data-lazy-src"]:
                        src = (img.get_attribute(attr) or "").strip()
                        if (src and src not in vistos and src.startswith("http")
                                and any(src.lower().split("?")[0].endswith(ext)
                                        for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"])):
                            urls.append(src)
                            vistos.add(src)
                debug_path = DEBUG_SELENIUM_PATH
                debug_path.write_text(driver.page_source, encoding="utf-8")
                if urls:
                    log.warning(f"  [LeerCapitulo] {len(urls)} imagen(es) vía fallback "
                               f"genérico (.comic_wraCon vacío) — HTML volcado en "
                               f"{debug_path} para revisar.")
                else:
                    log.warning(f"  [LeerCapitulo] Sin imágenes. HTML en {debug_path}")

            log.info(f"  [LeerCapitulo] {len(urls)} imágenes — {driver.current_url}")
            return urls

        except Exception as e:
            log.error(f"  [LeerCapitulo] Error: {e}")
            return []
        finally:
            try:
                driver.quit()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
# §11e  TMO (ZonaTMO)
# ══════════════════════════════════════════════════════════════════════

class TmoScraper(ScraperBase):
    """
    ZonaTMO (zonatmo.org).

    Plataforma propia (Laravel — nada de wp-manga/Madara). Todo
    server-rendered, sin AJAX ni JS necesario para nada:

    - La lista de capítulos viene COMPLETA en el HTML de la página del
      manga (`/library/manga/{slug}`), sin paginar. Confirmado con HTML
      real (tmo_manga.html, ago-2026): cada capítulo es un
      `<li class="upload-link" data-chapter-number="N">`, y dentro de
      él puede haber 1 o más "versiones" (mismo capítulo subido por
      distintos grupos de scanlation).
    - Las imágenes del capítulo vienen directo en el HTML de
      `/view_uploads/{id}` (`<img class="reader-image" src="...">`),
      en `https://storage.zonatmo.org/chapters/{id}/{n}.webp`, sin
      lazy-load (el src ya es la URL final). Confirmado con PowerShell
      que el CDN no exige Referer ni sesión — 200 en frío.

    Parsing defensivo por diseño: en vez de depender de las clases
    Bootstrap del bloque de cada versión (son utilitarias, cambian
    fácil con cualquier retoque de estilo), se ancla en dos cosas
    semánticas que es mucho menos probable que cambien: el propio link
    "Leer online" (href contiene "/view_uploads/") y el link al grupo
    (href contiene "/groups/"). La extracción de imágenes tampoco
    depende de la clase CSS del <img> — matchea directo el patrón de
    URL del CDN sobre el HTML crudo.

    Selección de versión (cuando hay 2+): usa 'grupo_preferido' de
    seguimiento.json si está seteado y matchea alguna versión de ESE
    capítulo puntual (substring case-insensitive contra el nombre del
    grupo). Si no está seteado, o no matchea para ese capítulo, cae a
    la versión MÁS ANTIGUA subida (fecha absoluta que trae cada
    versión, con fallback a comparar por ID de view_uploads si por
    algún motivo la fecha no parsea — en la práctica el ID sube en el
    mismo orden que la fecha).

    slug: se guarda "{manga_id}/{nombre-slug}" tal cual aparece en la
    URL (ej: "30593/tensei-shitara-slime-datta-ken"), igual criterio
    que Olympus/LeerCapitulo.
    """

    BASE_URL = "https://zonatmo.org"

    def __init__(self):
        super().__init__()
        self.session.headers.update({"Referer": self.BASE_URL + "/"})

    @property
    def nombre(self) -> str:
        return "ZonaTMO"

    def obtener_capitulos(self, slug: str, grupo_preferido: str = None,
                           url_manga: str = None) -> list[dict]:
        """
        grupo_preferido y url_manga son opcionales (no forman parte de
        la firma abstracta de ScraperBase) — se los pasa a mano solo
        para la fuente 'tmo' desde escanear_manga(), leyéndolos de
        manga_cfg['grupo_preferido'] y manga_cfg['url_manga']. El resto
        de las fuentes no se ve afectado.

        url_manga importa acá en particular (2026-08-23, bug real
        encontrado): ZonaTMO categoriza el contenido por tipo en la
        URL — /library/manga/, /library/manhwa/, /library/manhua/,
        /library/novela/... — y antes esto se armaba a mano asumiendo
        SIEMPRE "manga", lo que daba 404 en cualquier manhwa/manhua/
        novela (ej. "no-le-digas-a-tu-mama" es manhwa, no manga). No
        hay forma de reconstruir el tipo correcto solo a partir del
        slug — si viene url_manga, se usa tal cual (ya tiene el tipo
        correcto, es la misma URL que ve un humano en el navegador).
        Si no viene (config vieja sin ese campo), cae al armado viejo
        asumiendo "manga" — funciona para ese tipo, sigue rompiendo
        para el resto, pero no le cambia el comportamiento a nadie que
        ya tuviera solo "manga" funcionando.
        """
        if url_manga:
            url = url_manga.strip()
        else:
            url = f"{self.BASE_URL}/library/manga/{slug.strip('/')}"
        r = hacer_get(url, self.session)
        if not r:
            raise SitioRotoError(
                f"No se pudo obtener la página del manga '{slug}' en "
                f"ZonaTMO — el manga pudo haber sido removido, el ID "
                f"cambió, o el sitio está caído/migró de dominio otra vez"
            )

        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("li.upload-link[data-chapter-number]")
        if not items:
            raise SitioRotoError(
                f"La página de '{slug}' respondió 200 pero no se encontró "
                f"NINGÚN capítulo con el selector "
                f"'li.upload-link[data-chapter-number]' — posible cambio "
                f"de estructura en el sitio"
            )

        grupo_buscado = (grupo_preferido or "").strip().lower()
        hubo_grupo_configurado = bool(grupo_buscado)
        algun_match_de_grupo   = False
        huboalguna_multiversion = False

        caps = []
        for li in items:
            num_attr = li.get("data-chapter-number", "").strip()
            try:
                num = numero_a_float(num_attr)
            except Exception:
                continue

            enlaces_version = li.select('a[href*="/view_uploads/"]')
            if not enlaces_version:
                continue

            versiones = []
            for a in enlaces_version:
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                m_id = re.search(r"/view_uploads/(\d+)", href)
                if not m_id:
                    continue
                upload_id = int(m_id.group(1))

                bloque = a.find_parent("div") or li
                texto_bloque = bloque.get_text(" ", strip=True)

                grupo_a = bloque.select_one('a[href*="/groups/"]')
                grupo_nombre = grupo_a.get_text(strip=True) if grupo_a else ""

                m_fecha = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto_bloque)
                fecha = None
                if m_fecha:
                    try:
                        fecha = datetime(int(m_fecha.group(3)), int(m_fecha.group(2)),
                                          int(m_fecha.group(1)))
                    except ValueError:
                        fecha = None

                versiones.append({
                    "url": href if href.startswith("http") else f"{self.BASE_URL}{href}",
                    "upload_id": upload_id,
                    "grupo": grupo_nombre,
                    "fecha": fecha,
                })

            if not versiones:
                continue
            if len(versiones) > 1:
                huboalguna_multiversion = True

            elegida = None
            if grupo_buscado:
                for v in versiones:
                    if grupo_buscado in v["grupo"].lower():
                        elegida = v
                        algun_match_de_grupo = True
                        break

            if elegida is None:
                # Fallback: la más antigua. Orden principal por fecha
                # (None al final para no ganarle a una fecha real),
                # desempate/backup por upload_id ascendente.
                elegida = min(
                    versiones,
                    key=lambda v: (v["fecha"] is None, v["fecha"] or datetime.max, v["upload_id"])
                )

            n_int = int(num) if num == int(num) else num
            caps.append({
                "numero": num,
                "url":    elegida["url"],
                "titulo": f"Capítulo {n_int}",
                "slug":   slug,
            })

        if hubo_grupo_configurado and huboalguna_multiversion and not algun_match_de_grupo:
            log.warning(f"  ⚠  [ZonaTMO] grupo_preferido='{grupo_preferido}' configurado "
                       f"para '{slug}' pero no matcheó NINGUNA versión en todo el manga "
                       f"(¿nombre mal escrito?) — se usó la versión más antigua en todos "
                       f"los casos")

        caps.sort(key=lambda x: x["numero"])
        return caps

    def obtener_imagenes(self, cap_url: str, slug: str, numero) -> list[str]:
        """
        Extrae imágenes por 'data-page' en vez de por el nombre de
        archivo — el nombre NO es confiable como número de página:
        capítulos viejos usan un hash random
        (chapters/927162/598e5912....webp) en vez de un número
        secuencial (chapters/972720/1.webp). Confirmado con HTML real
        de un capítulo que falló (ago-2026): mismo sitio, dos esquemas
        de nombre distintos según cuándo/quién subió el capítulo.

        Restringido a '#reader-wrap' a propósito: la página precarga
        las primeras imágenes del CAPÍTULO SIGUIENTE en un array JS
        (`const nextUrls = [...]`) para el scroll continuo, con el
        mismo dominio storage.zonatmo.org/chapters/ — sin esta
        restricción, un capítulo con nombres de archivo numéricos
        terminaría trayendo también páginas del capítulo de al lado.

        El dominio del CDN de imágenes NO es siempre 'storage.zonatmo.org'
        — confirmado con HTML real (2026-08-23) que el sitio balancea
        carga entre 'storage.zonatmo.org' Y 'storage2.zonatmo.org',
        mezclados dentro del mismo capítulo (se vio en el propio array
        nextUrls, con las dos variantes una al lado de la otra). Antes
        se exigía la substring exacta 'storage.zonatmo.org/chapters/',
        que rechazaba silenciosamente cualquier página servida desde
        'storage2' — en la práctica, cualquier capítulo que tuviera
        aunque sea una sola página en 'storage2' terminaba con "sin
        páginas válidas" aunque el HTML estuviera perfectamente bien.
        """
        _RE_STORAGE_ZONATMO = re.compile(r"storage\d*\.zonatmo\.org/chapters/")

        r = hacer_get(cap_url, self.session)
        if not r:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        contenedor = soup.select_one("#reader-wrap")
        if not contenedor:
            debug_path = DEBUG_SELENIUM_PATH
            debug_path.write_text(r.text, encoding="utf-8")
            log.warning(f"  [ZonaTMO] No se encontró '#reader-wrap' en {cap_url} — "
                       f"HTML volcado en {debug_path} para revisar")
            return []

        paginas = {}
        for wrap in contenedor.select(".reader-img-wrap[data-page]"):
            try:
                n = int(wrap.get("data-page"))
            except (TypeError, ValueError):
                continue
            img = wrap.select_one("img.reader-image") or wrap.select_one("img")
            if not img:
                continue
            src = (img.get("src") or "").strip()
            if not src or not _RE_STORAGE_ZONATMO.search(src):
                continue
            paginas[n] = src

        if not paginas:
            debug_path = DEBUG_SELENIUM_PATH
            debug_path.write_text(r.text, encoding="utf-8")
            log.warning(f"  [ZonaTMO] '#reader-wrap' encontrado pero sin páginas "
                       f"válidas (data-page + img) en {cap_url} — "
                       f"HTML volcado en {debug_path} para revisar")
            return []

        urls = [paginas[n] for n in sorted(paginas)]
        log.info(f"  [ZonaTMO] {len(urls)} imágenes — {cap_url}")
        return urls


def crear_scraper(fuente: str) -> ScraperBase | None:
    fuente = fuente.lower().strip()
    scrapers = {
        "olympus":   OlympusScraper,
        "temple":    TempleScraper,
        "dragon":    DragonScraper,
        "manhwaweb": ManhwasWebScraper,
        "nexus":     NexusScraper,
        "ikigai":    IkigaiScraper,
        "leercapitulo": LeerCapituloScraper,
        "taurus":     TauroScraper,
        "tmo":        TmoScraper,
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
                            timeout_seg: float) -> tuple[bool, bool, str | None]:
    """
    Ejecuta scraper.descargar_capitulo() con un límite de tiempo real.

    Corre la descarga en un thread daemon aparte: si no termina dentro
    de timeout_seg, esta función retorna igual (no bloquea el resto del
    escaneo), marcando colgado=True. El thread daemon eventualmente
    termina solo en segundo plano (o el proceso se cierra junto con el
    programa principal si nunca termina) — no se "mata" a la fuerza
    porque Python no tiene una forma segura de hacerlo sin arriesgar
    corrupción de archivos a mitad de escritura.

    Retorna (ok, colgado, motivo):
      - (True/False, False, motivo)  → terminó a tiempo, resultado normal.
        'motivo' es None si ok=True; si ok=False, es la razón específica
        que descargar_capitulo() dejó en self._ultimo_motivo_fallo (ver
        ahí) — permite que el resumen final diga POR QUÉ falló en vez
        de un genérico "no se pudo descargar".
      - (False, True, None)  → no terminó a tiempo, se abandonó.
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
        return False, True, None

    if excepcion[0] is not None:
        raise excepcion[0]

    ok = bool(resultado[0])
    motivo = None if ok else getattr(scraper, "_ultimo_motivo_fallo", None)
    return ok, False, motivo


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
    nombre           = sanitizar_nombre_carpeta(manga_cfg["nombre_carpeta"])
    prioridad_propia = prioridad_efectiva(manga_cfg)  # SIEMPRE un número
    t_inicio_manga   = time.time()
    omitidos_mejor   = 0  # cubierto por una fuente de prioridad estrictamente mejor
    omitidos_empate  = 0  # cubierto por una fuente de la MISMA prioridad manual
                           # (doble prioridad) — gana quien lo haya descargado
                           # primero, sin importar el orden de escaneo del ciclo
    omitidos_legado  = 0

    for idx_cap, cap in enumerate(caps_nuevos):
        num      = cap["numero"]
        nombrecap = nombre_capitulo(num)

        CONTROL.avanzar_capitulo(idx_cap + 1, len(caps_nuevos))
        CONTROL.chequear_y_lanzar()

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
                #
                # Empate (prioridad_propia == prio_previa): esto es el
                # sistema de "doble prioridad" — dos fuentes declaradas
                # a mano como igual de confiables para este manga. Acá
                # NO se recalcula nada por ORDEN_FUENTES ni se favorece
                # a ninguna fuente por convención: gana la que ya está
                # descargada (quien haya llegado primero, en este ciclo
                # o en uno anterior), y esa versión queda fija — la
                # fuente empatada nunca la reemplaza después.
                if prio_previa is not None and prioridad_propia >= prio_previa:
                    if prioridad_propia == prio_previa:
                        omitidos_empate += 1
                    else:
                        omitidos_mejor += 1
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
            ok, colgado, motivo_fallo = _descargar_con_timeout(scraper, cap, carpeta_destino, restante)
            pausas_nuevas = scraper.detector_bloqueo.veces_pausado - pausas_antes
        except Exception as e:
            log.error(f"  Error inesperado descargando {nombrecap}: {e}")
            marcar_capitulo_con_error(manga_cfg, num)
            if upgrade_de is not None:
                shutil.rmtree(carpeta_destino, ignore_errors=True)
            if reporte:
                _sufijo_cubierto = f" (Cubierto por {upgrade_de})" if upgrade_de else ""
                reporte.error(nombre, fuente,
                              f"{nombrecap}: excepción ({type(e).__name__}) — {e}{_sufijo_cubierto}")
            time.sleep(1)
            continue

        if colgado:
            log.warning(f"  ⏱  {nombrecap} de {nombre} excedió {int(restante)}s "
                       f"sin terminar — se abandona y se sigue con el resto")
            marcar_capitulo_con_error(manga_cfg, num)
            if upgrade_de is not None:
                shutil.rmtree(carpeta_destino, ignore_errors=True)
            if reporte:
                _sufijo_cubierto = f" (Cubierto por {upgrade_de})" if upgrade_de else ""
                reporte.advertencia(nombre, fuente,
                    f"⏱ {nombrecap}: excedió el tiempo límite ({int(restante)}s) "
                    f"y se abandonó — se reintentará en el próximo escaneo{_sufijo_cubierto}")
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
                # Independiente de parcial/éxito: un capítulo puede
                # haber quedado "completado" (se guardó todo lo que el
                # filtro aceptó) y aun así ser sospechoso (aceptó muy
                # poco comparado con el resto del manga) — no son la
                # misma señal, así que se registran las dos.
                if info.get("sospechoso_mediana") is not None:
                    reporte.sospechoso(nombre, fuente, nombrecap,
                                       info.get("validas", 0),
                                       info["sospechoso_mediana"])
        else:
            marcar_capitulo_con_error(manga_cfg, num)
            if upgrade_de is not None:
                shutil.rmtree(carpeta_destino, ignore_errors=True)
                log.warning(f"    ⬆️  {nombrecap}: no se pudo mejorar desde "
                           f"'{fuente}', se mantiene la versión de '{upgrade_de}'")
            if reporte:
                _sufijo_cubierto = f" (Cubierto por {upgrade_de})" if upgrade_de else ""
                if motivo_fallo:
                    reporte.error(nombre, fuente, f"{nombrecap}: {motivo_fallo}{_sufijo_cubierto}")
                else:
                    reporte.error(nombre, fuente, f"{nombrecap}: no se pudo descargar{_sufijo_cubierto}")

        time.sleep(1)

    if omitidos_mejor or omitidos_empate or omitidos_legado:
        partes = []
        if omitidos_mejor:
            partes.append(f"{omitidos_mejor} ya cubierto(s) por otra fuente de mejor prioridad")
        if omitidos_empate:
            partes.append(f"{omitidos_empate} ya cubierto(s) por otra fuente de IGUAL prioridad "
                          f"(doble prioridad, ganó quien lo descargó primero)")
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
        log.info("")
        nombre        = sanitizar_nombre_carpeta(manga_cfg["nombre_carpeta"])
        slug_guardado = manga_cfg.get("slug", "")
        ultimo        = float(manga_cfg.get("ultimo_capitulo", 0))
        carpeta_manga = carpeta_base / nombre

        CONTROL.avanzar_manga(nombre)
        CONTROL.chequear_y_lanzar()

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
    nombre        = sanitizar_nombre_carpeta(manga_cfg["nombre_carpeta"])
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
        # tmo es la única fuente que necesita datos de manga_cfg ANTES
        # de resolver la lista de capítulos: grupo_preferido decide qué
        # versión de cada capítulo se elige cuando hay varias, y
        # url_manga es necesaria porque ZonaTMO categoriza el contenido
        # por tipo en la URL (/library/manga/, /library/manhwa/,
        # /library/manhua/, /library/novela/...) — no hay forma de
        # reconstruir eso solo a partir del slug (ver docstring de
        # TmoScraper.obtener_capitulos). El resto de las fuentes no usan
        # nada de manga_cfg acá, así que siguen con la firma simple.
        if fuente == "tmo":
            caps = scraper.obtener_capitulos(slug, grupo_preferido=manga_cfg.get("grupo_preferido"),
                                              url_manga=manga_cfg.get("url_manga"))
        else:
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
    try:
        return _descargar_caps_nuevos(nuevos, manga_cfg, carpeta_manga, scraper, reporte)
    finally:
        # Cierra el navegador reutilizado (si se abrió uno para este
        # manga) — se abre a demanda en el primer capítulo que lo
        # necesite y se reutiliza para todos los siguientes; acá se
        # cierra una sola vez al terminar, en vez de por capítulo.
        scraper.cerrar_driver_selenium()


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
                "sospechosos": [],
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

    def sospechoso(self, nombre: str, fuente: str, capitulo: str,
                   paginas: int, mediana: float):
        """
        Registra un capítulo marcado 🚩 por _chequear_paginas_sospechosas
        (muchas menos páginas que la mediana reciente de este manga).
        Se muestra en el resumen final con el número de capítulo y el
        conteo exacto — pensado para alguien que no sigue la terminal en
        vivo y solo quiere ver, al final, qué capítulos puntuales vale
        la pena abrir a revisar a mano.
        """
        self._manga(nombre, fuente)["sospechosos"].append(
            {"capitulo": capitulo, "paginas": paginas, "mediana": mediana})

    def bloqueo(self, nombre: str, fuente: str, mensaje: str):
        """
        Registra una pausa anti-bloqueo (BlockDetector) detectada durante
        la descarga. Se muestra aparte de errores/advertencias en el
        reporte porque significa "el sitio nos está rate-limitando",
        no "algo está roto" — pero conviene saberlo igual si se repite
        seguido en varios escaneos.
        """
        self._manga(nombre, fuente)["bloqueos"].append(_truncar_mensaje(mensaje))

    def imprimir_resumen_terminal(self):
        """
        Imprime al log un resumen agrupado por manga de todo lo que tuvo
        algún problema en este ciclo (errores, capítulos parciales,
        capítulos sospechosos por pocas páginas, bloqueos anti
        rate-limit). No repite las advertencias sueltas (esas ya se ven
        en vivo y suelen ser informativas, no fallas), pero sí las
        cuenta si están presentes.

        Pensado para alguien que no sigue la terminal en vivo mientras
        corre el scraper: cada categoría con problemas queda con el
        detalle puntual (qué capítulo, cuántas páginas, qué mensaje) en
        vez de solo un número — así alcanza con leer el final del log
        de este ciclo para saber exactamente qué revisar a mano, sin
        tener que haber visto pasar el 🚩 o el ❌ en el momento.

        Se llama al final de ciclo_escaneo(), después de que ya se
        procesaron todos los mangas.
        """
        con_problemas = {
            nombre: info for nombre, info in self._mangas.items()
            if info["errores"] or info["parciales"] or info["bloqueos"]
            or info["sospechosos"]
        }
        if not con_problemas:
            return

        log.info("")
        log.info("  ── Resumen de incidencias del ciclo ──")
        for nombre, info in con_problemas.items():
            partes = []
            if info["sospechosos"]:
                partes.append(f"{len(info['sospechosos'])} sospechoso(s) por pocas páginas")
            if info["errores"]:
                partes.append(f"{len(info['errores'])} error(es)")
            if info["parciales"]:
                partes.append(f"{len(info['parciales'])} capítulo(s) parcial(es)")
            if info["bloqueos"]:
                partes.append(f"{len(info['bloqueos'])} bloqueo(s) anti rate-limit")
            log.info(f"    • {nombre} [{info['fuente']}]: {', '.join(partes)}")

            for s in info["sospechosos"]:
                log.info(f"        🚩 {s['capitulo']}: {s['paginas']} página(s) "
                          f"(mediana reciente de este manga: {s['mediana']:.0f})")
            for e in info["errores"]:
                log.info(f"        ❌ {e}")
            for p in info["parciales"]:
                log.info(f"        ⚠  {p['capitulo']}: {p['validas']}/{p['esperadas']} "
                          f"página(s) — se reintentará lo faltante en el próximo escaneo")

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

    aplicar_orden_personalizado(data)

    carpeta_base = obtener_carpeta_base()
    mangas       = [m for m in data.get("mangas", []) if m.get("activo", True)]
    mangas       = filtrar_fuentes_desactivadas(mangas, data)
    reporte      = ReporteEscaneo()
    CONTROL.iniciar_ciclo(len(mangas))

    log.info(f"  📂 {carpeta_base}")
    log.info(f"  📚 {len(mangas)} manga(s) activos")

    # Si algún manga tiene 'url_manga' (URL completa pegada del navegador),
    # extraer el slug automáticamente antes de procesar nada.
    hubo_cambios_url = False
    for manga_cfg in mangas:
        if normalizar_url_manga(manga_cfg):
            hubo_cambios_url = True
    if hubo_cambios_url:
        guardar_seguimiento(data)
        log.info("")

    # Avisos de cordura (no frenan nada) sobre configuraciones de
    # seguimiento.json que probablemente sean un error humano.
    validar_multi_fuente(mangas)

    total_nuevos = 0

    # ── Armar la secuencia de escaneo completa, ya en el orden efectivo ──
    # Antes Olympus corría en un bloque aparte SIEMPRE primero, sin
    # importar ORDEN_FUENTES para nada — reordenar desde el panel del
    # navegador nunca lo movía de lugar. Ahora se intercala en el mismo
    # sorteo que el resto: técnicamente sigue usando su propio mecanismo
    # de dos fases (escanear_olympus, que primero revisa las páginas de
    # "novedades" para no perderse slugs rotados — eso no cambió, sigue
    # haciendo falta), pero el LUGAR donde ese bloque corre dentro del
    # ciclo ahora sí respeta el orden configurado (de fábrica o
    # personalizado) igual que cualquier otra fuente.
    mangas_olympus = [m for m in mangas if m.get("fuente") == "olympus"]
    mangas_otros   = [m for m in mangas if m.get("fuente") != "olympus"]
    sitios = {}
    for m in mangas_otros:
        sitios.setdefault(m.get("fuente","?"), []).append(m)
    if mangas_olympus:
        sitios["olympus"] = mangas_olympus

    # Orden de escaneo: de mejor a peor calidad/confiabilidad (mismo
    # criterio y misma variable que la prioridad automática — ver
    # ORDEN_FUENTES/aplicar_orden_personalizado() arriba, así nunca se
    # desincronizan; ya viene resuelta para este ciclo, sea la de fábrica
    # o una personalizada desde seguimiento.json). Si más adelante un
    # sitio de mejor calidad falla un capítulo puntual, esto asegura que
    # ya corrió ANTES que los de menor prioridad en el mismo ciclo
    # (relevante para el sistema de prioridad multi-fuente).
    sitios = dict(sorted(
        sitios.items(),
        key=lambda kv: ORDEN_FUENTES.index(kv[0]) if kv[0] in ORDEN_FUENTES else len(ORDEN_FUENTES)
    ))

    for fuente, lista in sitios.items():
        if fuente == "olympus":
            log.info(f"\n  ┌─ 🏛  Olympus ({len(lista)} manga(s))")
            try:
                scraper_olympus = OlympusScraper()
                n = escanear_olympus(lista, carpeta_base, scraper_olympus, reporte,
                                     guardado_incremental=lambda: guardar_seguimiento(data))
                total_nuevos += n
                log.info(f"  └─ ✓ {n} capítulo(s) nuevos")
            except Exception as e:
                log.error(f"  └─ ✗ Error Olympus: {e}")
                reporte.error("Olympus (general)", "olympus", f"Error general del bloque: {e}")
            finally:
                # Guardar lo que se haya avanzado, incluso si el bloque
                # entero tiró una excepción a mitad de camino (no perder
                # progreso ya confirmado de los mangas que sí se
                # terminaron de procesar).
                guardar_seguimiento(data)
            continue

        icono = {"nexus":"🔗","temple":"🏯","dragon":"🐉","manhwaweb":"📚","ikigai":"🌸","leercapitulo":"📕","taurus":"🐂","tmo":"📙"}.get(fuente,"📖")
        log.info(f"\n  ┌─ {icono}  {fuente.title()} ({len(lista)} manga(s))")
        n_fuente = 0
        for manga_cfg in lista:
            CONTROL.avanzar_manga(manga_cfg.get('nombre_carpeta', '?'))
            CONTROL.chequear_y_lanzar()
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

    # Resumen agrupado de todo lo que tuvo problemas este ciclo (si hubo
    # algo) — antes había que scrollear todo el log para juntarlo a mano.
    reporte.imprimir_resumen_terminal()

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
    CONTROL.iniciar("continuous")
    while True:
        try:
            ciclo_escaneo()
        except Exception as e:
            log.error(f"Error crítico en ciclo de escaneo ({type(e).__name__}): {e}")

        proxima = INTERVALO_HORAS * 3600
        objetivo = time.time() + proxima
        log.info(f"⏰ Próximo escaneo en {INTERVALO_HORAS} horas")
        CONTROL.marcar_esperando(objetivo)

        # Dormir en tramos cortos en vez de un time.sleep(proxima) único
        # — así, si piden detener durante la espera entre ciclos (no
        # solo durante un escaneo activo), se nota en segundos y no
        # hasta una hora después. También hay que refrescar el
        # heartbeat cada tanto durante esta espera larga: si no,
        # queda con la marca de tiempo de hace casi una hora, y Node
        # (que usa el heartbeat para saber si el proceso sigue vivo)
        # lo confundiría con una caída real.
        tramos = 0
        while time.time() < objetivo:
            CONTROL.chequear_y_lanzar()
            if tramos % 6 == 0:  # cada ~30s (6 tramos de 5s), no en cada uno
                CONTROL.marcar_esperando(objetivo)
            tramos += 1
            time.sleep(max(0, min(5, objetivo - time.time())))

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
    log.info("║       📚  M4RTO SCRAPER  v1.9            ║")
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

    detenido_manualmente = False
    try:
        if not SEGUIMIENTO_PATH.exists():
            log.error(f"No se encontró seguimiento.json en {SCRIPT_DIR}")
            log.error("Creá el archivo con tus mangas antes de iniciar el scraper.")
            sys.exit(1)

        # Modo --una-vez: ejecutar una sola vez y salir (útil para pruebas)
        if "--una-vez" in sys.argv:
            log.info("Modo: una sola ejecución")
            CONTROL.iniciar("once")
            try:
                ciclo_escaneo()
            except (DetenerScraperError, KeyboardInterrupt):
                detenido_manualmente = True
                log.info("⏹  Detenido — se guardó todo lo confirmado hasta ahora.")
            except Exception as e:
                log.error(f"Error crítico en el escaneo ({type(e).__name__}): {e}")
                sys.exit(1)
            return

        # Modo normal: loop infinito con intervalo
        try:
            scheduler()
        except (DetenerScraperError, KeyboardInterrupt):
            detenido_manualmente = True
            log.info("⏹  Modo continuo detenido — se guardó todo lo confirmado hasta ahora.")
    finally:
        CONTROL.finalizar(detenido_manualmente)
        _liberar_lock()

if __name__ == "__main__":
    main()
