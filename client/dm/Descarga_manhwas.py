"""
╔══════════════════════════════════════════════════════════════════════╗
║       🎌  MANGA DOWNLOADER UNIFICADO  v9.0                          ║
║                                                                      ║
║  Sitios soportados:                                                  ║
║    1. 🏛️  Olympus Scan       olympusbiblioteca.com                  ║
║    2. 🌸  Ikigai Mangas      zonaikigai.milkchoco.online            ║
║    3. 📚  ManhwasWEB         manhwaweb.com                          ║
║    4. 🐉  Dragon Translation dragontranslation.org                  ║
║    5. 💜  Manhwa Latino      manhwa-latino.com                      ║
║                                                                      ║
║  Configuración → config.json  (se crea automáticamente)             ║
║  Instala deps  → pip install requests beautifulsoup4 Pillow         ║
║                              selenium tqdm webdriver-manager psutil  ║
║                              undetected-chromedriver DrissionPage    ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# ══════════════════════════════════════════════════════════════════════
# §0  AUTO-INSTALADOR
# ══════════════════════════════════════════════════════════════════════

import sys
import subprocess
import importlib.util

_PAQUETES_BASE = {
    "requests":          "requests",
    "bs4":               "beautifulsoup4",
    "PIL":               "Pillow",
    "selenium":          "selenium",
    "tqdm":              "tqdm",
    "webdriver_manager": "webdriver-manager",
    "psutil":            "psutil",
}

_PAQUETES_OPC = {
    "undetected_chromedriver": "undetected-chromedriver",
    "DrissionPage":            "DrissionPage",
}


def _auto_instalar():
    faltantes = [(m, p) for m, p in _PAQUETES_BASE.items()
                 if importlib.util.find_spec(m) is None]
    if not faltantes:
        return
    print("\n" + "═"*60)
    print("  ⚠️   PAQUETES FALTANTES")
    print("═"*60)
    for _, p in faltantes:
        print(f"     • {p}")
    if input("\n  ¿Instalar automáticamente? (s/n): ").lower() != "s":
        sys.exit(1)
    for _, p in faltantes:
        print(f"  📦 {p}...", end=" ", flush=True)
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", p, "-q"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("✅")
        except Exception as e:
            print(f"❌ ({e})")
    print("\n  ✅ Reiniciando...\n")
    import os
    os.execv(sys.executable, [sys.executable] + sys.argv)


_auto_instalar()

# ══════════════════════════════════════════════════════════════════════
# §1  IMPORTS
# ══════════════════════════════════════════════════════════════════════

import os
import re
import io
import json
import time
import signal
import shutil
import threading
import datetime
from abc import ABC, abstractmethod
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeoutError
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import psutil
import requests
from bs4 import BeautifulSoup
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

try:
    from tqdm import tqdm
    TQDM_OK = True
except ImportError:
    TQDM_OK = False

try:
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service as ChromeService
    WDM_OK = True
except ImportError:
    WDM_OK = False

try:
    import undetected_chromedriver as uc
    UC_OK = True
except ImportError:
    UC_OK = False

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    DP_OK = True
except ImportError:
    DP_OK = False

# ══════════════════════════════════════════════════════════════════════
# §2  CTRL+C GRACEFUL
# ══════════════════════════════════════════════════════════════════════

_INTERRUMPIDO = threading.Event()


def _handler_sigint(sig, frame):
    if not _INTERRUMPIDO.is_set():
        print("\n\n  ⚠️   CTRL+C — terminando capítulo actual y saliendo...\n")
        _INTERRUMPIDO.set()


signal.signal(signal.SIGINT, _handler_sigint)

# ══════════════════════════════════════════════════════════════════════
# §3  SITIOS DISPONIBLES  (fuente única de verdad para numeración)
# ══════════════════════════════════════════════════════════════════════

SITIOS = {
    "1": {"clave": "olympus",   "nombre": "Olympus Scan",       "icono": "🏛️"},
    "2": {"clave": "ikigai",    "nombre": "Ikigai Mangas",      "icono": "🌸"},
    "3": {"clave": "manhwaweb", "nombre": "ManhwasWEB",         "icono": "📚"},
    "4": {"clave": "dragon",    "nombre": "Dragon Translation",  "icono": "🐉"},
    "5": {"clave": "manhwalatico", "nombre": "Manhwa Latino",   "icono": "💜"},
}

def _nombre_sitio(clave: str) -> str:
    for v in SITIOS.values():
        if v["clave"] == clave:
            return f"{v['icono']} {v['nombre']}"
    return clave

def _icono_sitio(clave: str) -> str:
    for v in SITIOS.values():
        if v["clave"] == clave:
            return v["icono"]
    return "📖"

# ══════════════════════════════════════════════════════════════════════
# §4  CONFIG.JSON
# ══════════════════════════════════════════════════════════════════════

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(_SCRIPT_DIR, "config.json")

_CONFIG_DEFAULT: dict = {
    "_nota": "Manga Downloader Unificado v8.0",
    "carpeta_base":    r"C:\Users\octan\Documents\mangas",
    "usar_brave":      True,
    "ruta_brave":      "",
    "ruta_chrome":     r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "ancho_minimo":    600,
    "alto_minimo":     800,
    "max_reintentos":  5,
    "delay_reintento": 3,
    "num_hilos":       4,
    "verificar_integridad": True,
    "timeout":         15,
    "ultimo_manga":    0,
    "compresion_pdf": {
        "activada":     True,
        "calidad_jpeg": 85,
        "max_ancho_px": 1600,
    },
    "deteccion_bloqueo": {
        "errores_consecutivos_max": 8,
        "pausa_segundos":          120,
    },
    # ── Filtros adaptativos por sitio ────────────────────────────────
    # ancho_minimo_abs: píxeles mínimos de ancho para no ser descartada de inmediato
    # alto_minimo_abs:  píxeles mínimos de alto
    # ratio_max:        ratio ancho/alto máximo (>1 = horizontal). 4.0 = banner muy ancho
    # ratio_cuadrado_max_px: si la imagen es casi cuadrada (ratio 0.8-1.2) y
    #                        tiene menos de estos px se considera logo/icono
    # tolerancia_ancho_pct: margen % para la detección de ancho dominante del capítulo
    # min_imgs_fallback: si quedan menos de este nº de imgs tras filtrar, se relajan filtros
    "filtros_sitio": {
        "olympus": {
            "ancho_minimo_abs": 400, "alto_minimo_abs": 400,
            "ratio_max": 3.5, "ratio_cuadrado_max_px": 500,
            "tolerancia_ancho_pct": 15, "min_imgs_fallback": 2,
        },
        "ikigai": {
            "ancho_minimo_abs": 400, "alto_minimo_abs": 400,
            "ratio_max": 3.5, "ratio_cuadrado_max_px": 500,
            "tolerancia_ancho_pct": 15, "min_imgs_fallback": 2,
        },
        "manhwaweb": {
            # ManhwasWEB: anchos muy variables (500-1080px) y páginas muy largas
            "ancho_minimo_abs": 300, "alto_minimo_abs": 300,
            "ratio_max": 4.0, "ratio_cuadrado_max_px": 400,
            "tolerancia_ancho_pct": 30, "min_imgs_fallback": 2,
        },
        "dragon": {
            "ancho_minimo_abs": 150, "alto_minimo_abs": 150,
            "ratio_max": 4.0, "ratio_cuadrado_max_px": 500,
            "tolerancia_ancho_pct": 20, "min_imgs_fallback": 2,
        },
        "manhwalatico": {
            "ancho_minimo_abs": 400, "alto_minimo_abs": 400,
            "ratio_max": 3.5, "ratio_cuadrado_max_px": 500,
            "tolerancia_ancho_pct": 20, "min_imgs_fallback": 2,
        },
    },
    "mangas": [],
}


def cargar_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_CONFIG_DEFAULT, f, indent=2, ensure_ascii=False)
        print(f"\n  ✅  config.json creado: {CONFIG_PATH}\n")
        return _CONFIG_DEFAULT.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in _CONFIG_DEFAULT.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    except Exception as e:
        print(f"  ⚠️   Error config.json: {e}")
        return _CONFIG_DEFAULT.copy()


def guardar_config(cfg: dict):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)

# ══════════════════════════════════════════════════════════════════════
# §5  CONTEXTO DE MANGA
# ══════════════════════════════════════════════════════════════════════

@dataclass
class MangaCtx:
    nombre:             str
    sitio:              str
    url:                str
    urls_paginas:       list
    carpeta_manga:      str
    carpeta_convertir:  str
    carpeta_rango:      str
    carpeta_sin_filtro: str
    carpeta_pdf:        str
    carpeta_logs:       str
    archivo_cache:      str
    archivo_registro:   str
    ancho_min:  int
    alto_min:   int
    max_reintentos:   int
    delay_reintento:  float
    num_hilos:        int
    timeout:          int
    headers: dict = field(default_factory=lambda: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    verificar_integridad: bool = True
    comp_activada:  bool = True
    comp_calidad:   int  = 85
    comp_max_ancho: int  = 1600
    bloqueo_max_errs: int = 8
    bloqueo_pausa:    int = 120
    usar_brave: bool = True
    ruta_brave: str  = ""
    ruta_chrome: str = ""
    # filtros adaptativos para el engine (se cargan desde config según sitio)
    filtro_ancho_min_abs:     int   = 400
    filtro_alto_min_abs:      int   = 400
    filtro_ratio_max:         float = 3.5
    filtro_cuadrado_max_px:   int   = 500
    filtro_tolerancia_ancho:  int   = 15    # % margen ancho dominante
    filtro_min_imgs_fallback: int   = 2     # relajar filtros si quedan menos imgs


_FILTROS_SITIO_DEFAULT = {
    "ancho_minimo_abs": 400, "alto_minimo_abs": 400,
    "ratio_max": 3.5, "ratio_cuadrado_max_px": 500,
    "tolerancia_ancho_pct": 15, "min_imgs_fallback": 2,
}

def _filtros_para_sitio(sitio: str, cfg: dict) -> dict:
    """Extrae los parámetros de filtro para el sitio indicado desde config."""
    mapa = cfg.get("filtros_sitio", {})
    f = {**_FILTROS_SITIO_DEFAULT, **mapa.get(sitio, {})}
    return {
        "filtro_ancho_min_abs":     int(f["ancho_minimo_abs"]),
        "filtro_alto_min_abs":      int(f["alto_minimo_abs"]),
        "filtro_ratio_max":         float(f["ratio_max"]),
        "filtro_cuadrado_max_px":   int(f["ratio_cuadrado_max_px"]),
        "filtro_tolerancia_ancho":  int(f["tolerancia_ancho_pct"]),
        "filtro_min_imgs_fallback": int(f["min_imgs_fallback"]),
    }


def crear_ctx(manga_cfg: dict, cfg: dict) -> MangaCtx:
    nombre = manga_cfg["nombre"]
    base   = cfg.get("carpeta_base", r"C:\Users\octan\Documents\mangas")
    cm     = os.path.join(base, nombre)
    comp   = cfg.get("compresion_pdf", {})
    blq    = cfg.get("deteccion_bloqueo", {})
    for d in [cm,
              os.path.join(cm, "Capitulos_a_Convertir"),
              os.path.join(cm, "Capitulos_por_Rango"),
              os.path.join(cm, "Capitulos_sin_Filtro"),
              os.path.join(cm, "Manhwa_a_PDF"),
              os.path.join(cm, "logs")]:
        os.makedirs(d, exist_ok=True)

    sitio = manga_cfg.get("sitio", "olympus")
    hdrs  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if sitio == "ikigai":
        hdrs["Accept"]  = "image/avif,image/webp,image/apng,*/*;q=0.8"
        hdrs["Referer"] = "https://viralikigai.ozoviral.xyz/"
    elif sitio == "manhwaweb":
        hdrs["Referer"] = "https://manhwaweb.com/"
    elif sitio == "manhwalatico":
        hdrs["Referer"] = "https://manhwa-latino.com/"

    return MangaCtx(
        nombre            = nombre,
        sitio             = sitio,
        url               = manga_cfg.get("url", ""),
        urls_paginas      = manga_cfg.get("urls_paginas", []),
        carpeta_manga     = cm,
        carpeta_convertir = os.path.join(cm, "Capitulos_a_Convertir"),
        carpeta_rango     = os.path.join(cm, "Capitulos_por_Rango"),
        carpeta_sin_filtro= os.path.join(cm, "Capitulos_sin_Filtro"),
        carpeta_pdf       = os.path.join(cm, "Manhwa_a_PDF"),
        carpeta_logs      = os.path.join(cm, "logs"),
        archivo_cache     = os.path.join(cm, f"{nombre}_capitulos.json"),
        archivo_registro  = os.path.join(cm, f"{nombre}_registro.json"),
        ancho_min         = cfg.get("ancho_minimo", 600),
        alto_min          = cfg.get("alto_minimo", 800),
        max_reintentos    = cfg.get("max_reintentos", 5),
        delay_reintento   = cfg.get("delay_reintento", 3),
        num_hilos         = cfg.get("num_hilos", 4),
        timeout           = cfg.get("timeout", 15),
        headers           = hdrs,
        verificar_integridad = cfg.get("verificar_integridad", True),
        comp_activada     = comp.get("activada", True),
        comp_calidad      = comp.get("calidad_jpeg", 85),
        comp_max_ancho    = comp.get("max_ancho_px", 1600),
        bloqueo_max_errs  = blq.get("errores_consecutivos_max", 8),
        bloqueo_pausa     = blq.get("pausa_segundos", 120),
        usar_brave        = cfg.get("usar_brave", True),
        ruta_brave        = cfg.get("ruta_brave", ""),
        ruta_chrome       = cfg.get("ruta_chrome", ""),
        **_filtros_para_sitio(sitio, cfg),
    )

# ══════════════════════════════════════════════════════════════════════
# §6  SESSION LOGGER
# ══════════════════════════════════════════════════════════════════════

class SessionLogger:
    def __init__(self, manga, sitio, tipo, carpeta_logs, carpeta_destino=""):
        self.manga          = manga
        self.sitio          = sitio
        self.tipo           = tipo
        self.carpeta_logs   = carpeta_logs
        self.carpeta_destino = carpeta_destino
        self.inicio         = datetime.datetime.now()
        self._lock          = threading.Lock()
        self._caps: dict    = {}
        self.imgs_error:  list = []
        self.imgs_corr:   list = []
        self.eventos:     list = []

    def _ev(self, tipo, msg, datos=None):
        e = {"timestamp": datetime.datetime.now().isoformat(), "tipo": tipo, "mensaje": msg}
        if datos:
            e["datos"] = datos
        with self._lock:
            self.eventos.append(e)

    def inicio_cap(self, numero, url):
        with self._lock:
            self._caps[str(numero)] = {
                "numero": numero, "url": url, "estado": "en_progreso",
                "_t0": datetime.datetime.now().isoformat(),
                "imagenes_guardadas": 0, "imagenes_fallidas_descarga": 0,
                "imagenes_rechazadas_filtro": 0, "imagenes_corrompidas": 0,
                "tiempo_segundos": 0.0,
            }

    def fin_cap(self, numero, estado, guardadas, fallidas, rechazadas, corrompidas):
        key = str(numero)
        with self._lock:
            if key in self._caps:
                t0 = datetime.datetime.fromisoformat(self._caps[key]["_t0"])
                self._caps[key].update({
                    "estado": estado, "imagenes_guardadas": guardadas,
                    "imagenes_fallidas_descarga": fallidas,
                    "imagenes_rechazadas_filtro": rechazadas,
                    "imagenes_corrompidas": corrompidas,
                    "tiempo_segundos": round((datetime.datetime.now() - t0).total_seconds(), 1),
                })

    def img_error(self, cap, idx, url, err):
        with self._lock:
            self.imgs_error.append({"capitulo": cap, "indice": idx, "url": url, "error": err})

    def img_corrompida(self, cap, idx, ruta, err):
        with self._lock:
            self.imgs_corr.append({"capitulo": cap, "indice": idx, "ruta": ruta, "error": err})

    def guardar(self) -> str:
        fin  = datetime.datetime.now()
        caps = [{k: v for k, v in c.items() if k != "_t0"} for c in self._caps.values()]
        payload = {
            "sesion": {
                "manga": self.manga, "sitio": self.sitio,
                "tipo_descarga": self.tipo, "carpeta_destino": self.carpeta_destino,
                "fecha_inicio": self.inicio.isoformat(), "fecha_fin": fin.isoformat(),
                "duracion_segundos": round((fin - self.inicio).total_seconds(), 1),
            },
            "resumen": {
                "capitulos_exitosos": sum(1 for c in caps if c["estado"] == "exitoso"),
                "capitulos_omitidos": sum(1 for c in caps if c["estado"] == "omitido"),
                "capitulos_fallidos": sum(1 for c in caps if c["estado"] == "fallido"),
                "imagenes_guardadas_total": sum(c["imagenes_guardadas"] for c in caps),
                "imagenes_error_descarga": len(self.imgs_error),
                "imagenes_corrompidas":    len(self.imgs_corr),
            },
            "capitulos": caps,
            "imagenes_con_error":   self.imgs_error,
            "imagenes_corrompidas": self.imgs_corr,
            "eventos":              self.eventos,
        }
        os.makedirs(self.carpeta_logs, exist_ok=True)
        ts   = self.inicio.strftime("%Y%m%d_%H%M%S")
        ruta = os.path.join(self.carpeta_logs, f"log_{self.manga}_{ts}.json")
        tmp  = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ruta)
        return ruta

# ══════════════════════════════════════════════════════════════════════
# §7  REGISTRO (RESUME)
# ══════════════════════════════════════════════════════════════════════

def _cargar_reg(ctx: MangaCtx) -> dict:
    if os.path.exists(ctx.archivo_registro):
        try:
            with open(ctx.archivo_registro, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _guardar_reg(ctx: MangaCtx, reg: dict):
    tmp = ctx.archivo_registro + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ctx.archivo_registro)


def cap_ya_descargado(reg, carp_reg, numero, carpeta_disco) -> bool:
    info = reg.get(carp_reg, {}).get(str(numero), {})
    if info.get("estado") != "completado":
        return False
    num_str  = str(int(numero)) if numero == int(numero) else str(numero)
    ruta_cap = os.path.join(carpeta_disco, f"Capitulo_{num_str}")
    if not os.path.isdir(ruta_cap):
        reg.get(carp_reg, {}).pop(str(numero), None)
        return False
    imgs = [f for f in os.listdir(ruta_cap)
            if f.lower().endswith((".jpg",".jpeg",".png",".webp",".gif",".bmp"))]
    if not imgs:
        reg.get(carp_reg, {}).pop(str(numero), None)
        return False
    return True


def marcar_completado(ctx, reg, carp_reg, numero, n_imgs):
    if carp_reg not in reg:
        reg[carp_reg] = {}
    reg[carp_reg][str(numero)] = {
        "estado": "completado", "imagenes": n_imgs,
        "fecha": datetime.datetime.now().isoformat(),
    }
    _guardar_reg(ctx, reg)


def marcar_parcial(ctx, reg, carp_reg, numero, n_imgs):
    if carp_reg not in reg:
        reg[carp_reg] = {}
    reg[carp_reg][str(numero)] = {
        "estado": "parcial", "imagenes": n_imgs,
        "fecha": datetime.datetime.now().isoformat(),
    }
    _guardar_reg(ctx, reg)


def promedio_paginas(reg, carp_reg) -> float:
    vals = [v["imagenes"] for v in reg.get(carp_reg, {}).values()
            if v.get("estado") == "completado" and v.get("imagenes", 0) > 0]
    return sum(vals) / len(vals) if vals else 0.0


def contar_caps_reg(ctx: MangaCtx) -> int:
    reg = _cargar_reg(ctx)
    return sum(sum(1 for v in c.values() if v.get("estado") == "completado")
               for c in reg.values())

# ══════════════════════════════════════════════════════════════════════
# §8  SPEED TRACKER + BLOCK DETECTOR
# ══════════════════════════════════════════════════════════════════════

class SpeedTracker:
    def __init__(self):
        self._lock   = threading.Lock()
        self._bytes  = 0
        self._inicio = time.time()

    def add(self, n):
        with self._lock:
            self._bytes += n

    @property
    def mbps(self):
        elapsed = max(time.time() - self._inicio, 0.01)
        with self._lock:
            return round(self._bytes / (1024*1024) / elapsed, 2)

    @property
    def total_mb(self):
        with self._lock:
            return round(self._bytes / (1024*1024), 2)


class BlockDetector:
    """
    Detecta bloqueos del servidor contando errores *consecutivos* a nivel
    de petición individual (no de hilo). Se corrigen tres bugs del original:

    1. El contador NO se comparte entre capítulos — se resetea al iniciar
       cada descarga de capítulo llamando a reset().
    2. La descarga es paralela (N hilos), por lo que N imágenes que fallan
       simultáneamente no deben contar como N*reintentos errores consecutivos.
       Usamos un umbral ponderado: solo disparamos si los errores superan
       max_errs Y no hubo ningún éxito entre ellos.
    3. El sleep de pausa se hace fuera del lock para no bloquear a los
       otros hilos que esperan en esperar_si_pausado().
    """
    def __init__(self, max_errs, pausa_seg):
        self._lock      = threading.Lock()
        self._event     = threading.Event()
        self._event.set()
        self._n_err     = 0   # errores consecutivos sin ningún éxito
        self._pausas    = 0   # cuántas veces pausamos (para logging)
        self.max_errs   = max_errs
        self.pausa_seg  = pausa_seg

    def reset(self):
        """Llamar al inicio de cada capítulo para limpiar el contador."""
        with self._lock:
            self._n_err  = 0
            self._pausas = 0
        self._event.set()   # asegurarse de que no quede pausado

    def esperar_si_pausado(self):
        self._event.wait()

    def registrar_error(self):
        with self._lock:
            self._n_err += 1
            # Solo lanzamos pausa si superamos el umbral Y el evento está activo
            # (para que solo UN hilo sea el que duerme, no todos a la vez)
            lanzar = (self._n_err >= self.max_errs) and self._event.is_set()
            if lanzar:
                self._event.clear()   # bloquear otros hilos mientras pausamos
                self._pausas += 1

        if lanzar:
            print(f"\n  🚫 Posible bloqueo ({self._n_err} errores consec.) "
                  f"— pausando {self.pausa_seg}s...")
            time.sleep(self.pausa_seg)   # fuera del lock
            with self._lock:
                self._n_err = 0
            print("  ▶️   Reanudando...\n")
            self._event.set()

    def registrar_exito(self):
        with self._lock:
            self._n_err = 0   # cualquier éxito resetea el contador consecutivo

# ══════════════════════════════════════════════════════════════════════
# §9  BRAVE / DRIVER AUTO-DETECCIÓN
# ══════════════════════════════════════════════════════════════════════

_RUTAS_BRAVE_WIN = [
    os.path.join(os.environ.get("PROGRAMFILES",    ""),
                 "BraveSoftware","Brave-Browser","Application","brave.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)",""),
                 "BraveSoftware","Brave-Browser","Application","brave.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA",    ""),
                 "BraveSoftware","Brave-Browser","Application","brave.exe"),
]
_RUTAS_CHROME_WIN = [
    os.path.join(os.environ.get("PROGRAMFILES",    ""),
                 "Google","Chrome","Application","chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)",""),
                 "Google","Chrome","Application","chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA",    ""),
                 "Google","Chrome","Application","chrome.exe"),
]


def _detectar_ruta_brave() -> str:
    for r in _RUTAS_BRAVE_WIN:
        if r and os.path.isfile(r):
            return r
    return ""


def _detectar_ruta_chrome() -> str:
    for r in _RUTAS_CHROME_WIN:
        if r and os.path.isfile(r):
            return r
    return ""


def _detectar_cualquier_browser() -> str:
    """Detecta Brave o Chrome automáticamente."""
    r = _detectar_ruta_brave()
    if r:
        return r
    return _detectar_ruta_chrome()


def _obtener_version_brave(ruta: str) -> str:
    try:
        lv = os.path.join(os.path.dirname(ruta), "Last Version")
        if os.path.isfile(lv):
            with open(lv) as f:
                v = f.read().strip()
            if re.match(r"\d+\.\d+\.\d+\.\d+", v):
                return v
    except Exception:
        pass
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\BraveSoftware\Brave-Browser\BLBeacon")
                v, _ = winreg.QueryValueEx(key, "version")
                winreg.CloseKey(key)
                if v:
                    return v
            except Exception:
                continue
    except ImportError:
        pass
    return ""


def _instalar_driver_brave(ruta_brave: str) -> "str | None":
    if not WDM_OK or not ruta_brave:
        return None
    version = _obtener_version_brave(ruta_brave)
    major   = version.split(".")[0] if version else ""
    for mod_path in ("webdriver_manager.core.os_manager", "webdriver_manager.utils"):
        try:
            mod = __import__(mod_path, fromlist=["ChromeType"])
            ChromeType = getattr(mod, "ChromeType")
            return ChromeDriverManager(chrome_type=ChromeType.BRAVE).install()
        except Exception:
            continue
    if major:
        try:
            r = requests.get(
                f"https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_{major}",
                timeout=10)
            r.raise_for_status()
            return ChromeDriverManager(driver_version=r.text.strip()).install()
        except Exception:
            pass
    try:
        return ChromeDriverManager().install()
    except Exception:
        return None


def _hacer_driver_headless(ruta_browser: str, es_brave: bool) -> "webdriver.Chrome | None":
    """Crea un driver headless genérico con anti-detección."""
    options = Options()
    if ruta_browser and os.path.isfile(ruta_browser):
        options.binary_location = ruta_browser
    for arg in ["--headless=new", "--disable-gpu", "--no-sandbox",
                "--disable-dev-shm-usage", "--window-size=1920,1080",
                "--disable-blink-features=AutomationControlled",
                "--disable-logging", "--log-level=3"]:
        options.add_argument(arg)
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    try:
        if WDM_OK and es_brave and ruta_browser:
            drv_path = _instalar_driver_brave(ruta_browser)
            if drv_path:
                driver = webdriver.Chrome(service=ChromeService(drv_path), options=options)
                driver.execute_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                return driver
        if WDM_OK:
            driver = webdriver.Chrome(
                service=ChromeService(ChromeDriverManager().install()), options=options)
            driver.execute_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            return driver
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception as e:
        print(f"  ❌ Driver headless: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════
# §10 ADAPTADORES DE SITIO
# ══════════════════════════════════════════════════════════════════════

class AdaptadorBase(ABC):
    nombre_sitio = "base"
    _PALABRAS_BLOQ: list = []

    def __init__(self, ctx: MangaCtx):
        self.ctx = ctx

    def _resolver_browser(self):
        """Devuelve (ruta_browser, es_brave) resolviendo auto-detección."""
        ruta = self.ctx.ruta_brave
        es_brave = self.ctx.usar_brave
        if es_brave:
            if not ruta or not os.path.isfile(ruta):
                ruta = _detectar_ruta_brave()
                if ruta:
                    self.ctx.ruta_brave = ruta
                else:
                    ruta = _detectar_ruta_chrome()
                    es_brave = False
        else:
            if not ruta or not os.path.isfile(ruta):
                ruta = self.ctx.ruta_chrome or _detectar_ruta_chrome()
        return ruta, es_brave

    def _nuevo_driver(self) -> "webdriver.Chrome | None":
        ruta, es_brave = self._resolver_browser()
        return _hacer_driver_headless(ruta, es_brave)

    @abstractmethod
    def escanear_capitulos(self) -> list:
        ...

    @abstractmethod
    def obtener_urls_imagenes(self, url_capitulo: str) -> list:
        ...


# ── 1. OLYMPUS ────────────────────────────────────────────────────────

class AdaptadorOlympus(AdaptadorBase):
    nombre_sitio = "olympus"
    _PALABRAS_BLOQ = [
        "logo","banner","icon","avatar","ads","button","ui","sprite",
        "thumb","cover","team","olympus-logo","discord","patreon","zzz-","zonaolympus",
    ]

    def escanear_capitulos(self) -> list:
        driver = self._nuevo_driver()
        if not driver:
            return []
        try:
            driver.get(self.ctx.url)
            time.sleep(3)
            slug      = self.ctx.url.rstrip("/").split("/")[-1]
            caps_dict = {}
            ult = 0; sin_cambio = 0
            print("  📜 Cargando capítulos con scroll...\n")
            while sin_cambio < 5:
                for _ in range(5):
                    driver.execute_script("window.scrollBy(0,1000);")
                    time.sleep(0.3)
                driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
                time.sleep(2)
                soup = BeautifulSoup(driver.page_source, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a.get("href",""); texto = a.get_text(strip=True)
                    if "/capitulo/" in href and slug in href:
                        m = re.search(r"cap[ií]tulo\s*([\d.]+)", texto, re.IGNORECASE)
                        if m:
                            num = float(m.group(1))
                            if num not in caps_dict:
                                caps_dict[num] = {
                                    "numero": num, "url": urljoin(self.ctx.url, href),
                                    "texto": texto,
                                    "numero_str": str(int(num)) if num==int(num) else str(num),
                                }
                cant = len(caps_dict)
                print(f"     📊 {cant}", end="")
                if cant == ult:
                    sin_cambio += 1; print(f" (sin cambios {sin_cambio}/5)")
                else:
                    sin_cambio = 0; print(f" (+{cant-ult} nuevos)"); ult = cant
            driver.quit()
            return sorted(caps_dict.values(), key=lambda x: x["numero"])
        except Exception as e:
            print(f"  ❌ Olympus error: {e}")
            try: driver.quit()
            except: pass
            return []

    def obtener_urls_imagenes(self, url_cap: str) -> list:
        urls = self._con_requests(url_cap)
        if not urls:
            print("  🔄 Fallback Selenium + scroll...")
            urls = self._con_selenium(url_cap)
        return urls

    def _filtrar(self, soup, base) -> list:
        out = []
        for tag in soup.find_all("img"):
            src = tag.get("data-src") or tag.get("data-lazy-src") or tag.get("src")
            if not src: continue
            u = urljoin(base, src)
            if any(p in u.lower() for p in self._PALABRAS_BLOQ): continue
            if not re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", u.lower()): continue
            if "/covers/" in u or "/teams/" in u: continue
            out.append(u)
        return out

    def _con_requests(self, url) -> list:
        for _ in range(3):
            try:
                r = requests.get(url, headers=self.ctx.headers, timeout=self.ctx.timeout)
                r.raise_for_status()
                return self._filtrar(BeautifulSoup(r.text, "html.parser"), url)
            except Exception:
                time.sleep(self.ctx.delay_reintento)
        return []

    def _con_selenium(self, url) -> list:
        driver = self._nuevo_driver()
        if not driver: return []
        try:
            driver.get(url); time.sleep(3)
            for _ in range(10):
                driver.execute_script("window.scrollBy(0,800);"); time.sleep(0.4)
            driver.execute_script("window.scrollTo(0,document.body.scrollHeight);"); time.sleep(2)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            driver.quit()
            return self._filtrar(soup, url)
        except Exception as e:
            print(f"  ⚠️   Selenium fallback: {e}")
            try: driver.quit()
            except: pass
            return []


# ── 2. IKIGAI ─────────────────────────────────────────────────────────

class AdaptadorIkigai(AdaptadorBase):
    nombre_sitio = "ikigai"
    _PALABRAS_BLOQ = ["logo","banner","icon","avatar","ads","button","ui",
                      "sprite","thumb","loading","placeholder","spinner"]

    def _driver_ikigai(self):
        """
        Driver headless con anti-detección reforzada para Cloudflare.
        Usa Brave o Chrome con auto-detección igual que el resto de adaptadores.
        """
        ruta, es_brave = self._resolver_browser()   # ← auto-detección correcta
        options = Options()
        if ruta and os.path.isfile(ruta):
            options.binary_location = ruta
        for arg in [
            "--headless=new","--disable-gpu","--no-sandbox",
            "--disable-dev-shm-usage","--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled",
            "--disable-logging","--log-level=3",
            f"user-agent={self.ctx.headers.get('User-Agent','')}",
        ]:
            options.add_argument(arg)
        options.add_experimental_option("excludeSwitches", ["enable-automation","enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)
        try:
            if WDM_OK and es_brave and ruta:
                drv_path = _instalar_driver_brave(ruta)
                if drv_path:
                    d = webdriver.Chrome(service=ChromeService(drv_path), options=options)
                    d.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                    return d
            if WDM_OK:
                d = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()),
                                      options=options)
            else:
                d = webdriver.Chrome(options=options)
            d.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            return d
        except Exception as e:
            print(f"  ❌ Driver Ikigai: {e}")
            return None

    def _generar_urls_paginas(self, url_base_antigua: str) -> list:
        """
        Dado la URL de la página más antigua (número más alto), genera
        automáticamente todas las URLs contando hacia atrás hasta ?pagina=1.

        Ejemplo: si url = '.../?pagina=4'  →  genera [pagina=4, pagina=3, pagina=2, pagina=1]

        Ikigai ordena: página 1 = más reciente, página N = más antigua.
        El código devuelve la lista ordenada de más antigua a más reciente
        para que los capítulos queden numerados correctamente al acumularlos.
        """
        # Detectar número de página en la URL
        m = re.search(r'[?&]pagina=(\d+)', url_base_antigua, re.IGNORECASE)
        if not m:
            # Sin parámetro de página: es la página 1 directamente
            return [url_base_antigua]

        n_max = int(m.group(1))
        base  = re.sub(r'([?&])pagina=\d+', '', url_base_antigua).rstrip('?&')
        sep   = '&' if '?' in base else '?'

        # Páginas de N hasta 1 (más antigua → más reciente)
        urls = []
        for p in range(n_max, 0, -1):
            urls.append(f"{base}{sep}pagina={p}")
        return urls

    def _pedir_url_pagina_antigua(self, cfg: dict) -> list:
        """
        Pide UNA SOLA URL: la de la página con el número más alto (la más antigua).
        El código genera automáticamente todas las demás hasta ?pagina=1.
        Guarda el resultado en config.json.
        """
        print("\n  ℹ️   Ikigai — solo necesitas la URL de la página MÁS ANTIGUA.")
        print("  💡  Ejemplo: https://visorikigai.xyz/series/mi-manga/?pagina=4")
        print("       El código generará automáticamente pagina=3, 2, 1.\n")
        url_antigua = input("  URL de la página más antigua (número más alto): ").strip()
        if not url_antigua:
            print("  ❌ URL vacía")
            return []

        urls = self._generar_urls_paginas(url_antigua)
        print(f"\n  ✅ Se usarán {len(urls)} página/s:")
        for u in urls:
            print(f"     • {u}")

        # Guardar solo la URL base en config (la más antigua); se regenera al escanear
        for m in cfg.get("mangas", []):
            if m["nombre"] == self.ctx.nombre:
                m["urls_paginas"] = [url_antigua]   # guardar solo la raíz
                break
        guardar_config(cfg)
        self.ctx.urls_paginas = [url_antigua]
        print(f"\n  💾 Guardado en config.json\n")
        return urls

    def escanear_capitulos(self) -> list:
        cfg = cargar_config()
        urls_base = self.ctx.urls_paginas

        # Si hay URLs guardadas, regenerar las páginas a partir de la más antigua
        if urls_base:
            # La primera guardada es siempre la más antigua (número de página más alto)
            urls_escanear = self._generar_urls_paginas(urls_base[0])
        else:
            # No hay nada guardado: pedir la URL al usuario
            urls_escanear = self._pedir_url_pagina_antigua(cfg)

        if not urls_escanear:
            print("  ❌ Sin URLs de páginas para escanear")
            return []

        print(f"\n  📋 Escaneando {len(urls_escanear)} página/s de Ikigai...")
        driver = self._driver_ikigai()
        if not driver:
            return []

        caps_dict = {}
        try:
            for url_pag in urls_escanear:
                print(f"\n  🔍 {url_pag}")
                driver.get(url_pag); time.sleep(8)
                driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
                time.sleep(3)
                driver.execute_script("window.scrollTo(0,0);"); time.sleep(2)
                soup = BeautifulSoup(driver.page_source, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a.get("href",""); texto = a.get_text(strip=True)
                    if "/capitulo/" in href or "capitulo" in href.lower():
                        m = re.search(r"cap[ií]tulo\s*([\d.]+)", texto, re.IGNORECASE) \
                            or re.search(r"\b([\d.]+)\b", texto)
                        if m:
                            num = float(m.group(1))
                            if num not in caps_dict:
                                caps_dict[num] = {
                                    "numero": num, "url": urljoin(url_pag, href),
                                    "texto": texto or f"Capítulo {num}",
                                    "numero_str": str(int(num)) if num==int(num) else str(num),
                                }
                print(f"     ✅ {len(caps_dict)} acumulados")
        finally:
            driver.quit()

        return sorted(caps_dict.values(), key=lambda x: x["numero"])

    def obtener_urls_imagenes(self, url_cap: str) -> list:
        driver = self._driver_ikigai()
        if not driver:
            return []
        try:
            driver.get(url_cap); time.sleep(6)
            driver.execute_script("window.scrollTo(0,document.body.scrollHeight);"); time.sleep(3)
            driver.execute_script("window.scrollTo(0,0);"); time.sleep(2)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            urls = []
            for tag in soup.find_all("img"):
                src = tag.get("data-src") or tag.get("data-lazy-src") or tag.get("src")
                if not src: continue
                u = urljoin(url_cap, src)
                if any(p in u.lower() for p in self._PALABRAS_BLOQ): continue
                if not re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", u.lower()): continue
                urls.append(u)
            return urls
        except Exception as e:
            print(f"  ❌ Ikigai imgs: {e}")
            return []
        finally:
            driver.quit()


# ── 3. MANHWAWEB ──────────────────────────────────────────────────────

class AdaptadorManhwasWeb(AdaptadorBase):
    """
    ManhwasWEB — abre el navegador en modo VISIBLE.
    El usuario hace clic en 'Ver más' manualmente si es necesario,
    luego presiona Enter en la terminal para que el código continúe.
    Así se evitan todos los problemas de detección del botón.
    """
    nombre_sitio = "manhwaweb"
    DEBUG_PORT   = 9222  # pre_filtrado NO se pone: el engine filtra por PIL dimensions
    _PALABRAS_BLOQ = [
        "logo","banner","icon","avatar","ads","button","ui","sprite",
        "thumb","loading","placeholder","spinner","discord","patreon",
    ]

    def _abrir_navegador_visible(self) -> bool:
        """Abre Brave o Chrome en modo visible apuntando a la URL del manga."""
        ruta, es_brave = self._resolver_browser()
        nombre_nav = "Brave" if es_brave else "Chrome"
        if not ruta or not os.path.isfile(ruta):
            print(f"  ❌ No se encontró {nombre_nav}. Verifica 'ruta_brave' en config.json")
            return False

        # Guardar ruta detectada
        if not self.ctx.ruta_brave and es_brave:
            self.ctx.ruta_brave = ruta
            try:
                cfg = cargar_config(); cfg["ruta_brave"] = ruta; guardar_config(cfg)
                print(f"  💾 Ruta de Brave guardada en config.json")
            except Exception:
                pass

        user_data = "brave-mw-debug" if es_brave else "chrome-mw-debug"
        try:
            subprocess.Popen([
                ruta,
                f"--remote-debugging-port={self.DEBUG_PORT}",
                f"--user-data-dir=C:\\{user_data}",
                self.ctx.url,
            ])
            time.sleep(4)
            print(f"\n  ✅ {nombre_nav} abierto")
            return True
        except Exception as e:
            print(f"  ❌ Error abriendo {nombre_nav}: {e}")
            return False

    def _conectar_debug(self):
        """Conecta Selenium al browser ya abierto en puerto debug."""
        ruta, es_brave = self._resolver_browser()
        options = Options()
        if ruta and os.path.isfile(ruta):
            options.binary_location = ruta
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.DEBUG_PORT}")
        try:
            if WDM_OK and es_brave and ruta:
                drv_path = _instalar_driver_brave(ruta)
                if drv_path:
                    return webdriver.Chrome(service=ChromeService(drv_path), options=options)
            if WDM_OK:
                return webdriver.Chrome(
                    service=ChromeService(ChromeDriverManager().install()), options=options)
            return webdriver.Chrome(options=options)
        except Exception as e:
            print(f"  ❌ Conexión debug: {e}")
            return None

    def escanear_capitulos(self) -> list:
        print("\n" + "═"*65)
        print("  📚 MANHWASWEB — DETECCIÓN DE CAPÍTULOS")
        print("═"*65)
        print(f"\n  🌐 Manga: {self.ctx.url}")
        print("\n  ┌─────────────────────────────────────────────────────┐")
        print("  │  INSTRUCCIONES:                                     │")
        print("  │  1. Se abrirá el navegador con tu manga             │")
        print("  │  2. Si aparece un botón 'Ver más', haz clic en él   │")
        print("  │     TODAS las veces que sea necesario               │")
        print("  │  3. Cuando veas TODOS los capítulos, vuelve aquí    │")
        print("  │     y presiona Enter para continuar                 │")
        print("  └─────────────────────────────────────────────────────┘\n")

        if not self._abrir_navegador_visible():
            return []

        input("  ⏸️   Presiona Enter cuando hayas cargado todos los capítulos...")

        print("\n  🔗 Conectando a navegador...")
        driver = self._conectar_debug()
        if not driver:
            return []
        print("  ✅ Conectado\n")

        try:
            # Scroll para asegurar que todo está cargado
            print("  📜 Cargando con scroll...")
            for _ in range(5):
                driver.execute_script("window.scrollBy(0,2000);"); time.sleep(0.4)
            driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            caps_dict = {}

            for a in soup.find_all("a", href=True):
                href = a.get("href",""); texto = a.get_text(strip=True)
                # Patrón ManhwasWEB: /leer/slug-NUM
                m_url = re.search(r'-(\d+\.?\d*)/?$', href)
                if m_url and ("/leer/" in href or "/capitulo/" in href):
                    try:
                        num = float(m_url.group(1))
                        if num not in caps_dict:
                            caps_dict[num] = {
                                "numero": num, "url": urljoin(self.ctx.url, href),
                                "texto": texto or f"Capítulo {m_url.group(1)}",
                                "numero_str": str(int(num)) if num==int(num) else str(num),
                            }
                    except ValueError:
                        pass
                # También buscar en texto
                if texto and "cap" in texto.lower():
                    mt = re.search(r'cap[ií]tulo\s*(\d+\.?\d*)', texto, re.IGNORECASE)
                    if mt and href:
                        try:
                            num = float(mt.group(1))
                            if num not in caps_dict:
                                caps_dict[num] = {
                                    "numero": num, "url": urljoin(self.ctx.url, href),
                                    "texto": texto,
                                    "numero_str": str(int(num)) if num==int(num) else str(num),
                                }
                        except ValueError:
                            pass

            driver.quit()
            lista = sorted(caps_dict.values(), key=lambda x: x["numero"])
            print(f"\n  ✅ {len(lista)} capítulos detectados")
            return lista

        except Exception as e:
            print(f"  ❌ ManhwasWEB error: {e}")
            try: driver.quit()
            except: pass
            return []

    def obtener_urls_imagenes(self, url_cap: str) -> list:
        """
        ManhwasWEB — usa el browser debug ya abierto para renderizar
        cada capítulo con Selenium (protección anti-bot).
        Filtros basados en atributos HTML + extensión, igual que el script
        original que funciona. El engine aplica después su propio filtro PIL.
        """
        driver = self._conectar_debug()
        if not driver:
            print("  ⚠️   No hay navegador debug abierto. Escanea primero (opción 1).")
            return []

        try:
            driver.get(url_cap)
            time.sleep(3)

            # Scroll para lazy-loading (exactamente como el original)
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.5)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            urls = []

            for img in soup.find_all("img"):
                src = (img.get("data-src") or img.get("data-lazy-src")
                       or img.get("src") or "")
                if not src:
                    continue
                u       = urljoin(url_cap, src)
                u_lower = u.lower()

                # ── Rechazar GIFs (siempre son emojis/favicons en ManhwasWEB) ──
                if u_lower.endswith(".gif") or ".gif?" in u_lower:
                    continue

                # ── Solo imágenes con extensión de imagen ──────────────────────
                if not re.search(r'\.(jpg|jpeg|png|webp)(\?|$)', u_lower):
                    continue

                # ── Patrones de URL que indican ruido ──────────────────────────
                patrones_rechazar = [
                    r'/icon[s]?/', r'/emoji/', r'/avatar/', r'/social/',
                    r'/thumbnail/', r'\.ico(\?|$)',
                    r'logo', r'banner', r'/ads/',
                ]
                if any(re.search(p, u_lower) for p in patrones_rechazar):
                    continue

                # ── Filtros por clase CSS del img ──────────────────────────────
                clases = " ".join(img.get("class", []))
                if any(c in clases.lower() for c in
                       ["icon","emoji","avatar","social","logo","ads"]):
                    continue

                # ── Filtros por atributos HTML width/height ────────────────────
                # (misma lógica que el script original ManhwasWEB.py)
                width_attr  = img.get("width")
                height_attr = img.get("height")
                if width_attr and height_attr:
                    try:
                        w = int(width_attr); h = int(height_attr)
                        # Rechazar iconos diminutos
                        if w <= 150 or h <= 150:
                            continue
                        ratio = w / h if h > 0 else 0
                        # Banners horizontales
                        if ratio > 1.3:
                            continue
                        # Iconos cuadrados pequeños
                        if w == h and w <= 200:
                            continue
                        # Imágenes demasiado pequeñas para ser páginas de manga
                        if w < 400 or h < 400:
                            continue
                    except (ValueError, TypeError):
                        pass

                urls.append(u)

            return urls

        except Exception as e:
            print(f"  ❌ ManhwasWEB imgs: {e}")
            return []
        # NO cerrar el driver: el browser debug debe quedar abierto


# ── 4. DRAGON TRANSLATION ─────────────────────────────────────────────

class AdaptadorDragon(AdaptadorBase):
    """
    Dragon Translation — requests para la lista, Selenium para imágenes.
    Las imágenes se pre-verifican descargándolas, el engine no re-filtra.
    """
    nombre_sitio = "dragon"
    pre_filtrado = True    # imágenes ya descargadas y verificadas, engine no re-filtra
    _PALABRAS_BLOQ = ["logo","banner","icon","avatar","ads","button","ui",
                      "sprite","thumb","share","social","footer"]

    def escanear_capitulos(self) -> list:
        print(f"  🌐 Cargando: {self.ctx.url}")
        try:
            r = requests.get(self.ctx.url, headers=self.ctx.headers, timeout=self.ctx.timeout)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            caps_dict = {}
            for a in soup.find_all("a", href=True):
                href = a.get("href",""); texto = a.get_text(strip=True)
                if "/capitulo-" in href and self.ctx.url.replace("/","") in href.replace("/",""):
                    url_full = urljoin(self.ctx.url, href)
                    m = re.search(r'cap[ií]tulo\s*([\d.]+)', texto, re.IGNORECASE)
                    if not m:
                        m = re.search(r'capitulo-([\d.-]+)', href)
                        if m:
                            num_str = m.group(1).replace('-','.', 1)
                            try:
                                num = float(num_str.split('-')[0])
                            except Exception:
                                continue
                        else:
                            continue
                    else:
                        num = float(m.group(1))
                    if num not in caps_dict:
                        caps_dict[num] = {
                            "numero": num, "url": url_full,
                            "texto": texto or f"Capítulo {num}",
                            "numero_str": str(int(num)) if num==int(num) else str(num),
                        }
            lista = sorted(caps_dict.values(), key=lambda x: x["numero"])
            print(f"  ✅ {len(lista)} capítulos")
            return lista
        except Exception as e:
            print(f"  ❌ Dragon Translation: {e}")
            return []

    def obtener_urls_imagenes(self, url_cap: str) -> list:
        """
        Dragon Translation: usa _nuevo_driver() igual que los otros adaptadores,
        lo que garantiza que detecta Brave/Chrome correctamente.
        Luego requests verifica cada imagen candidata (igual que el script original).
        """
        driver = self._nuevo_driver()
        if not driver:
            print("  ❌ Dragon: no se pudo iniciar el driver. "
                  "Verifica 'ruta_brave'/'ruta_chrome' en config.json")
            return []

        soup = None
        try:
            driver.get(url_cap)
            time.sleep(5)

            # Scroll igual que el original
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, 1000);")
                time.sleep(1)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)

            soup = BeautifulSoup(driver.page_source, "html.parser")

        except Exception as e:
            print(f"  ❌ Dragon Selenium: {e}")
            return []
        finally:
            try: driver.quit()
            except: pass

        if soup is None:
            return []

        # Filtros exactos del script original
        filtros_excluir   = ["bcfd4689-2471", "default_profile", "logo-96",
                             "logo-dragon", "avatar", "logo"]
        tamaños_miniatura = ["-75x106", "-193x278", "-150x150", "-50x50"]

        candidatos = []
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
            if not src:
                continue
            img_url = urljoin(url_cap, src)

            if any(x in img_url.lower() for x in filtros_excluir):
                continue
            if any(x in img_url for x in tamaños_miniatura):
                continue

            # Verificar descargando y midiendo (igual que el original)
            try:
                r = requests.get(img_url, headers=self.ctx.headers, timeout=self.ctx.timeout)
                r.raise_for_status()
                imagen = Image.open(BytesIO(r.content))
                ancho, alto = imagen.size

                if ancho < 150 or alto < 150:
                    continue
                ratio = ancho / alto if alto > 0 else 0
                if ratio > 4:
                    continue
                if 0.8 < ratio < 1.2 and ancho < 500 and alto < 500:
                    continue

                candidatos.append(img_url)
            except Exception:
                continue

        return candidatos


# ── 5. MANHWA LATINO ──────────────────────────────────────────────────

class AdaptadorManhwaLatino(AdaptadorBase):
    """
    Manhwa Latino — usa DrissionPage para bypass de Cloudflare.
    Si DrissionPage no está, avisa al usuario.
    """
    nombre_sitio = "manhwalatico"
    _PALABRAS_BLOQ = ["logo","banner","avatar","favicon","ads"]

    def _verificar_dp(self) -> bool:
        if not DP_OK:
            print("\n  ❌ DrissionPage no instalado.")
            print("  💡 Ejecuta: pip install DrissionPage")
            return False
        return True

    def _iniciar_dp(self):
        ruta = _detectar_cualquier_browser()
        if not ruta:
            ruta = input("  Ruta al navegador (brave.exe / chrome.exe): ").strip().strip('"')
        opts = ChromiumOptions()
        opts.set_browser_path(ruta)
        opts.set_argument("--disable-blink-features=AutomationControlled")
        opts.set_argument("--no-sandbox")
        opts.set_argument("--disable-dev-shm-usage")
        return ChromiumPage(addr_or_opts=opts)

    def _esperar_cloudflare(self, browser, url, espera=12):
        browser.get(url)
        for _ in range(espera):
            time.sleep(1)
            titulo = browser.title or ""
            if not any(k in titulo for k in ["Verificando","Just a moment","moment"]):
                break
        time.sleep(1)

    def escanear_capitulos(self) -> list:
        if not self._verificar_dp():
            return []
        print(f"  🌐 Cloudflare bypass para: {self.ctx.url}")
        browser = self._iniciar_dp()
        try:
            self._esperar_cloudflare(browser, self.ctx.url)
            selectores = [
                "a[href*='/capitulo-']", ".chapter-item a",
                ".listing-chapters_wrap a", "ul.row-content-chapter li a",
                ".wp-manga-chapter a",
            ]
            elementos = []
            for sel in selectores:
                elementos = browser.eles(f"css:{sel}")
                if elementos: break
            if not elementos:
                elementos = [e for e in browser.eles("css:a")
                             if 'capitulo-' in (e.attr('href') or '')]

            caps_dict = {}
            for el in elementos:
                href = el.attr('href') or ''
                if 'capitulo-' not in href: continue
                if not href.startswith('http'):
                    href = urljoin(self.ctx.url, href)
                m = re.search(r'capitulo-(\d+)(?:-(\d+))?', href)
                if not m: continue
                ent = int(m.group(1)); dec = int(m.group(2)) if m.group(2) else 0
                num = float(f"{ent}.{dec}") if dec else float(ent)
                if num not in caps_dict:
                    caps_dict[num] = {
                        "numero": num, "url": href,
                        "texto": el.text.strip() or f"Capítulo {num}",
                        "numero_str": str(int(num)) if num==int(num) else str(num),
                    }

            # Nombre del manga desde el título
            try:
                t = browser.ele("css:.post-title h1") or browser.ele("css:h1")
                self.ctx.nombre = re.sub(r'[<>:"/\\|?*]','',t.text).strip()
            except Exception:
                pass

            browser.quit()
            lista = sorted(caps_dict.values(), key=lambda x: x["numero"])
            print(f"  ✅ {len(lista)} capítulos")
            return lista
        except Exception as e:
            print(f"  ❌ Manhwa Latino: {e}")
            try: browser.quit()
            except: pass
            return []

    def obtener_urls_imagenes(self, url_cap: str) -> list:
        if not self._verificar_dp():
            return []
        browser = self._iniciar_dp()
        try:
            self._esperar_cloudflare(browser, url_cap, espera=6)
            browser.scroll.to_bottom(); time.sleep(2)
            browser.scroll.to_top(); time.sleep(1)
            selectores = [
                "css:.reading-content img", "css:.page-break img",
                "css:#readerarea img", "css:.chapter-content img",
                "css:img.wp-manga-chapter-img",
            ]
            imgs = []
            for sel in selectores:
                imgs = browser.eles(sel)
                if imgs: break
            if not imgs:
                imgs = browser.eles("css:img")
            urls = []
            for img in imgs:
                src = (img.attr('src') or img.attr('data-src')
                       or img.attr('data-lazy-src') or '').strip()
                if not src or 'http' not in src: continue
                if not any(e in src.lower() for e in ['.jpg','.jpeg','.png','.webp','.gif']): continue
                if any(s in src.lower() for s in self._PALABRAS_BLOQ): continue
                urls.append(src)
            browser.quit()
            return list(dict.fromkeys(urls))
        except Exception as e:
            print(f"  ❌ Manhwa Latino imgs: {e}")
            try: browser.quit()
            except: pass
            return []


# ── Fábrica ──────────────────────────────────────────────────────────

def crear_adaptador(ctx: MangaCtx) -> AdaptadorBase:
    mapa = {
        "olympus":      AdaptadorOlympus,
        "ikigai":       AdaptadorIkigai,
        "manhwaweb":    AdaptadorManhwasWeb,
        "dragon":       AdaptadorDragon,
        "manhwalatico": AdaptadorManhwaLatino,
    }
    return mapa.get(ctx.sitio, AdaptadorOlympus)(ctx)

# ══════════════════════════════════════════════════════════════════════
# §11 DESCARGA DE IMÁGENES (engine genérico)
# ══════════════════════════════════════════════════════════════════════

def _descargar_img(url, ctx, detector, speed):
    """
    Descarga una imagen con reintentos.
    Solo registra UN error en el detector si la imagen falla todos los reintentos,
    y UN éxito si descarga bien — así las descargas paralelas no multiplican
    artificialmente el contador de errores consecutivos.
    """
    ultimo_exc = None
    for intento in range(1, ctx.max_reintentos + 1):
        detector.esperar_si_pausado()
        try:
            r = requests.get(url, headers=ctx.headers, timeout=ctx.timeout)
            r.raise_for_status()
            detector.registrar_exito()
            speed.add(len(r.content))
            return r.content, True
        except Exception as e:
            ultimo_exc = e
            if intento < ctx.max_reintentos:
                time.sleep(ctx.delay_reintento)
    # Solo UN error al agotar todos los reintentos
    detector.registrar_error()
    return None, False


def _worker(args):
    idx, url, ctx, detector, speed = args
    datos, ok = _descargar_img(url, ctx, detector, speed)
    return idx, url, datos, ok


def _descargar_paralelo(candidatos, ctx, detector, speed, barra=None) -> dict:
    resultados = {}
    lock       = threading.Lock()
    timeout_h  = ctx.timeout * ctx.max_reintentos + 30
    args_list  = [(idx, url, ctx, detector, speed) for idx, url in candidatos]
    with ThreadPoolExecutor(max_workers=ctx.num_hilos) as pool:
        futuros = {pool.submit(_worker, a): a[0] for a in args_list}
        for fut in as_completed(futuros, timeout=timeout_h + 60):
            idx_f = futuros[fut]
            try:
                idx, url, datos, ok = fut.result(timeout=timeout_h)
            except (FutTimeoutError, Exception):
                idx, url, datos, ok = idx_f, "", None, False
            with lock:
                resultados[idx] = (url, datos, ok)
            if barra:
                barra.set_postfix(MB=f"{speed.total_mb:.1f}", vel=f"{speed.mbps}MB/s")
                barra.update(1)
    return resultados


def verificar_integridad(ruta):
    try:
        img = Image.open(ruta); img.load()
        return True, "OK"
    except Exception as e:
        return False, str(e)


def _pdf_es_valido(ruta_pdf) -> bool:
    try:
        if os.path.getsize(ruta_pdf) < 1024: return False
        with open(ruta_pdf, "rb") as f: return f.read(4) == b"%PDF"
    except Exception:
        return False

# ══════════════════════════════════════════════════════════════════════
# §12 DESCARGA DE UN CAPÍTULO  (filtro adaptativo v2)
# ══════════════════════════════════════════════════════════════════════

# ── Helpers de filtro ────────────────────────────────────────────────

def _calcular_perfil_capitulo(imgs_ok: dict) -> dict:
    """
    Analiza el conjunto de imágenes descargadas y devuelve el 'perfil'
    del capítulo: tipo de contenido, ancho dominante, alto mediano, etc.

    Devuelve un dict con:
      tipo          : "webtoon" | "manga" | "mixto"
      ancho_med     : mediana de anchos
      alto_med      : mediana de altos
      ratio_med     : mediana de ratios ancho/alto
      ancho_dom     : ancho más frecuente (moda)
      anchos_unicos : set de anchos distintos
    """
    if not imgs_ok:
        return {"tipo": "mixto", "ancho_med": 0, "alto_med": 0,
                "ratio_med": 0, "ancho_dom": 0, "anchos_unicos": set()}

    anchos = sorted(v["ancho"] for v in imgs_ok.values())
    altos  = sorted(v["alto"]  for v in imgs_ok.values())
    ratios = sorted(v["ancho"] / v["alto"] for v in imgs_ok.values() if v["alto"] > 0)

    def mediana(lst):
        n = len(lst)
        return lst[n // 2] if n else 0

    ancho_med  = mediana(anchos)
    alto_med   = mediana(altos)
    ratio_med  = mediana(ratios)
    ancho_dom  = Counter(anchos).most_common(1)[0][0] if anchos else 0
    anchos_uni = set(anchos)

    # Webtoon: páginas muy verticales (ratio < 0.4) predominan
    n_webtoon = sum(1 for r in ratios if r < 0.4)
    n_manga   = sum(1 for r in ratios if r >= 0.4)
    tipo = "webtoon" if n_webtoon > n_manga else ("manga" if n_manga > n_webtoon else "mixto")

    return {
        "tipo": tipo,
        "ancho_med": ancho_med,
        "alto_med":  alto_med,
        "ratio_med": ratio_med,
        "ancho_dom": ancho_dom,
        "anchos_unicos": anchos_uni,
    }


def _deduplicar_urls(urls: list) -> list:
    """Elimina duplicados de URL manteniendo orden (para lazy-loading triple)."""
    visto = set(); result = []
    for u in urls:
        if u not in visto:
            visto.add(u); result.append(u)
    return result


def _filtrar_imagen(ancho: int, alto: int, url: str, perfil: dict, ctx: MangaCtx,
                    modo_relajado: bool = False) -> tuple:
    """
    Decide si una imagen pasa el filtro.
    Devuelve (pasa: bool, motivo: str).

    Lógica:
      1. Tamaño absoluto mínimo (configurable por sitio).
      2. Ratio extremo (banner horizontal o píxel tracker).
      3. Icono/logo cuadrado pequeño.
      4. Comparación contra el perfil del capítulo (ancho dominante).
         — Si el tipo es webtoon, se usan márgenes más amplios.
         — Si el tipo es manga, se usan los márgenes normales.
      5. Palabras clave en la URL (siempre activo, no se relaja).
    En modo_relajado se saltan los filtros de tamaño y perfil,
    solo se aplican los de ratio extremo y palabras clave.
    """
    ratio = ancho / alto if alto > 0 else 0

    # ── Palabras clave de ruido — siempre activas ──────────────────
    fname    = url.split("/")[-1].lower()
    url_low  = url.lower()
    ruido_fn = ["banner", "logo-", "zzz-", "promo", "discord", "patreon",
                "default_profile", "bcfd4689", "logo-96", "logo-dragon"]
    ruido_url = ["storage/teams", "storage/comics/covers", "/ads/", "/icon/",
                 "/avatar/", "/social/", "/emoji/"]
    if any(x in fname for x in ruido_fn):
        return False, f"nombre-ruido:{fname[:30]}"
    if any(x in url_low for x in ruido_url):
        return False, f"url-ruido"

    if modo_relajado:
        # En fallback solo rechazamos ratio absurdo y ruido de URL (ya verificado)
        if ratio > ctx.filtro_ratio_max * 1.5:
            return False, f"ratio-extremo:{ratio:.2f}"
        return True, "ok-relajado"

    # ── Tamaño absoluto mínimo ─────────────────────────────────────
    if ancho < ctx.filtro_ancho_min_abs or alto < ctx.filtro_alto_min_abs:
        return False, f"muy-pequeña:{ancho}x{alto}"

    # ── Ratio horizontal extremo (banner) ─────────────────────────
    if ratio > ctx.filtro_ratio_max:
        return False, f"banner:{ratio:.2f}"

    # ── Icono/logo cuadrado pequeño ────────────────────────────────
    if 0.8 < ratio < 1.2 and ancho < ctx.filtro_cuadrado_max_px and alto < ctx.filtro_cuadrado_max_px:
        return False, f"icono-cuadrado:{ancho}x{alto}"

    # ── Comparación con perfil del capítulo ───────────────────────
    ancho_dom = perfil.get("ancho_dom", 0)
    if ancho_dom > 0 and alto > ctx.filtro_alto_min_abs:
        tol = ctx.filtro_tolerancia_ancho / 100.0
        # Para webtoon usamos tolerancia más amplia porque páginas del mismo cap
        # pueden variar más de ancho
        if perfil.get("tipo") == "webtoon":
            tol = max(tol, 0.35)
        margen_min = ancho_dom * (1.0 - tol)
        margen_max = ancho_dom * (1.0 + tol)
        if ancho < margen_min or ancho > margen_max:
            return False, f"fuera-perfil:{ancho}px(dom={ancho_dom}±{int(tol*100)}%)"

    return True, "ok"


def descargar_capitulo(url, numero, con_filtro, carpeta_destino,
                        reg, carp_reg, ctx, logger, detector,
                        adaptador: AdaptadorBase) -> dict:
    num_str = str(int(numero)) if numero == int(numero) else str(numero)
    vacio   = {"estado": "fallido", "guardadas": 0,
               "fallidas_desc": 0, "rechazadas": 0, "corrompidas": 0}

    if cap_ya_descargado(reg, carp_reg, numero, carpeta_destino):
        n_r = reg[carp_reg][str(numero)]["imagenes"]
        print(f"  ⏭️   Cap {num_str}  [registro OK — {n_r} imgs]")
        logger.inicio_cap(numero, url)
        logger.fin_cap(numero, "omitido", n_r, 0, 0, 0)
        return {"estado": "omitido", "guardadas": n_r,
                "fallidas_desc": 0, "rechazadas": 0, "corrompidas": 0}

    logger.inicio_cap(numero, url)
    carpeta = os.path.join(carpeta_destino, f"Capitulo_{num_str}")
    os.makedirs(carpeta, exist_ok=True)

    # Resetear el detector de bloqueo entre capítulos para que los errores
    # del capítulo anterior no contaminen el conteo del siguiente
    detector.reset()

    print(f"\n{'═'*65}")
    print(f"  📖  CAPÍTULO {num_str}  [{_nombre_sitio(ctx.sitio)}]")
    print(f"{'─'*65}")
    print(f"  🔗 {url}")

    candidatos_raw = adaptador.obtener_urls_imagenes(url)
    candidatos     = _deduplicar_urls(candidatos_raw)
    n_dup = len(candidatos_raw) - len(candidatos)
    print(f"  🔎 Candidatos: {len(candidatos)}"
          + (f"  (🗑️ {n_dup} duplicados eliminados)" if n_dup else ""))

    if not candidatos:
        print("  ❌ Sin imágenes candidatas")
        logger.fin_cap(numero, "fallido", 0, 0, 0, 0)
        return vacio

    speed = SpeedTracker()
    barra_dl = None
    if TQDM_OK:
        barra_dl = tqdm(total=len(candidatos), unit="img", desc=f"  Cap {num_str}",
                        ncols=70, bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}] {postfix}")

    resultados = _descargar_paralelo(list(enumerate(candidatos)), ctx, detector, speed, barra_dl)
    if barra_dl: barra_dl.close()
    print(f"\n  📶 {speed.mbps} MB/s  │  {speed.total_mb} MB")

    imgs_ok: dict = {}; fallidas_desc = 0
    for idx in sorted(resultados):
        url_img, datos, ok = resultados[idx]
        if not ok or datos is None:
            fallidas_desc += 1
            logger.img_error(numero, idx+1, url_img, f"fallo tras {ctx.max_reintentos} reintentos")
            continue
        try:
            imagen = Image.open(BytesIO(datos))
            ancho, alto = imagen.size
            imgs_ok[idx] = {"url": url_img, "bytes": datos,
                             "imagen": imagen, "ancho": ancho, "alto": alto}
        except Exception as e:
            fallidas_desc += 1
            logger.img_error(numero, idx+1, url_img, f"PIL: {e}")

    if not imgs_ok:
        print("  ❌ Ninguna imagen válida")
        logger.fin_cap(numero, "fallido", 0, fallidas_desc, 0, 0)
        return {**vacio, "fallidas_desc": fallidas_desc}

    # ── Calcular perfil del capítulo ──────────────────────────────────
    aplicar_filtro_engine = con_filtro and not getattr(adaptador, "pre_filtrado", False)
    perfil = _calcular_perfil_capitulo(imgs_ok) if aplicar_filtro_engine else {}
    if aplicar_filtro_engine:
        print(f"  📐 Perfil: {perfil['tipo'].upper()}  "
              f"ancho_dom={perfil['ancho_dom']}px  "
              f"ratio_med={perfil['ratio_med']:.2f}  "
              f"({len(perfil['anchos_unicos'])} anchos distintos)")

    # ── Primera pasada con filtro normal ─────────────────────────────
    guardadas = rechazadas = corrompidas = 0; contador = 1
    rechazadas_meta: list = []   # guardamos metadatos para posible fallback

    print(f"\n  {'─'*62}")
    print(f"  {'#':>4}  {'Resolución':^13}  Estado")
    print(f"  {'─'*62}")

    imgs_guardadas_paso1: dict = {}

    for idx in sorted(imgs_ok):
        meta    = imgs_ok[idx]
        img_url = meta["url"]; datos = meta["bytes"]
        ancho   = meta["ancho"]; alto = meta["alto"]
        pref    = f"  {contador:>4}  {ancho}x{alto:<7}  "

        if aplicar_filtro_engine:
            pasa, motivo = _filtrar_imagen(ancho, alto, img_url, perfil, ctx)
            if not pasa:
                print(f"{pref}❌ Rechazada ({motivo})")
                rechazadas += 1
                rechazadas_meta.append((idx, meta, motivo))
                continue

        try:
            ext  = img_url.split(".")[-1].split("?")[0][:4]
            if ext not in ["jpg","jpeg","png","webp","gif"]: ext = "webp"
            nombre = f"{contador:03d}.{ext}"
            ruta   = os.path.join(carpeta, nombre)
            with open(ruta, "wb") as f: f.write(datos)
            if ctx.verificar_integridad:
                ok_i, msg_i = verificar_integridad(ruta)
                if not ok_i:
                    print(f"{pref}⚠️   CORROMPIDA: {msg_i}")
                    corrompidas += 1; logger.img_corrompida(numero, contador, ruta, msg_i)
                else:
                    print(f"{pref}✅ OK → {nombre}")
            else:
                print(f"{pref}✅ OK → {nombre}")
            imgs_guardadas_paso1[idx] = nombre
            guardadas += 1; contador += 1
        except Exception as e:
            print(f"{pref}❌ {e}"); logger.img_error(numero, contador, img_url, str(e))

    # ── Fallback: si quedan muy pocas imágenes, relajar y reintentar rechazadas ──
    if (aplicar_filtro_engine and rechazadas_meta
            and guardadas < ctx.filtro_min_imgs_fallback):
        print(f"\n  ⚠️   Solo {guardadas} imgs tras filtro — activando modo RELAJADO "
              f"({len(rechazadas_meta)} rechazadas a revisar)...")
        recuperadas = 0
        for idx, meta, motivo_orig in rechazadas_meta:
            img_url = meta["url"]; datos = meta["bytes"]
            ancho   = meta["ancho"]; alto = meta["alto"]
            pref    = f"  {contador:>4}  {ancho}x{alto:<7}  "
            pasa, motivo = _filtrar_imagen(ancho, alto, img_url, perfil, ctx,
                                           modo_relajado=True)
            if not pasa:
                print(f"{pref}❌ Sigue rechazada ({motivo})")
                continue
            try:
                ext  = img_url.split(".")[-1].split("?")[0][:4]
                if ext not in ["jpg","jpeg","png","webp","gif"]: ext = "webp"
                nombre = f"{contador:03d}.{ext}"
                ruta   = os.path.join(carpeta, nombre)
                with open(ruta, "wb") as f: f.write(datos)
                if ctx.verificar_integridad:
                    ok_i, msg_i = verificar_integridad(ruta)
                    if not ok_i:
                        print(f"{pref}⚠️   CORROMPIDA: {msg_i}")
                        corrompidas += 1
                        continue
                print(f"{pref}♻️  Recuperada (orig: {motivo_orig}) → {nombre}")
                guardadas += 1; contador += 1; rechazadas -= 1; recuperadas += 1
            except Exception as e:
                print(f"{pref}❌ {e}")
        if recuperadas:
            print(f"  ♻️   Recuperadas en fallback: {recuperadas}")

    print(f"  {'─'*62}")

    prom = promedio_paginas(reg, carp_reg)
    if prom > 3 and 0 < guardadas < prom * 0.5:
        print(f"\n  ⚠️   Solo {guardadas} págs (promedio: {prom:.1f}) — posiblemente incompleto")

    if guardadas == 0:
        estado = "fallido"; print(f"\n  ❌ Cap {num_str}: sin imágenes guardadas")
    else:
        estado = "exitoso"
        extras = []
        if fallidas_desc: extras.append(f"⚠️ {fallidas_desc} sin descargar")
        if corrompidas:   extras.append(f"⚠️ {corrompidas} corrompidas")
        if rechazadas:    extras.append(f"🚫 {rechazadas} filtradas")
        print(f"\n  ✅ Cap {num_str}: {guardadas} imgs" + ("  |  "+"  ".join(extras) if extras else ""))

    logger.fin_cap(numero, estado, guardadas, fallidas_desc, rechazadas, corrompidas)
    if estado == "exitoso" and corrompidas == 0 and fallidas_desc == 0:
        marcar_completado(ctx, reg, carp_reg, numero, guardadas)
    return {"estado": estado, "guardadas": guardadas,
            "fallidas_desc": fallidas_desc, "rechazadas": rechazadas, "corrompidas": corrompidas}

# ══════════════════════════════════════════════════════════════════════
# §13 COMPRESIÓN INTELIGENTE + PDF
# ══════════════════════════════════════════════════════════════════════

def _comprimir_img(img, ctx, fmt_orig=""):
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255,255,255)); bg.paste(img, mask=img.split()[3]); img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if not ctx.comp_activada: return img
    w, h = img.size
    necesita = w > ctx.comp_max_ancho or fmt_orig.upper() not in ("JPEG","JPG")
    if not necesita: return img
    if w > ctx.comp_max_ancho:
        h = int(h * ctx.comp_max_ancho / w); img = img.resize((ctx.comp_max_ancho, h), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=ctx.comp_calidad, optimize=True)
    buf.seek(0); result = Image.open(buf); result.load()
    return result


def convertir_a_pdf(ctx: MangaCtx):
    print("\n" + "═"*70)
    print("  📄  CONVERTIDOR DE IMÁGENES A PDF")
    print("═"*70)
    if ctx.comp_activada:
        print(f"  🗜️   Compresión: JPEG {ctx.comp_calidad}% / ≤{ctx.comp_max_ancho}px\n")

    print("  ¿Desde qué carpeta?\n")
    print("  1. Capitulos_a_Convertir")
    print("  2. Capitulos_por_Rango")
    print("  3. Capitulos_sin_Filtro")
    print("  4. Cancelar\n")

    mapa = {
        "1": ("Capitulos_a_Convertir", ctx.carpeta_convertir),
        "2": ("Capitulos_por_Rango",   ctx.carpeta_rango),
        "3": ("Capitulos_sin_Filtro",  ctx.carpeta_sin_filtro),
    }
    op = input("  ➡️   Elige (1-4): ").strip()
    if op not in mapa:
        print("  ℹ️   Operación cancelada")
        return

    nombre_orig, carpeta_orig = mapa[op]
    if not os.path.exists(carpeta_orig):
        print(f"\n  ℹ️   La carpeta '{nombre_orig}' no existe todavía.")
        print("  💡  Descarga capítulos primero con la opción correspondiente.")
        return

    carpetas = sorted(
        [d for d in os.listdir(carpeta_orig)
         if os.path.isdir(os.path.join(carpeta_orig, d)) and d.startswith("Capitulo_")],
        key=lambda x: float(x.replace("Capitulo_",""))
    )
    if not carpetas:
        print(f"\n  ℹ️   No hay capítulos descargados en '{nombre_orig}'.")
        print("  💡  Descarga capítulos primero con la opción correspondiente.")
        return

    print(f"\n  📁 {nombre_orig}  ({len(carpetas)} cap/s)")
    sel = input("\n  Capítulos (ENTER = todos): ").strip()
    if sel:
        nums_f = {float(n) for n in parsear_seleccion(sel)}
        carpetas = [c for c in carpetas if float(c.replace("Capitulo_","")) in nums_f]
    if not carpetas:
        print("  ⚠️   Sin coincidencias con la selección")
        return
    if input(f"  ¿Convertir {len(carpetas)} cap/s? (s/n): ").lower() != "s":
        return

    exitosos = omitidos = errores = total_pags = 0
    peso_a = peso_d = 0.0; caps_error = []
    barra = tqdm(total=len(carpetas), unit="cap", desc="  Convirtiendo", ncols=65,
                 bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]") if TQDM_OK else None

    for c_nombre in carpetas:
        numero   = c_nombre.replace("Capitulo_","")
        c_ruta   = os.path.join(carpeta_orig, c_nombre)
        pdf_path = os.path.join(ctx.carpeta_pdf, f"Capitulo_{numero}.pdf")
        if barra: barra.set_postfix(cap=numero)
        if os.path.exists(pdf_path):
            omitidos += 1
            if barra: barra.update(1)
            continue
        try:
            archivos = sorted([f for f in os.listdir(c_ruta)
                               if f.lower().endswith((".jpg",".jpeg",".png",".webp",".gif",".bmp"))])
            if not archivos:
                errores += 1; caps_error.append(numero)
                if barra: barra.update(1)
                continue
            imgs_pdf = []
            for arch in archivos:
                r_arch = os.path.join(c_ruta, arch)
                peso_a += os.path.getsize(r_arch) / (1024*1024)
                img = Image.open(r_arch)
                img = _comprimir_img(img, ctx, img.format or "")
                imgs_pdf.append(img)
            if len(imgs_pdf) > 1:
                imgs_pdf[0].save(pdf_path, "PDF", save_all=True, append_images=imgs_pdf[1:])
            else:
                imgs_pdf[0].save(pdf_path, "PDF")
            peso_d += os.path.getsize(pdf_path) / (1024*1024)
            exitosos += 1; total_pags += len(imgs_pdf)
        except Exception as e:
            errores += 1; caps_error.append(numero)
        if barra: barra.update(1)

    if barra: barra.close()
    print(f"\n  ✅ {exitosos}  ⏭️ {omitidos}  ❌ {errores}  📄 {total_pags} págs")
    if ctx.comp_activada and peso_a > 0:
        print(f"  🗜️   {peso_a:.1f} MB → {peso_d:.1f} MB  (ahorro: {(1-peso_d/peso_a)*100:.1f}%)")
    if caps_error:
        print(f"  ⚠️   Error en: {', '.join(f'Cap {c}' for c in caps_error)}")
    print(f"  📁 {ctx.carpeta_pdf}\n")

# ══════════════════════════════════════════════════════════════════════
# §14 MENÚ + BANNER
# ══════════════════════════════════════════════════════════════════════

def _sep(c="═", n=70):
    return c * n


def mostrar_banner(ctx: MangaCtx):
    icono = _icono_sitio(ctx.sitio)
    nav   = "🦁 Brave" if ctx.usar_brave else "🌐 Chrome"
    comp  = f"✅ {ctx.comp_calidad}%/≤{ctx.comp_max_ancho}px" if ctx.comp_activada else "❌"
    print("\n" + _sep())
    print(f"  {icono}  MANGA DOWNLOADER UNIFICADO  v6.0")
    print(_sep())
    print(f"  📚 Manga    : {ctx.nombre}")
    print(f"  🌐 Sitio    : {_nombre_sitio(ctx.sitio)}")
    print(f"  🔧 Nav      : {nav}  │  ⚡ Hilos: {ctx.num_hilos}  │  🔁 Reintentos: {ctx.max_reintentos}")
    print(f"  🗜️  Comp    : {comp}  │  🔍 Integridad: {'✅' if ctx.verificar_integridad else '❌'}")
    print(f"  📁 Carpeta  : {ctx.carpeta_manga}")
    print(_sep() + "\n")


def mostrar_menu_manga() -> str:
    print("\n" + _sep("─"))
    print("  📋  MENÚ PRINCIPAL")
    print(_sep("─"))
    print("  1   Detectar y listar todos los capítulos")
    print("  2   Detectar capítulos NUEVOS (vs cache)")
    print("  3   Descargar por rango")
    print("  4   Descargar selección manual")
    print("  5   Descargar ÚLTIMO capítulo")
    print("  6   Descargar sin filtro")
    print("  7   Convertir imágenes a PDF")
    print("  8   Relanzar capítulos fallidos")
    print("  9   Limpiar carpetas (imágenes ya convertidas)")
    print("  10  Ver registro de descargas")
    print("  11  Exportar registro a TXT")
    print("  0   ← Volver al selector de manga")
    print("  Q   ❌ Salir del programa")
    print(_sep("─"))
    return input("  ➡️   Opción: ").strip().upper()


def mostrar_menu_multimanga(cfg: dict) -> "dict | None | str":
    """
    Devuelve:
      dict  → manga seleccionado
      None  → salir del programa
      "add" → agregar nuevo
    """
    mangas     = cfg.get("mangas", [])
    ultimo_idx = cfg.get("ultimo_manga", 0)

    print("\n" + _sep())
    print("  🎌  MANGA DOWNLOADER UNIFICADO  v6.0")
    print(_sep())
    print("\n  SITIOS DISPONIBLES:\n")
    for k, v in SITIOS.items():
        print(f"     {k}.  {v['icono']}  {v['nombre']}")
    print()

    if mangas:
        print("  📚 MANGAS CONFIGURADOS:\n")
        for i, m in enumerate(mangas, 1):
            icono  = _icono_sitio(m.get("sitio",""))
            estado = "✅" if m.get("activo", True) else "⏸️"
            ctx_tmp = crear_ctx(m, cfg)
            n_caps  = contar_caps_reg(ctx_tmp)
            marca   = " ◀ último" if i-1 == ultimo_idx else ""
            print(f"     {i:2d}. {estado} {icono} {m['nombre']:<22}"
                  f"[{m.get('sitio','?'):<12}] {n_caps:>3} caps{marca}")
        n = len(mangas)
        print(f"\n     {n+1:2d}. ➕  Agregar nuevo manga")
        print(f"     {n+2:2d}. ✏️   Editar / Eliminar manga")
        print(f"     {n+3:2d}. 🔄  Descarga Sucesiva  (opción 12)")
        print(f"     {n+4:2d}. ❌  Salir del programa")
    else:
        print("  ℹ️   Sin mangas configurados.\n")
        print("     1. ➕ Agregar nuevo manga")
        print("     2. ❌ Salir del programa")
        n = 0

    print()
    sug = f" [ENTER = {ultimo_idx+1}]" if 0 <= ultimo_idx < n else ""
    raw = input(f"  ➡️   Selecciona{sug}: ").strip()

    if raw == "" and 0 <= ultimo_idx < n:
        op = ultimo_idx + 1
    else:
        try:
            op = int(raw)
        except ValueError:
            return None

    if op == n + 4 or op == 2 and n == 0:
        return None
    if op == n + 3 and n > 0:
        return "sucesiva"
    if op == n + 2 and n > 0:
        _editar_eliminar_manga(cfg)
        return "reload"
    if op == n + 1:
        _agregar_manga(cfg)
        return "reload"
    if 1 <= op <= n:
        cfg["ultimo_manga"] = op - 1
        guardar_config(cfg)
        return mangas[op - 1]
    return None


def _seleccionar_sitio() -> str:
    print("\n  SITIOS DISPONIBLES:\n")
    for k, v in SITIOS.items():
        print(f"     {k}.  {v['icono']}  {v['nombre']}")
    raw = input("\n  ➡️   Número del sitio: ").strip()
    return SITIOS.get(raw, {}).get("clave", "")


def _agregar_manga(cfg: dict):
    print("\n" + _sep("─"))
    print("  ➕  AGREGAR NUEVO MANGA")
    print(_sep("─"))
    nombre = input("  Nombre (se usará como carpeta): ").strip()
    if not nombre:
        print("  ❌ Nombre inválido"); return

    sitio = _seleccionar_sitio()
    if not sitio:
        print("  ❌ Sitio inválido"); return

    url = ""
    if sitio != "ikigai":
        url = input("  URL de la serie: ").strip()

    nuevo = {"nombre": nombre, "sitio": sitio, "url": url, "activo": True}

    if sitio == "ikigai":
        nuevo["urls_paginas"] = []
        print("  ℹ️   Las URLs de páginas se pedirán al escanear por primera vez.")

    cfg["mangas"].append(nuevo)
    guardar_config(cfg)
    print(f"\n  ✅ '{nombre}' [{_nombre_sitio(sitio)}] agregado.\n")


def _editar_eliminar_manga(cfg: dict):
    mangas = cfg.get("mangas", [])
    if not mangas:
        print("  ℹ️   Sin mangas para editar.")
        return

    print("\n" + _sep("─"))
    print("  ✏️   EDITAR / ELIMINAR MANGA")
    print(_sep("─"))
    for i, m in enumerate(mangas, 1):
        print(f"  {i:2d}. {_icono_sitio(m.get('sitio',''))} {m['nombre']}  [{m.get('sitio','')}]")
    try:
        idx = int(input("\n  Número del manga: ")) - 1
        if not (0 <= idx < len(mangas)):
            print("  ❌ Índice inválido"); return
    except ValueError:
        print("  ❌ Inválido"); return

    m = mangas[idx]
    print(f"\n  Manga: {m['nombre']}  |  Sitio: {m.get('sitio','')}")
    print(f"  URL actual: {m.get('url','(sin url)')}\n")
    print("  1. ✏️   Cambiar nombre")
    print("  2. 🔗  Cambiar URL")
    print("  3. 🌐  Cambiar sitio")
    print("  4. 🔗  Editar URLs de páginas (Ikigai)")
    print("  5. 🗑️   Eliminar este manga del config")
    print("  6. ↩️   Cancelar\n")

    acc = input("  ➡️   Acción: ").strip()

    if acc == "1":
        nuevo_nombre = input("  Nuevo nombre: ").strip()
        if nuevo_nombre:
            m["nombre"] = nuevo_nombre
            guardar_config(cfg)
            print(f"  ✅ Nombre cambiado a '{nuevo_nombre}'")

    elif acc == "2":
        nueva_url = input("  Nueva URL: ").strip()
        if nueva_url:
            m["url"] = nueva_url
            guardar_config(cfg)
            print("  ✅ URL actualizada")

    elif acc == "3":
        nuevo_sitio = _seleccionar_sitio()
        if nuevo_sitio:
            m["sitio"] = nuevo_sitio
            guardar_config(cfg)
            print(f"  ✅ Sitio cambiado a {_nombre_sitio(nuevo_sitio)}")

    elif acc == "4":
        print("  ℹ️   Solo ingresa la URL de la página MÁS ANTIGUA (número más alto).")
        print("       Ejemplo: https://visorikigai.xyz/series/mi-manga/?pagina=4")
        url_ant = input("  URL más antigua: ").strip()
        if url_ant:
            m["urls_paginas"] = [url_ant]
            guardar_config(cfg)
            # Mostrar cuántas páginas se generarán
            m2 = re.search(r'[?&]pagina=(\d+)', url_ant, re.IGNORECASE)
            n_pags = int(m2.group(1)) if m2 else 1
            print(f"  ✅ Guardado. Se generarán {n_pags} página/s al escanear.")

    elif acc == "5":
        conf = input(f"  ¿Eliminar '{m['nombre']}' del config? Escribe CONFIRMAR: ").strip()
        if conf == "CONFIRMAR":
            mangas.pop(idx)
            cfg["mangas"] = mangas
            guardar_config(cfg)
            print("  ✅ Manga eliminado del config (las carpetas NO se borran)")
        else:
            print("  ❌ Cancelado")

    else:
        print("  ↩️   Cancelado")

# ══════════════════════════════════════════════════════════════════════
# §15 UTILS
# ══════════════════════════════════════════════════════════════════════

def parsear_seleccion(s: str) -> list:
    nums = []
    for p in s.split(","):
        p = p.strip()
        if "-" in p and not p.startswith("-"):
            try:
                partes = p.split("-", 1)
                a, b   = float(partes[0]), float(partes[1])
                if a == int(a) and b == int(b):
                    nums.extend(str(n) for n in range(int(a), int(b)+1))
                else:
                    cur = a
                    while cur <= b + 0.001:
                        nums.append(str(int(cur)) if cur==int(cur) else str(cur))
                        cur = round(cur + 0.5, 2)
            except Exception:
                print(f"  ⚠️   Rango inválido: {p}")
        else:
            try:
                v = float(p)
                nums.append(str(int(v)) if v==int(v) else str(v))
            except Exception:
                print(f"  ⚠️   Número inválido: {p}")
    return nums


def cargar_capitulos_cache(ctx: MangaCtx, adaptador: AdaptadorBase) -> list:
    if os.path.exists(ctx.archivo_cache):
        try:
            with open(ctx.archivo_cache, "r", encoding="utf-8") as f:
                caps = json.load(f)
            print(f"\n  💾 Cache: {len(caps)} capítulos")
            if input("  ¿Actualizar desde el sitio? (s/n): ").lower() == "s":
                return _escanear_y_guardar(ctx, adaptador)
            return caps
        except Exception:
            pass
    return _escanear_y_guardar(ctx, adaptador)


def _escanear_y_guardar(ctx: MangaCtx, adaptador: AdaptadorBase) -> list:
    print(f"\n  🔍 Escaneando {_nombre_sitio(ctx.sitio)}...")
    lista = adaptador.escanear_capitulos()
    if lista:
        with open(ctx.archivo_cache, "w", encoding="utf-8") as f:
            json.dump(lista, f, indent=2, ensure_ascii=False)
        print(f"  ✅ {len(lista)} capítulos | Cap {lista[0]['numero']} → Cap {lista[-1]['numero']}")
        print(f"  💾 Cache: {ctx.archivo_cache}\n")
    else:
        print("  ℹ️   No se detectaron capítulos. Verifica la URL en config.json.")
    return lista


def detectar_nuevos(ctx: MangaCtx, adaptador: AdaptadorBase) -> list:
    if not os.path.exists(ctx.archivo_cache):
        print("  ℹ️   Sin cache previo. Usa la opción 1 para escanear primero.")
        return []
    try:
        with open(ctx.archivo_cache, "r", encoding="utf-8") as f:
            viejos = json.load(f)
        nums_viejos = {c["numero"] for c in viejos}
    except Exception:
        return []

    print(f"  📦 Cache anterior: {len(viejos)} capítulos")
    nueva = adaptador.escanear_capitulos()
    if not nueva:
        return []
    with open(ctx.archivo_cache, "w", encoding="utf-8") as f:
        json.dump(nueva, f, indent=2, ensure_ascii=False)

    nuevos = [c for c in nueva if c["numero"] not in nums_viejos]
    if not nuevos:
        print(f"  ✅ Sin capítulos nuevos. Último disponible: Cap {nueva[-1]['numero']}")
        return []
    print(f"  🎉 {len(nuevos)} nuevo/s:")
    for c in nuevos:
        n = str(int(c["numero"])) if c["numero"]==int(c["numero"]) else str(c["numero"])
        print(f"     🆕 Cap {n}  {c.get('texto','')[:40]}")
    if input("\n  ¿Descargar ahora? (s/n): ").lower() == "s":
        return nuevos
    return []


def listar_capitulos(lista: list):
    if not lista:
        print("  ℹ️   Sin capítulos para mostrar.")
        return
    total = len(lista)
    especiales = [c for c in lista if c["numero"] != int(c["numero"])]
    print(f"\n  📚 {total} caps  |  Cap {lista[0]['numero']} → Cap {lista[-1]['numero']}")
    print(f"  Normales: {total - len(especiales)}   Especiales: {len(especiales)}")
    if especiales:
        print("  ⭐ Especiales: " + ", ".join(str(e["numero"]) for e in especiales[:8])
              + (f" +{len(especiales)-8}" if len(especiales)>8 else ""))
    print(_sep("─"))
    for i, c in enumerate(lista[:30], 1):
        n = str(int(c["numero"])) if c["numero"]==int(c["numero"]) else str(c["numero"])
        print(f"   {i:3d}. Cap {n:<6}  {c.get('texto','')[:45]}")
    if total > 30:
        print(f"        ... {total-30} más")
    print(_sep() + "\n")


def relanzar_fallidos(ctx: MangaCtx, caps_detectados: list) -> list:
    if not os.path.isdir(ctx.carpeta_logs):
        print("  ℹ️   Sin logs de sesiones anteriores.")
        return []
    logs = sorted(f for f in os.listdir(ctx.carpeta_logs) if f.endswith(".json"))
    if not logs:
        print("  ℹ️   Sin logs disponibles. Realiza una descarga primero.")
        return []
    try:
        with open(os.path.join(ctx.carpeta_logs, logs[-1]), "r", encoding="utf-8") as f:
            log = json.load(f)
    except Exception as e:
        print(f"  ❌ Log ilegible: {e}")
        return []

    fallidos = [c for c in log.get("capitulos",[]) if c["estado"] == "fallido"]
    if not fallidos:
        print(f"  ✅ Sin capítulos fallidos en el último log ({logs[-1]}).")
        return []

    nums = {float(c["numero"]) for c in fallidos}
    print(f"\n  ⛔ Fallidos en '{logs[-1]}':")
    for c in fallidos:
        print(f"     • Cap {c['numero']}")
    if input("  ¿Relanzar? (s/n): ").lower() != "s":
        return []
    return [c for c in caps_detectados if c["numero"] in nums]


def limpiar_carpetas(ctx: MangaCtx):
    mapa = {"1": ("Capitulos_a_Convertir", ctx.carpeta_convertir),
            "2": ("Capitulos_por_Rango",   ctx.carpeta_rango),
            "3": ("Capitulos_sin_Filtro",  ctx.carpeta_sin_filtro)}
    print("\n  1. Capitulos_a_Convertir\n  2. Capitulos_por_Rango\n  3. Capitulos_sin_Filtro\n  4. Cancelar")
    op = input("  ➡️   Elige: ").strip()
    if op not in mapa:
        print("  ℹ️   Operación cancelada"); return

    nombre, carpeta = mapa[op]
    if not os.path.isdir(carpeta):
        print(f"  ℹ️   La carpeta '{nombre}' no existe todavía."); return

    subs = [d for d in os.listdir(carpeta)
            if os.path.isdir(os.path.join(carpeta, d)) and d.startswith("Capitulo_")]
    if not subs:
        print(f"  ℹ️   '{nombre}' no tiene subcarpetas de capítulos."); return

    validos   = [s for s in subs if _pdf_es_valido(
        os.path.join(ctx.carpeta_pdf, f"Capitulo_{s.replace('Capitulo_','')}.pdf"))]
    invalidos = [s for s in subs if s not in validos and os.path.exists(
        os.path.join(ctx.carpeta_pdf, f"Capitulo_{s.replace('Capitulo_','')}.pdf"))]
    sin_pdf   = [s for s in subs if s not in validos and s not in invalidos]

    print(f"\n  📊 Con PDF válido  : {len(validos)}")
    print(f"  ⚠️   PDF inválido   : {len(invalidos)}")
    print(f"  ❌ Sin PDF         : {len(sin_pdf)}")

    if invalidos:
        print(f"\n  ⚠️   PDFs rotos (no se eliminarán sus imágenes):")
        for s in invalidos[:5]:
            print(f"     • {s.replace('Capitulo_','Cap ')}")

    if not validos:
        print("\n  ℹ️   Ninguna carpeta tiene PDF válido confirmado. Convierte primero.")
        return

    print(f"\n  Se eliminarán {len(validos)} carpetas con PDF válido confirmado.")
    print("  ❗ Esta acción NO es reversible.\n")
    if input("  Escribe CONFIRMAR para proceder: ").strip() != "CONFIRMAR":
        print("  ❌ Cancelado"); return

    elim = errs = 0
    for s in validos:
        try:
            shutil.rmtree(os.path.join(carpeta, s)); elim += 1
        except Exception as e:
            print(f"  ❌ {s}: {e}"); errs += 1
    print(f"\n  ✅ {elim} eliminadas  │  ❌ {errs} errores\n")


def ver_registro(ctx: MangaCtx):
    reg = _cargar_reg(ctx)
    if not reg:
        print("  ℹ️   Registro vacío. Descarga algún capítulo primero.")
        return
    print("\n" + _sep())
    print(f"  📋  REGISTRO — {ctx.nombre.upper()}")
    print(_sep())
    tot_c = tot_i = 0
    for carp, caps in reg.items():
        print(f"\n  📂 {carp}  ({len(caps)} cap/s):")
        print(f"  {'─'*55}")
        for num_str, info in sorted(caps.items(), key=lambda x: float(x[0])):
            fecha = info.get("fecha","")[:10]
            print(f"     Cap {num_str:<6}  {info['estado']:<12}  {info['imagenes']:>4} imgs  {fecha}")
        tot_c += len(caps); tot_i += sum(v["imagenes"] for v in caps.values())
    print(f"\n  Total: {tot_c} caps  |  {tot_i} imgs")
    print(f"  💾 {ctx.archivo_registro}")
    print(_sep() + "\n")


def exportar_registro_txt(ctx: MangaCtx):
    reg = _cargar_reg(ctx)
    if not reg:
        print("  ℹ️   Registro vacío. Nada que exportar.")
        return
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join(ctx.carpeta_manga, f"{ctx.nombre}_resumen_{ts}.txt")
    lineas = ["="*60, f"  {ctx.nombre.upper()}  [{_nombre_sitio(ctx.sitio)}]",
              f"  Fecha: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", "="*60, ""]
    tot_c = tot_i = 0
    for carp, caps in sorted(reg.items()):
        lineas.append(f"📂 {carp}  ({len(caps)} caps)")
        for num_str, info in sorted(caps.items(), key=lambda x: float(x[0])):
            est = "✅" if info["estado"] == "completado" else "⚠️"
            lineas.append(f"   {est} Cap {num_str:<6}  {info['imagenes']:>4} imgs  {info.get('fecha','')[:10]}")
        lineas.append("")
        tot_c += len(caps); tot_i += sum(v["imagenes"] for v in caps.values())
    lineas += ["─"*60, f"TOTAL: {tot_c} caps  |  {tot_i} imgs", "="*60]
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    print(f"  ✅ Exportado: {ruta}\n")


def mostrar_reporte_final(procesados, tipo, ruta_log, ctx):
    exitosos = [p for p in procesados if p["res"]["estado"] == "exitoso"]
    omitidos = [p for p in procesados if p["res"]["estado"] == "omitido"]
    fallidos = [p for p in procesados if p["res"]["estado"] == "fallido"]
    total_imgs = sum(p["res"].get("guardadas",0) for p in procesados)
    total_fds  = sum(p["res"].get("fallidas_desc",0) for p in procesados)

    def fn(n): return str(int(n)) if n==int(n) else str(n)
    print("\n" + _sep())
    print(f"  🏁  RESUMEN — {ctx.nombre.upper()}  [{tipo}]  [{_nombre_sitio(ctx.sitio)}]")
    print(_sep())
    print(f"  ✅ Exitosos  : {len(exitosos)}")
    print(f"  ⏭️  Omitidos : {len(omitidos)}")
    print(f"  ❌ Fallidos  : {len(fallidos)}")
    print(f"  🖼️  Imgs     : {total_imgs}")
    if total_fds:
        print(f"  ⚠️  Sin descargar: {total_fds}  ({ctx.max_reintentos} reintentos c/u)")
    if omitidos:
        print(f"\n  📋 Omitidos: {', '.join(fn(p['num']) for p in omitidos)}")
    if fallidos:
        print("\n  ⛔ FALLIDOS:")
        for p in fallidos:
            print(f"     • Cap {fn(p['num'])}")
    print(f"\n  📝 Log: {ruta_log}")
    print(_sep() + "\n")

# ══════════════════════════════════════════════════════════════════════
# §16 SESIÓN POR MANGA
# ══════════════════════════════════════════════════════════════════════

def ejecutar_sesion_manga(ctx: MangaCtx):
    mostrar_banner(ctx)
    adaptador        = crear_adaptador(ctx)
    reg              = _cargar_reg(ctx)
    caps_detectados: list = []

    while True:
        opcion = mostrar_menu_manga()

        if opcion == "Q":
            print("\n  👋 ¡Hasta luego!\n")
            sys.exit(0)
        if opcion == "0":
            break
        if opcion == "1":
            caps_detectados = cargar_capitulos_cache(ctx, adaptador)
            if caps_detectados: listar_capitulos(caps_detectados)
            continue
        if opcion == "2":
            nuevos = detectar_nuevos(ctx, adaptador)
            if nuevos:
                _lanzar_descarga(nuevos, True, ctx.carpeta_convertir,
                                 "Capitulos_a_Convertir", "nuevos", ctx, reg, adaptador)
            continue
        if opcion == "7":
            convertir_a_pdf(ctx); continue
        if opcion == "8":
            if not caps_detectados:
                caps_detectados = cargar_capitulos_cache(ctx, adaptador)
            a_rel = relanzar_fallidos(ctx, caps_detectados)
            if a_rel:
                _lanzar_descarga(a_rel, True, ctx.carpeta_convertir,
                                 "Capitulos_a_Convertir", "relanzar", ctx, reg, adaptador)
            continue
        if opcion == "9":
            limpiar_carpetas(ctx); continue
        if opcion == "10":
            ver_registro(ctx); continue
        if opcion == "11":
            exportar_registro_txt(ctx); continue

        if not caps_detectados:
            caps_detectados = cargar_capitulos_cache(ctx, adaptador)
            if not caps_detectados:
                print("  ❌ No se pudieron cargar capítulos.")
                continue

        caps_a = []; filtro = True
        c_dest = ctx.carpeta_convertir
        c_reg  = "Capitulos_a_Convertir"
        tipo   = "selección"

        if opcion == "3":
            try:
                mn = caps_detectados[0]["numero"]; mx = caps_detectados[-1]["numero"]
                print(f"\n  Rango disponible: {mn} → {mx}")
                ini = float(input("     Inicio: "))
                fin = float(input("     Fin   : "))
                caps_a = [c for c in caps_detectados if ini <= c["numero"] <= fin]
                c_dest = ctx.carpeta_rango; c_reg = "Capitulos_por_Rango"
                tipo   = f"rango {ini}–{fin}"
                if not caps_a:
                    print("  ⚠️   Sin capítulos en ese rango.")
                    continue
            except ValueError:
                print("  ❌ Rango inválido"); continue

        elif opcion == "4":
            sel = input("\n  Capítulos (ej: 1,2,5-10): ").strip()
            if not sel: continue
            nums_f = [float(n) for n in parsear_seleccion(sel)]
            caps_a = [c for c in caps_detectados if c["numero"] in nums_f]

        elif opcion == "5":
            ult = caps_detectados[-1]
            n_u = str(int(ult["numero"])) if ult["numero"]==int(ult["numero"]) else str(ult["numero"])
            print(f"\n  ⬇️   Último disponible: Cap {n_u}  ({ult.get('texto','')[:40]})")
            if input("  ¿Descargar? (s/n): ").lower() == "s":
                caps_a = [ult]; tipo = f"último (cap {n_u})"
            else:
                continue

        elif opcion == "6":
            sel = input("\n  Sin filtro — capítulos (ej: 1,2,5-10): ").strip()
            if not sel: continue
            nums_f = [float(n) for n in parsear_seleccion(sel)]
            caps_a = [c for c in caps_detectados if c["numero"] in nums_f]
            filtro = False; c_dest = ctx.carpeta_sin_filtro
            c_reg  = "Capitulos_sin_Filtro"; tipo = "sin filtro"

        if not caps_a:
            print("  ⚠️   Sin capítulos con esos parámetros.")
            continue

        _lanzar_descarga(caps_a, filtro, c_dest, c_reg, tipo, ctx, reg, adaptador)


def _lanzar_descarga(caps_a_descargar, con_filtro, carpeta_destino,
                      carp_reg, tipo_descarga, ctx, reg, adaptador):
    ya = sum(1 for c in caps_a_descargar
             if cap_ya_descargado(reg, carp_reg, c["numero"], carpeta_destino))
    print(f"\n  📊 {len(caps_a_descargar)} cap/s  (⏭️ {ya} en registro, "
          f"⬇️ {len(caps_a_descargar)-ya} nuevos)")
    if input("  ¿Continuar? (s/n): ").lower() != "s":
        return

    _INTERRUMPIDO.clear()
    logger   = SessionLogger(ctx.nombre, ctx.sitio, tipo_descarga,
                              ctx.carpeta_logs, carpeta_destino)
    detector = BlockDetector(ctx.bloqueo_max_errs, ctx.bloqueo_pausa)

    barra_caps = tqdm(caps_a_descargar, unit="cap", desc="  Progreso", ncols=65,
                      bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]"
                      ) if TQDM_OK else None

    procesados: list = []; cap_en_curso = None
    try:
        for cap in (barra_caps or caps_a_descargar):
            if _INTERRUMPIDO.is_set(): break
            num_fmt = str(int(cap["numero"])) if cap["numero"]==int(cap["numero"]) \
                      else str(cap["numero"])
            if barra_caps: barra_caps.set_postfix(cap=num_fmt)
            cap_en_curso = cap
            res = descargar_capitulo(
                url=cap["url"], numero=cap["numero"],
                con_filtro=con_filtro, carpeta_destino=carpeta_destino,
                reg=reg, carp_reg=carp_reg, ctx=ctx,
                logger=logger, detector=detector, adaptador=adaptador,
            )
            cap_en_curso = None
            procesados.append({"num": cap["numero"], "url": cap["url"], "res": res})
            if not _INTERRUMPIDO.is_set(): time.sleep(1)
    except Exception:
        pass
    finally:
        if _INTERRUMPIDO.is_set() and cap_en_curso is not None:
            num_str = str(int(cap_en_curso["numero"])) if cap_en_curso["numero"]==int(cap_en_curso["numero"]) \
                      else str(cap_en_curso["numero"])
            carpeta_cap = os.path.join(carpeta_destino, f"Capitulo_{num_str}")
            n_p = len([f for f in os.listdir(carpeta_cap)
                        if f.lower().endswith((".jpg",".jpeg",".png",".webp",".gif"))]) \
                  if os.path.isdir(carpeta_cap) else 0
            marcar_parcial(ctx, reg, carp_reg, cap_en_curso["numero"], n_p)
            print(f"\n  ⚠️   Cap {num_str} marcado PARCIAL ({n_p} imgs guardadas)")
        if barra_caps: barra_caps.close()
        ruta_log = logger.guardar()
        mostrar_reporte_final(procesados, tipo_descarga, ruta_log, ctx)
        if _INTERRUMPIDO.is_set():
            print("  ⚠️   Sesión interrumpida. Log guardado.\n")

# ══════════════════════════════════════════════════════════════════════
# §17 DESCARGA SUCESIVA  (opción 12)
# ══════════════════════════════════════════════════════════════════════

_SESION_SUCESIVA_PATH = os.path.join(_SCRIPT_DIR, "sesion_sucesiva.json")

# ── Helpers de estimación ─────────────────────────────────────────────

def _estimar_tiempo(caps_total: int, prom_pags: float, vel_hist_mbs: float = 0.5) -> str:
    """Devuelve string "~X min" estimado basándose en páginas y velocidad."""
    if caps_total <= 0 or prom_pags <= 0:
        return "desconocido"
    bytes_por_pag = 350_000          # ~350 KB por imagen (estimación conservadora)
    total_mb      = caps_total * prom_pags * bytes_por_pag / (1024 * 1024)
    vel           = max(vel_hist_mbs, 0.1)
    seg           = total_mb / vel
    minutos       = int(seg / 60) + 1
    if minutos < 60:
        return f"~{minutos} min"
    h = minutos // 60; m = minutos % 60
    return f"~{h}h {m}min"


def _caps_pendientes(ctx: MangaCtx, todos_los_caps: list) -> list:
    """Devuelve los capítulos de todos_los_caps que NO están en el registro."""
    reg    = _cargar_reg(ctx)
    c_dest = ctx.carpeta_convertir
    c_reg  = "Capitulos_a_Convertir"
    return [c for c in todos_los_caps
            if not cap_ya_descargado(reg, c_reg, c["numero"], c_dest)]


# ── Guardar / cargar sesión interrumpida ─────────────────────────────

def _guardar_sesion_sucesiva(plan: list):
    """
    Guarda el plan de descarga sucesiva al disco para poder reanudar
    si la sesión se interrumpe.
    plan = lista de dicts con claves: manga_cfg_nombre, caps_json, completado
    """
    try:
        with open(_SESION_SUCESIVA_PATH, "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.datetime.now().isoformat(), "plan": plan},
                      f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _cargar_sesion_sucesiva() -> list:
    try:
        with open(_SESION_SUCESIVA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("plan", [])
    except Exception:
        return []


def _borrar_sesion_sucesiva():
    try:
        if os.path.exists(_SESION_SUCESIVA_PATH):
            os.remove(_SESION_SUCESIVA_PATH)
    except Exception:
        pass


# ── Log global ────────────────────────────────────────────────────────

class LogGlobalSucesiva:
    """
    Genera dos archivos al finalizar una descarga sucesiva:
      1. Un .json completo (datos por manga y capítulo)
      2. Un .html con tabla visual navegable
    """
    def __init__(self, carpeta_logs_global: str):
        self.carpeta = carpeta_logs_global
        self.ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.mangas: list = []   # acumulamos resultados por manga
        self.t_inicio = time.time()

    def agregar_manga(self, nombre: str, sitio: str, caps_intentados: int,
                      procesados: list):
        exitosos  = [p for p in procesados if p["res"]["estado"] == "exitoso"]
        omitidos  = [p for p in procesados if p["res"]["estado"] == "omitido"]
        fallidos  = [p for p in procesados if p["res"]["estado"] == "fallido"]
        total_img = sum(p["res"].get("guardadas", 0) for p in procesados)
        self.mangas.append({
            "nombre": nombre, "sitio": sitio,
            "intentados": caps_intentados,
            "exitosos": len(exitosos), "omitidos": len(omitidos),
            "fallidos": len(fallidos), "total_imgs": total_img,
            "caps_fallidos": [str(int(p["num"])) if p["num"]==int(p["num"])
                               else str(p["num"]) for p in fallidos],
        })

    def guardar(self) -> tuple:
        """Devuelve (ruta_json, ruta_html)."""
        os.makedirs(self.carpeta, exist_ok=True)
        dur_seg  = int(time.time() - self.t_inicio)
        duracion = f"{dur_seg//60}m {dur_seg%60}s"

        totales = {
            "mangas":    len(self.mangas),
            "caps_ok":   sum(m["exitosos"]  for m in self.mangas),
            "omitidos":  sum(m["omitidos"]  for m in self.mangas),
            "fallidos":  sum(m["fallidos"]  for m in self.mangas),
            "imgs":      sum(m["total_imgs"] for m in self.mangas),
            "duracion":  duracion,
        }

        # ── JSON ─────────────────────────────────────────────────────
        ruta_json = os.path.join(self.carpeta, f"sucesiva_{self.ts}.json")
        with open(ruta_json, "w", encoding="utf-8") as f:
            json.dump({"fecha": self.ts, "totales": totales,
                       "mangas": self.mangas}, f, indent=2, ensure_ascii=False)

        # ── HTML ─────────────────────────────────────────────────────
        ruta_html = os.path.join(self.carpeta, f"sucesiva_{self.ts}.html")
        filas = ""
        for m in self.mangas:
            icono   = _icono_sitio(m["sitio"])
            fal_str = (", ".join(f"Cap {c}" for c in m["caps_fallidos"][:5])
                       + ("..." if len(m["caps_fallidos"]) > 5 else "")) \
                      if m["caps_fallidos"] else "—"
            est_cls = "ok" if m["fallidos"] == 0 else "warn"
            filas += (
                f'<tr class="{est_cls}">'
                f'<td>{icono} {m["nombre"]}</td>'
                f'<td>{_nombre_sitio(m["sitio"])}</td>'
                f'<td>{m["intentados"]}</td>'
                f'<td class="ok-n">{m["exitosos"]}</td>'
                f'<td class="skip-n">{m["omitidos"]}</td>'
                f'<td class="fail-n">{m["fallidos"]}</td>'
                f'<td>{m["total_imgs"]}</td>'
                f'<td class="small">{fal_str}</td>'
                f'</tr>\n'
            )

        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Descarga Sucesiva — {self.ts}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background:#0f0f0f; color:#e0e0e0; padding:20px; }}
  h1   {{ color:#a78bfa; }} h2 {{ color:#7dd3fc; margin-top:30px; }}
  .summary {{ display:flex; gap:20px; flex-wrap:wrap; margin:16px 0; }}
  .card {{ background:#1e1e2e; border-radius:10px; padding:16px 24px; min-width:120px; text-align:center; }}
  .card .val {{ font-size:2em; font-weight:bold; color:#a78bfa; }}
  .card .lbl {{ font-size:.8em; color:#888; margin-top:4px; }}
  table {{ border-collapse:collapse; width:100%; margin-top:12px; }}
  th    {{ background:#1e1e2e; color:#7dd3fc; padding:10px 14px; text-align:left; }}
  td    {{ padding:9px 14px; border-bottom:1px solid #2a2a3a; }}
  tr.ok   {{ }} tr.warn td {{ background:#1a120a; }}
  .ok-n   {{ color:#4ade80; font-weight:bold; }}
  .skip-n {{ color:#facc15; }}
  .fail-n {{ color:#f87171; font-weight:bold; }}
  .small  {{ font-size:.8em; color:#aaa; max-width:200px; word-break:break-word; }}
  tr:hover td {{ background:#1e2a1e; }}
  .footer {{ margin-top:24px; color:#555; font-size:.85em; }}
</style>
</head>
<body>
<h1>🎌 Descarga Sucesiva</h1>
<p>Fecha: <b>{self.ts[:4]}-{self.ts[4:6]}-{self.ts[6:8]} {self.ts[9:11]}:{self.ts[11:13]}</b>
   &nbsp;|&nbsp; Duración: <b>{duracion}</b></p>
<div class="summary">
  <div class="card"><div class="val">{totales["mangas"]}</div><div class="lbl">Mangas</div></div>
  <div class="card"><div class="val ok-n">{totales["caps_ok"]}</div><div class="lbl">Caps ✅</div></div>
  <div class="card"><div class="val skip-n">{totales["omitidos"]}</div><div class="lbl">Omitidos ⏭️</div></div>
  <div class="card"><div class="val fail-n">{totales["fallidos"]}</div><div class="lbl">Fallidos ❌</div></div>
  <div class="card"><div class="val">{totales["imgs"]}</div><div class="lbl">Imágenes 🖼️</div></div>
</div>
<h2>📋 Detalle por Manga</h2>
<table>
<thead><tr>
  <th>Manga</th><th>Sitio</th><th>Intentados</th>
  <th>✅ OK</th><th>⏭️ Omit</th><th>❌ Fal</th>
  <th>🖼️ Imgs</th><th>Caps fallidos</th>
</tr></thead>
<tbody>
{filas}
</tbody>
</table>
<div class="footer">Generado por Manga Downloader Unificado v9.0</div>
</body></html>"""

        with open(ruta_html, "w", encoding="utf-8") as f:
            f.write(html)

        return ruta_json, ruta_html


# ── Notificación Windows ──────────────────────────────────────────────

def _notificar_windows(titulo: str, mensaje: str):
    """Intenta mostrar notificación de Windows y reproducir sonido."""
    # Sonido de sistema
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONINFORMATION)
    except Exception:
        pass
    # Toast notification via PowerShell (no requiere dependencias externas)
    try:
        ps_script = (
            f"[Windows.UI.Notifications.ToastNotificationManager, "
            f"Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
            f"$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
            f"$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);"
            f"$x.GetElementsByTagName('text')[0].AppendChild($x.CreateTextNode('{titulo}')) | Out-Null;"
            f"$x.GetElementsByTagName('text')[1].AppendChild($x.CreateTextNode('{mensaje}')) | Out-Null;"
            f"$n = [Windows.UI.Notifications.ToastNotification]::new($x);"
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier"
            f"('MangaDownloader').Show($n);"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


# ── Fase de preparación ───────────────────────────────────────────────

def _preparar_manga_sucesivo(manga_cfg: dict, cfg: dict, idx_display: int,
                               total: int) -> "dict | None":
    """
    Ejecuta la fase de preparación para un manga:
      1. Escanea capítulos (o usa cache)
      2. Detecta pendientes automáticamente
      3. Pregunta el rango (propone pendientes como default)
      4. Devuelve el plan para ese manga o None si se cancela
    """
    ctx       = crear_ctx(manga_cfg, cfg)
    adaptador = crear_adaptador(ctx)
    icono     = _icono_sitio(ctx.sitio)

    print(f"\n{'═'*70}")
    print(f"  [{idx_display}/{total}]  {icono}  {ctx.nombre}  [{_nombre_sitio(ctx.sitio)}]")
    print(f"{'─'*70}")

    # ── Escanear capítulos ────────────────────────────────────────────
    caps_all: list = []
    if os.path.exists(ctx.archivo_cache):
        try:
            with open(ctx.archivo_cache, "r", encoding="utf-8") as f:
                caps_cache = json.load(f)
            print(f"  💾 Cache existente: {len(caps_cache)} capítulos  "
                  f"(Cap {caps_cache[0]['numero']} → {caps_cache[-1]['numero']})")
            resp = input("  ¿Actualizar desde el sitio? (s/n) [ENTER=no]: ").strip().lower()
            if resp == "s":
                caps_all = _escanear_y_guardar(ctx, adaptador)
            else:
                caps_all = caps_cache
        except Exception:
            caps_all = _escanear_y_guardar(ctx, adaptador)
    else:
        caps_all = _escanear_y_guardar(ctx, adaptador)

    if not caps_all:
        print(f"  ❌ No se pudieron obtener capítulos. Se omitirá este manga.")
        return None

    # ── Detectar pendientes ───────────────────────────────────────────
    pendientes = _caps_pendientes(ctx, caps_all)
    prom       = promedio_paginas(_cargar_reg(ctx), "Capitulos_a_Convertir")

    print(f"\n  📊 Total disponibles : {len(caps_all)} caps")
    print(f"  🆕 Pendientes        : {len(pendientes)} caps  (no en registro)")
    if prom > 0:
        print(f"  📄 Prom. páginas     : {prom:.1f} imgs/cap")

    # ── Propuesta de rango ────────────────────────────────────────────
    if pendientes:
        prop_ini = pendientes[0]["numero"]
        prop_fin = pendientes[-1]["numero"]
    else:
        prop_ini = caps_all[0]["numero"]
        prop_fin = caps_all[-1]["numero"]

    mn_disp = caps_all[0]["numero"]
    mx_disp = caps_all[-1]["numero"]
    fn       = lambda n: str(int(n)) if n == int(n) else str(n)

    print(f"\n  Rango disponible   : {fn(mn_disp)} → {fn(mx_disp)}")
    print(f"  Propuesta default  : {fn(prop_ini)} → {fn(prop_fin)}")
    print(f"  (ENTER para aceptar propuesta, 'todos' para todos, o escribe rango)")
    print()

    raw_ini = input(f"     Inicio [{fn(prop_ini)}]: ").strip()
    raw_fin = input(f"     Fin    [{fn(prop_fin)}]: ").strip()

    try:
        ini = float(raw_ini) if raw_ini else prop_ini
        fin = float(raw_fin) if raw_fin else prop_fin
    except ValueError:
        print("  ❌ Rango inválido, se omite este manga.")
        return None

    caps_sel = [c for c in caps_all if ini <= c["numero"] <= fin]
    if not caps_sel:
        print(f"  ⚠️   Sin capítulos en ese rango. Se omite este manga.")
        return None

    # Ya descargados dentro del rango
    reg       = _cargar_reg(ctx)
    c_dest    = ctx.carpeta_convertir
    c_reg     = "Capitulos_a_Convertir"
    ya_en_reg = sum(1 for c in caps_sel
                    if cap_ya_descargado(reg, c_reg, c["numero"], c_dest))
    nuevos    = len(caps_sel) - ya_en_reg
    est_t     = _estimar_tiempo(nuevos, prom if prom > 0 else 15)

    print(f"\n  ✅ Seleccionados: {len(caps_sel)} caps  "
          f"(⏭️ {ya_en_reg} en registro, ⬇️ {nuevos} nuevos)")
    print(f"  ⏱️   Estimado: {est_t}")

    return {
        "manga_cfg":   manga_cfg,
        "caps_sel":    caps_sel,
        "ini":         ini,
        "fin":         fin,
        "nuevos":      nuevos,
        "ya_en_reg":   ya_en_reg,
        "est_t":       est_t,
        "completado":  False,
    }


# ── Resumen previo + confirmación ────────────────────────────────────

def _mostrar_resumen_previo(planes: list):
    """Muestra tabla resumen antes de iniciar las descargas."""
    print(f"\n{'═'*70}")
    print(f"  📋  RESUMEN DESCARGA SUCESIVA  ({len(planes)} mangas)")
    print(f"{'═'*70}")
    print(f"  {'#':>3}  {'Manga':<26}  {'Sitio':<18}  {'Caps':>5}  {'New':>5}  {'Estim.':<9}")
    print(f"  {'─'*68}")
    total_new = 0
    for i, plan in enumerate(planes, 1):
        m    = plan["manga_cfg"]
        ctx_ = plan.get("_ctx_nombre", m["nombre"])
        icono= _icono_sitio(m.get("sitio",""))
        ncaps= len(plan["caps_sel"])
        new  = plan["nuevos"]
        est  = plan["est_t"]
        total_new += new
        print(f"  {i:>3}. {icono} {m['nombre']:<24}  "
              f"{_nombre_sitio(m.get('sitio','')):<18}  "
              f"{ncaps:>5}  {new:>5}  {est:<9}")
    print(f"  {'─'*68}")
    print(f"  {'TOTAL':<32}  {sum(len(p['caps_sel']) for p in planes):>21}  {total_new:>5}")
    print(f"{'═'*70}\n")


# ── Función principal opción 12 ───────────────────────────────────────

def ejecutar_descarga_sucesiva(cfg: dict):
    mangas = cfg.get("mangas", [])
    if not mangas:
        print("  ℹ️   No hay mangas configurados. Agrega mangas primero.")
        return

    # ── Verificar sesión interrumpida ─────────────────────────────────
    sesion_guardada = _cargar_sesion_sucesiva()
    if sesion_guardada:
        pendientes_prev = [p for p in sesion_guardada if not p.get("completado")]
        completados_prev = [p for p in sesion_guardada if p.get("completado")]

        print(f"\n{'═'*70}")
        print(f"  ⚠️   SESIÓN SUCESIVA INTERRUMPIDA")
        print(f"{'═'*70}")
        print(f"  ✅ Ya completados : {len(completados_prev)} manga/s")
        print(f"  ⏳ Pendientes     : {len(pendientes_prev)} manga/s")
        if pendientes_prev:
            for p in pendientes_prev:
                nombre = p.get("manga_nombre", "?")
                ncaps  = len(p.get("caps_sel", []))
                print(f"     • {nombre}  ({ncaps} caps)")
        print()
        resp = input("  ¿Reanudar sesión anterior? (s/n): ").strip().lower()

        if resp == "s":
            # Reconstruir planes desde sesión guardada
            planes = []
            nombres_en_sesion = set()
            for entrada in sesion_guardada:
                if entrada.get("completado"):
                    continue
                m_cfg = next(
                    (m for m in mangas if m["nombre"] == entrada["manga_nombre"]),
                    None
                )
                if m_cfg is None:
                    print(f"  ⚠️   '{entrada['manga_nombre']}' ya no está en el config, se omite.")
                    continue
                caps = entrada.get("caps_sel", [])
                if not caps:
                    continue
                planes.append({
                    "manga_cfg":  m_cfg,
                    "caps_sel":   caps,
                    "ini":        caps[0]["numero"],
                    "fin":        caps[-1]["numero"],
                    "nuevos":     entrada.get("nuevos", len(caps)),
                    "ya_en_reg":  entrada.get("ya_en_reg", 0),
                    "est_t":      entrada.get("est_t", "?"),
                    "completado": False,
                })
                nombres_en_sesion.add(m_cfg["nombre"])

            if not planes:
                print("  ℹ️   No se pudieron reconstruir los planes. Iniciando nueva sesión...")
                _borrar_sesion_sucesiva()
                sesion_guardada = []
            else:
                print(f"\n  ✅ {len(planes)} manga/s de la sesión anterior recuperados.")

                # ── Preguntar si añadir mangas adicionales ────────────────
                mangas_disponibles_nuevos = [
                    m for m in mangas if m["nombre"] not in nombres_en_sesion
                ]
                if mangas_disponibles_nuevos:
                    print(f"\n  ¿Quieres agregar mangas adicionales a esta sesión?")
                    print(f"  Mangas disponibles no incluidos:\n")
                    for i, m in enumerate(mangas_disponibles_nuevos, 1):
                        icono = _icono_sitio(m.get("sitio", ""))
                        print(f"     {i:>3}. {icono}  {m['nombre']:<28}  [{m.get('sitio','?')}]")
                    print(f"\n  Escribe números a agregar (ej: 1,3) o ENTER para omitir")
                    raw_extra = input("  ➡️   Agregar: ").strip()

                    if raw_extra:
                        try:
                            idx_extra = [int(x.strip()) - 1 for x in raw_extra.split(",")
                                         if x.strip()]
                            idx_extra = [i for i in idx_extra
                                         if 0 <= i < len(mangas_disponibles_nuevos)]
                        except ValueError:
                            idx_extra = []

                        if idx_extra:
                            mangas_nuevos = [mangas_disponibles_nuevos[i] for i in idx_extra]
                            print(f"\n  ✅ {len(mangas_nuevos)} manga/s adicionales. "
                                  f"Preparando...")

                            # Preguntar modo para los nuevos
                            print(f"\n  ¿Modo para los mangas adicionales?")
                            print(f"  1. Normal  — escanear + preguntar rango")
                            print(f"  2. Pending — solo capítulos nuevos")
                            modo_raw = input("  ➡️   Modo [1/2, ENTER=2]: ").strip()
                            modo_pending_extra = (modo_raw != "1")

                            planes_extra = []
                            for j, m_cfg in enumerate(mangas_nuevos, 1):
                                if modo_pending_extra:
                                    ctx_e     = crear_ctx(m_cfg, cfg)
                                    adapt_e   = crear_adaptador(ctx_e)
                                    icono_e   = _icono_sitio(ctx_e.sitio)
                                    print(f"\n  [{j}/{len(mangas_nuevos)}] "
                                          f"{icono_e} {ctx_e.nombre} — escaneando...")
                                    caps_all_e = _escanear_y_guardar(ctx_e, adapt_e)
                                    if not caps_all_e:
                                        print(f"  ❌ Sin capítulos. Se omite.")
                                        continue
                                    pend_e = _caps_pendientes(ctx_e, caps_all_e)
                                    if not pend_e:
                                        print(f"  ✅ Sin pendientes. Se omite.")
                                        continue
                                    prom_e = promedio_paginas(_cargar_reg(ctx_e),
                                                               "Capitulos_a_Convertir")
                                    est_e  = _estimar_tiempo(len(pend_e),
                                                             prom_e if prom_e > 0 else 15)
                                    print(f"  🆕 {len(pend_e)} pendientes | {est_e}")
                                    planes_extra.append({
                                        "manga_cfg":  m_cfg,
                                        "caps_sel":   pend_e,
                                        "ini":        pend_e[0]["numero"],
                                        "fin":        pend_e[-1]["numero"],
                                        "nuevos":     len(pend_e),
                                        "ya_en_reg":  0,
                                        "est_t":      est_e,
                                        "completado": False,
                                    })
                                else:
                                    plan_e = _preparar_manga_sucesivo(
                                        m_cfg, cfg, j, len(mangas_nuevos))
                                    if plan_e:
                                        planes_extra.append(plan_e)

                            if planes_extra:
                                planes = planes + planes_extra
                                print(f"\n  ✅ Cola final: {len(planes)} mangas "
                                      f"({len(planes) - len(planes_extra)} reanudados "
                                      f"+ {len(planes_extra)} nuevos)")

                # Mostrar resumen combinado antes de arrancar
                _mostrar_resumen_previo(planes)
                confirmar = input("  ¿Iniciar descarga? (s/n): ").strip().lower()
                if confirmar != "s":
                    print("  ❌ Descarga cancelada. La sesión anterior sigue guardada.")
                    return

                _iniciar_fase_descarga(planes, cfg)
                return
        else:
            _borrar_sesion_sucesiva()

    # ── Mostrar lista de mangas ───────────────────────────────────────
    print(f"\n{'═'*70}")
    print(f"  🎌  DESCARGA SUCESIVA  —  SELECCIÓN DE MANGAS")
    print(f"{'═'*70}")
    print(f"\n  Mangas disponibles:\n")
    for i, m in enumerate(mangas, 1):
        icono  = _icono_sitio(m.get("sitio",""))
        estado = "✅" if m.get("activo", True) else "⏸️"
        print(f"     {i:>3}. {estado} {icono}  {m['nombre']:<28}  [{m.get('sitio','?')}]")

    print(f"\n  Escribe los números separados por comas (ej: 1,3,4)")
    print(f"  O escribe 'todos' para seleccionar todos los mangas activos")
    raw = input("\n  ➡️   Selección: ").strip()

    if not raw:
        print("  ❌ Selección vacía.")
        return

    if raw.lower() == "todos":
        indices = list(range(len(mangas)))
    else:
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
            indices = [i for i in indices if 0 <= i < len(mangas)]
        except ValueError:
            print("  ❌ Selección inválida.")
            return

    if not indices:
        print("  ❌ Ningún índice válido.")
        return

    mangas_sel = [mangas[i] for i in indices]
    print(f"\n  ✅ {len(mangas_sel)} manga/s seleccionados.")

    # ── Modo rápido --pending ─────────────────────────────────────────
    print(f"\n  ¿Modo de preparación?")
    print(f"  1. Normal   — escanear + preguntar rango para cada manga")
    print(f"  2. Pending  — solo capítulos nuevos, sin preguntar rango")
    modo_raw = input("\n  ➡️   Modo [1/2, ENTER=1]: ").strip()
    modo_pending = (modo_raw == "2")

    # ══ FASE DE PREPARACIÓN ══════════════════════════════════════════
    print(f"\n{'═'*70}")
    print(f"  🔍  FASE DE PREPARACIÓN  ({len(mangas_sel)} mangas)")
    print(f"{'═'*70}")

    planes = []
    for i, m_cfg in enumerate(mangas_sel, 1):
        if modo_pending:
            # Preparación automática: solo pendientes sin pedir rango
            ctx       = crear_ctx(m_cfg, cfg)
            adaptador = crear_adaptador(ctx)
            icono     = _icono_sitio(ctx.sitio)
            print(f"\n  [{i}/{len(mangas_sel)}] {icono} {ctx.nombre} — escaneando...")
            caps_all = _escanear_y_guardar(ctx, adaptador)
            if not caps_all:
                print(f"  ❌ Sin capítulos. Se omite.")
                continue
            pendientes = _caps_pendientes(ctx, caps_all)
            if not pendientes:
                print(f"  ✅ Sin pendientes. Se omite.")
                continue
            prom  = promedio_paginas(_cargar_reg(ctx), "Capitulos_a_Convertir")
            est_t = _estimar_tiempo(len(pendientes), prom if prom > 0 else 15)
            print(f"  🆕 {len(pendientes)} pendientes | {est_t}")
            planes.append({
                "manga_cfg":   m_cfg,
                "caps_sel":    pendientes,
                "ini":         pendientes[0]["numero"],
                "fin":         pendientes[-1]["numero"],
                "nuevos":      len(pendientes),
                "ya_en_reg":   0,
                "est_t":       est_t,
                "completado":  False,
            })
        else:
            plan = _preparar_manga_sucesivo(m_cfg, cfg, i, len(mangas_sel))
            if plan:
                planes.append(plan)

    if not planes:
        print(f"\n  ℹ️   No quedaron mangas para descargar.")
        return

    # ── Resumen + confirmación ────────────────────────────────────────
    _mostrar_resumen_previo(planes)
    confirmar = input("  ¿Iniciar descarga? (s/n): ").strip().lower()
    if confirmar != "s":
        print("  ❌ Descarga cancelada.")
        return

    _iniciar_fase_descarga(planes, cfg)


def _iniciar_fase_descarga(planes: list, cfg: dict):
    """Ejecuta la fase de descarga para todos los planes preparados."""

    # Guardar sesión al disco (por si se interrumpe)
    _guardar_sesion_sucesiva([
        {
            "manga_nombre": p["manga_cfg"]["nombre"],
            "caps_sel":     p["caps_sel"],
            "nuevos":       p["nuevos"],
            "ya_en_reg":    p["ya_en_reg"],
            "est_t":        p["est_t"],
            "completado":   p["completado"],
        }
        for p in planes
    ])

    # Preparar log global
    carpeta_logs_global = os.path.join(_SCRIPT_DIR, "logs_sucesiva")
    os.makedirs(carpeta_logs_global, exist_ok=True)
    log_global = LogGlobalSucesiva(carpeta_logs_global)

    t_inicio_total = time.time()

    print(f"\n{'═'*70}")
    print(f"  ⬇️   FASE DE DESCARGA  ({len(planes)} mangas)")
    print(f"{'═'*70}")

    for i, plan in enumerate(planes, 1):
        if _INTERRUMPIDO.is_set():
            print("\n  ⚠️   CTRL+C — deteniendo descarga sucesiva.")
            break

        m_cfg     = plan["manga_cfg"]
        caps_sel  = plan["caps_sel"]
        ctx       = crear_ctx(m_cfg, cfg)
        adaptador = crear_adaptador(ctx)
        reg       = _cargar_reg(ctx)
        icono     = _icono_sitio(ctx.sitio)

        print(f"\n{'═'*70}")
        print(f"  📦  [{i}/{len(planes)}]  {icono}  {ctx.nombre}")
        print(f"      Caps: {len(caps_sel)}  |  "
              f"Rango: {plan['ini']} → {plan['fin']}  |  "
              f"Estimado: {plan['est_t']}")
        print(f"{'═'*70}")

        ya = sum(1 for c in caps_sel
                 if cap_ya_descargado(reg, "Capitulos_a_Convertir",
                                      c["numero"], ctx.carpeta_convertir))
        print(f"  📊 {len(caps_sel)} cap/s  (⏭️ {ya} en registro, "
              f"⬇️ {len(caps_sel)-ya} nuevos)\n")

        _INTERRUMPIDO.clear()
        logger_ind = SessionLogger(ctx.nombre, ctx.sitio,
                                   f"sucesiva_{log_global.ts}",
                                   ctx.carpeta_logs, ctx.carpeta_convertir)
        detector = BlockDetector(ctx.bloqueo_max_errs, ctx.bloqueo_pausa)

        barra_caps = tqdm(
            caps_sel, unit="cap", desc=f"  {ctx.nombre[:20]}", ncols=65,
            bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]"
        ) if TQDM_OK else None

        procesados: list = []
        cap_en_curso = None
        c_dest = ctx.carpeta_convertir
        c_reg  = "Capitulos_a_Convertir"

        try:
            for cap in (barra_caps or caps_sel):
                if _INTERRUMPIDO.is_set():
                    break
                num_fmt = (str(int(cap["numero"])) if cap["numero"] == int(cap["numero"])
                           else str(cap["numero"]))
                if barra_caps:
                    barra_caps.set_postfix(cap=num_fmt)
                cap_en_curso = cap
                res = descargar_capitulo(
                    url=cap["url"], numero=cap["numero"],
                    con_filtro=True, carpeta_destino=c_dest,
                    reg=reg, carp_reg=c_reg, ctx=ctx,
                    logger=logger_ind, detector=detector,
                    adaptador=adaptador,
                )
                cap_en_curso = None
                procesados.append({"num": cap["numero"], "url": cap["url"],
                                   "res": res})
                if not _INTERRUMPIDO.is_set():
                    time.sleep(1)
        except Exception:
            pass
        finally:
            if _INTERRUMPIDO.is_set() and cap_en_curso is not None:
                num_str = (str(int(cap_en_curso["numero"]))
                           if cap_en_curso["numero"] == int(cap_en_curso["numero"])
                           else str(cap_en_curso["numero"]))
                cap_c = os.path.join(c_dest, f"Capitulo_{num_str}")
                n_p   = len([f for f in os.listdir(cap_c)
                              if f.lower().endswith((".jpg",".jpeg",".png",".webp",".gif"))]) \
                        if os.path.isdir(cap_c) else 0
                marcar_parcial(ctx, reg, c_reg, cap_en_curso["numero"], n_p)
            if barra_caps:
                barra_caps.close()
            ruta_log_ind = logger_ind.guardar()
            mostrar_reporte_final(procesados, f"sucesiva [{i}/{len(planes)}]",
                                  ruta_log_ind, ctx)

        # Registrar en log global
        log_global.agregar_manga(ctx.nombre, ctx.sitio, len(caps_sel), procesados)

        # Marcar como completado en sesión guardada
        plan["completado"] = True
        _guardar_sesion_sucesiva([
            {
                "manga_nombre": p["manga_cfg"]["nombre"],
                "caps_sel":     p["caps_sel"],
                "nuevos":       p["nuevos"],
                "ya_en_reg":    p["ya_en_reg"],
                "est_t":        p["est_t"],
                "completado":   p["completado"],
            }
            for p in planes
        ])

        if _INTERRUMPIDO.is_set():
            break

    # ── Guardar log global ────────────────────────────────────────────
    ruta_json, ruta_html = log_global.guardar()

    dur_total = int(time.time() - t_inicio_total)
    dur_str   = f"{dur_total//60}m {dur_total%60}s"

    print(f"\n{'═'*70}")
    print(f"  🏁  DESCARGA SUCESIVA COMPLETADA  —  {dur_str}")
    print(f"{'═'*70}")
    totales_gl = {
        "mangas":  len(log_global.mangas),
        "caps_ok": sum(m["exitosos"]  for m in log_global.mangas),
        "omitidos":sum(m["omitidos"]  for m in log_global.mangas),
        "fallidos":sum(m["fallidos"]  for m in log_global.mangas),
        "imgs":    sum(m["total_imgs"] for m in log_global.mangas),
    }
    print(f"  📚 Mangas   : {totales_gl['mangas']}")
    print(f"  ✅ Caps OK  : {totales_gl['caps_ok']}")
    print(f"  ⏭️  Omitidos: {totales_gl['omitidos']}")
    print(f"  ❌ Fallidos : {totales_gl['fallidos']}")
    print(f"  🖼️  Imágenes: {totales_gl['imgs']}")
    print(f"\n  📄 Log JSON : {ruta_json}")
    print(f"  🌐 Reporte  : {ruta_html}")
    print(f"{'═'*70}\n")

    # Limpiar sesión guardada si todo fue bien
    if not _INTERRUMPIDO.is_set():
        _borrar_sesion_sucesiva()

    # Notificación Windows
    mangas_fail = [m["nombre"] for m in log_global.mangas if m["fallidos"] > 0]
    msg_notif   = (f"{totales_gl['caps_ok']} caps descargados en {dur_str}. "
                   + (f"Fallidos: {', '.join(mangas_fail[:3])}" if mangas_fail else "Sin errores."))
    _notificar_windows("🎌 Descarga Sucesiva Completada", msg_notif)


# ══════════════════════════════════════════════════════════════════════
# §18 MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    cfg = cargar_config()
    while True:
        resultado = mostrar_menu_multimanga(cfg)
        if resultado is None:
            print("\n  👋 ¡Hasta luego!\n")
            break
        if resultado == "reload":
            cfg = cargar_config()
            continue
        if resultado == "sucesiva":
            ejecutar_descarga_sucesiva(cfg)
            continue
        manga_cfg = resultado
        ctx = crear_ctx(manga_cfg, cfg)
        ejecutar_sesion_manga(ctx)


if __name__ == "__main__":
    main()