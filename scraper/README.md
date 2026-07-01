# M4RTO Scraper

Scraper multi-sitio en Python que descarga capítulos de manga/manhwa/manhua a la carpeta local que sirve M4RTO Server. Corre en loop (escanea, espera, vuelve a escanear) o una sola vez con `--una-vez`, y todo lo que descarga queda inmediatamente disponible para el servidor.

Todo lo que se puede ajustar (intervalos, timeouts, filtros de imagen, prioridad de fuentes, etc.) vive centralizado al principio de `scraper.py`, en la sección `§1 CONFIGURACIÓN`, cada constante con su propio comentario explicando qué hace.

---

## 1. Sitios soportados

| Fuente (`fuente` en seguimiento.json) | Sitio | Tipo | Particularidad principal |
|---|---|---|---|
| `olympus` | Olympus Scanlation | API JSON (Nuxt/Vue) | Auto-recupera el slug si venció (404); el dato real sale de `dashboard.olympusxyz.com/api`, no del HTML |
| `temple` | Temple Scan | WordPress/Madara | Dominio se resuelve dinámicamente vía Supabase en cada arranque (el dominio cambia seguido) |
| `dragon` | Dragon Translation | WordPress/Madara | Dominio fijo (`dragontranslation.org`) |
| `manhwaweb` | ManhwasWEB | SPA React + API JSON | El HTML del frontend está vacío; todo sale del backend en Railway |
| `nexus` | Nexus Scanlation | API JSON (Next.js) | Imágenes a veces vienen "scrambled" (cortadas en grilla y reordenadas) — se reconstruyen con Pillow |
| `ikigai` | Ikigai Mangas | SSR Qwik | Dominios rotativos anti-bloqueo, con auto-detección de los nuevos |

Más detalle de cada uno en la sección 5.

---

## 2. Instalación y requisitos

- **Python 3.10+** (probado en 3.10; ojo con f-strings con backslash dentro de `{}`, eso recién se permite desde 3.12).
- Al arrancar, el script instala solo las dependencias que falten: `requests`, `beautifulsoup4`, `Pillow`, `tqdm`, `pycryptodome`. No hace falta `pip install` manual salvo para Selenium (ver abajo).
- **Selenium + Brave** (opcional, pero recomendado): Temple Scan y Dragon Translation a veces redirigen el lector de capítulos vía JavaScript, y en esos casos el scraper abre una instancia de **Brave** (no Chrome) con un perfil dedicado en `C:\brave-scraper` para renderizar la página y sacar las URLs de imagen. Si Selenium no está instalado, esos capítulos puntuales fallan en vez de usar el fallback — el resto de sitios no lo necesitan para nada.
  ```
  pip install selenium
  ```
  Brave debe estar instalado en la ruta estándar de Windows; la versión de ChromeDriver se detecta y descarga sola según la versión de Brave instalada.

---

## 3. Configuración (.env)

El scraper lee la carpeta de destino de los mangas desde el `.env` del proyecto del servidor (busca un nivel o dos arriba de `scraper/`):

```env
MANGA_PATH_SCRAPER=D:\Mangas
```

Si no encuentra `MANGA_PATH_SCRAPER`, usa `MANGA_PATH` como fallback (la misma variable que usa el servidor). Si no encuentra ninguna de las dos, cae a `./mangas` dentro del proyecto y avisa por log.

---

## 4. Ejecución

```bash
python scraper.py              # loop infinito: escanea, espera 3h, repite
python scraper.py --una-vez    # un solo ciclo de escaneo y sale (para pruebas)
```

- El intervalo entre ciclos es `INTERVALO_HORAS = 3` (ajustable en §1).
- Se crea un `scraper.lock` mientras corre, para que no se pisen dos instancias escribiendo a la vez `seguimiento.json`/`registro_progreso.json`. Si el lock tiene más de 6 horas se asume abandonado (corte de luz, proceso matado) y se ignora solo.
- `seguimiento.json` se guarda de forma **incremental**: después de terminar cada manga, no solo al final del ciclo completo. Si el proceso se corta a mitad de camino, no se pierde lo ya descargado.

---

## 5. Cómo funciona un ciclo de escaneo

Cada ciclo (`ciclo_escaneo`) hace, en orden:

1. **Carga `seguimiento.json`** y filtra solo los mangas con `"activo": true`.
2. **Normaliza `url_manga` → `slug`**: si una entrada tiene el campo `url_manga` con la URL completa pegada del navegador, se extrae el slug automáticamente y se guarda en `slug` (no hace falta calcular el slug a mano).
3. **Avisos de cordura** (`validar_multi_fuente`): si dos entradas comparten `nombre_carpeta`, avisa si hay fuentes repetidas por error de copy-paste, o fuentes distintas con la misma prioridad efectiva (ambigüedad a desambiguar a mano). Nunca frena el escaneo, solo loguea.
4. **Olympus va siempre primero** y aparte: en vez de visitar manga por manga, scrapea las páginas de novedades (`/capitulos?page=1..20`) para tener slugs siempre vigentes, y por cada manga de Olympus consulta igual su página de serie completa (nunca confía solo en "apareció en novedades", para no dejar huecos de capítulos viejos).
5. **El resto de sitios**, agrupados por fuente y escaneados en el orden de `ORDEN_FUENTES` (ver sección 6) — no importa el orden en que estén en el JSON.
6. Por cada manga: pide la lista de capítulos al sitio, descarta el capítulo `0` (casi siempre un placeholder de "fecha de lanzamiento" sin contenido real), detecta huecos, arma la lista de capítulos nuevos a bajar, y descarga.
7. Al final genera/actualiza `reporte.html` con el resumen del ciclo.

### Detección de huecos ("huecos")

Antes de bajar nada nuevo, compara el catálogo **real** del sitio (no un rango de números asumido) contra lo que hay completo en disco. Cualquier capítulo con número ≤ `ultimo_capitulo` que el sitio dice que existe pero no está completo en disco (lo borraste a mano, quedó corrupto, etc.) se marca para reintentar. Usar el catálogo real evita falsos positivos en mangas con numeración no secuencial (capítulos `.5`, saltos, especiales).

### Capítulos marcados con error

Si un capítulo falla por completo (no logra guardar ni una imagen), no queda ningún rastro en `registro_progreso.json` — por eso esos números se guardan aparte en `capitulos_con_error` dentro de `seguimiento.json`, y se reintentan en cada ciclo siguiente hasta que se logren bajar bien (momento en el que se sacan solos de la lista).

### Descarga de un capítulo

- Descarga en paralelo (4 hilos).
- Filtros adaptativos por sitio (ver `FILTROS` en §1): tamaño mínimo, ratio máximo (para descartar banners panorámicos), umbral de "ícono cuadrado", y tolerancia de ancho respecto al ancho dominante del capítulo (para descartar imágenes sueltas que no son páginas reales). Los webtoons (predominantemente verticales) usan una tolerancia de ancho más laxa (mínimo 35%) porque varían más de página a página. Si la entrada en `seguimiento.json` tiene `"tipo_contenido": "manga"`, se activa el **modo manga flexible**: el único filtro de dimensiones que aplica es que el ancho y el alto sean ≥ 100 px; no hay ratio máximo, ni umbral de cuadrado, ni tolerancia de ancho dominante. Esto cubre paneles a color, páginas dobles y capítulos escaneados con diferente equipo (que cambian el ancho de capítulo a capítulo) sin perder páginas legítimas. El filtro de ruido por nombre de archivo/URL sigue corriendo igual independientemente del modo.
- Filtro de ruido por nombre de archivo/ruta de URL (`banner`, `logo-`, `discord`, `/ads/`, `/avatar/`, etc.), independiente de las dimensiones.
- Si después de filtrar quedan muy pocas imágenes (menos que `fallback_min`, normalmente 2), entra en **modo relajado** y reintenta recuperar las rechazadas (salvo las de ratio extremo) — red de seguridad para capítulos con páginas de tamaño inusual que igual son legítimas.
- Cada imagen guardada se verifica con Pillow (`img.verify()`) para descartar archivos truncados o HTML de error guardado con extensión de imagen.
- El resultado queda en `registro_progreso.json` (dentro de la carpeta del manga) como `completado` (todas las imágenes esperadas están íntegras) o `parcial` (faltan algunas — se reintenta en el próximo ciclo).

### BlockDetector (anti-bloqueo)

Si un dominio específico acumula `BLOQUEO_MAX_ERRORES_CONSECUTIVOS = 8` errores seguidos (403/410/429/503, sin ningún éxito en el medio), se pausan las descargas de **ese dominio** durante `BLOQUEO_PAUSA_SEG = 30` segundos antes de seguir. Es por dominio, no global, así que un CDN de imágenes bloqueado no frena el resto.

---

## 6. Sistema multi-fuente y prioridad de descarga

Un mismo manga puede estar trackeado desde **2 o más sitios distintos a la vez**, apuntando a la misma carpeta (mismo `nombre_carpeta`). Esto sirve para quedarte siempre con la mejor versión disponible: si el sitio prioritario falla un capítulo puntual, el de respaldo lo cubre, y si más adelante el prioritario lo recupera, lo reemplaza automáticamente.

### Orden de prioridad

Definido en un único lugar (`ORDEN_FUENTES`), de mejor a peor:

```
1. olympus
2. nexus
3. temple
4. manhwaweb
5. dragon
6. ikigai
```

Este mismo orden se usa para dos cosas: en qué orden se escanean los sitios dentro de un ciclo, y la prioridad automática de cada manga si no se fuerza nada a mano. Una fuente nueva que no esté en esta lista cae al final, sin romper nada.

Se puede **forzar manualmente** la prioridad de una entrada puntual con el campo opcional `prioridad_fuente` (número, menor = mejor) en `seguimiento.json` — útil para overridear el orden automático en un manga específico sin tocar el orden global.

Existe además una prioridad especial, `externa` (rango `0`), reservada para capítulos que aparecieron en la carpeta de un manga **sin que ningún scraper los haya bajado** (los pusiste a mano, o son de antes de tener este sistema). Es mejor que cualquier fuente real: nada de lo que bajen los scrapers puede reemplazarlo nunca. Esto se asigna solo automáticamente cuando el sistema regenera el registro de un manga sin `registro_progreso.json` previo — **no es un valor que se ponga en el campo `fuente` de una entrada de seguimiento.json**.

### Cómo se resuelve un capítulo cuando hay 2+ fuentes

Antes de bajar cada capítulo, se consulta `registro_progreso.json` (compartido por todas las fuentes de ese manga, vive en la carpeta del manga) para ver qué fuente lo tiene actualmente:

- **Ya cubierto por una fuente de prioridad igual o mejor** → se omite, sin gastar ni una request. Importante: si la entrada actual es de peor prioridad, **no** avanza su propio `ultimo_capitulo`, para seguir revisando ese número en escaneos futuros (red de seguridad por si la fuente mejor llegara a perder ese capítulo más adelante).
- **Cubierto por una fuente peor, o no cubierto todavía** → se descarga, pero **nunca directo en la carpeta real**: va primero a una carpeta de staging (`_staging_{fuente}_{Capitulo_N}`). Solo si la descarga termina 100% completa se borra la versión vieja y se reemplaza por la nueva. Así, dos sitios que dividen el mismo capítulo en distinta cantidad de imágenes nunca dejan páginas mezcladas, y si la descarga falla a mitad de camino la versión vieja (que funciona) queda intacta.

Si un manga **no** tiene `prioridad_fuente` y solo tiene una entrada en `seguimiento.json` (caso normal, sin multi-fuente), nada de este cruce cambia el comportamiento de siempre — el manga participa igual con la prioridad automática de su sitio, pero como nunca aparece una entrada rival con la que comparar, es indistinguible del comportamiento simple de una sola fuente.

---

## 7. Detalle por sitio

**Olympus Scanlation** — Se consulta la API real (`dashboard.olympusxyz.com/api/series/{slug}/chapters`), nunca el HTML directo (es una SPA Nuxt/Vue). El `slug` guardado puede vencer (los slugs de Olympus rotan con timestamps); cuando eso pasa (404 explícito), el scraper busca el slug vigente recorriendo las páginas de novedades por nombre. Para no depender de eso, también persiste un `manga_id` (sacado de la URL de la portada) como identificador estable — una vez que existe en `seguimiento.json`, ya no hace falta resolver el slug para conseguir las imágenes.

**Temple Scan / Dragon Translation (Madara)** — Comparten la misma base de WordPress/Madara. Primero intentan parsear los capítulos directo del HTML de la página del manga; si el sitio solo los expone vía AJAX, caen a un POST a `/wp-admin/admin-ajax.php`. Temple además resuelve su dominio actual en cada arranque consultando un endpoint de Supabase (el dominio cambia seguido), con un dominio de respaldo fijo si Supabase no responde. Si el lector de un capítulo redirige vía JavaScript a otro dominio, ambos caen al fallback de Selenium+Brave descrito en la sección 2.

**ManhwasWEB** — El frontend (`manhwaweb.com`) es una SPA React sin contenido en el HTML; todo sale del backend (`manhwawebbackend-production.up.railway.app`). El `slug` en `seguimiento.json` es el `_id` completo del manga tal como aparece en la URL (ej: `gata-rebelde_1780473612629`), no un nombre simplificado. A diferencia del resto de sitios, ManhwasWEB publica tanto **manhwas** (tiras verticales de ancho uniforme) como **mangas** (páginas de ancho variable, paneles a color, etc.). Para los mangas conviene agregar `"tipo_contenido": "manga"` en su entrada de `seguimiento.json`, que activa el modo flexible descrito en la sección 5 — sin él, los filtros de ancho dominante pueden rechazar páginas legítimas cuando un capítulo viene escaneado a resolución distinta del resto.

**Nexus Scanlation** — API JSON propia (Next.js). Algunas imágenes vienen "scrambled": la API entrega, junto con la URL, un objeto `sc = {c, r, s}` (columnas, filas, semilla) que indica que la imagen está cortada en una grilla y las celdas reordenadas con un shuffle determinista (Fisher-Yates con PRNG mulberry32). El scraper reconstruye la imagen original con Pillow antes de guardarla — el algoritmo es el mismo que usa el lector web oficial (sacado de la extensión de Tachiyomi/Mihon para este sitio).

**Ikigai Mangas** — No necesita Selenium (el HTML ya viene server-rendered). El listado de series y el lector de capítulos viven en dominios distintos que rotan de forma independiente por anti-bloqueo; el scraper detecta los cambios de dominio solos (siguiendo las redirecciones) y los persiste en `ikigai_estado.json`, sin depender de ningún dominio fijo más que una semilla inicial. Si algún día todos los dominios conocidos dejan de responder a la vez, no hay forma automática de recuperarse — el scraper loguea un error explícito pidiendo un dominio nuevo a mano en vez de fallar en silencio. Los banners promocionales se descartan comparando la ruta exacta de la URL (viven en una carpeta `posts/misc/` separada de las páginas reales), no por dimensiones, porque comparten tamaño y clase CSS con páginas legítimas.

---

## 8. Archivos que genera el scraper

Todos viven en la misma carpeta que `scraper.py`, salvo `registro_progreso.json` que es por manga.

| Archivo | Para qué sirve |
|---|---|
| `scraper.log` | Log rotativo (10 MB × 3 backups = 40 MB máximo). |
| `reporte.html` | Reporte visual acumulativo de todos los escaneos (hasta 200 escaneos o 5 MB, lo que se cumpla primero). El historial completo está embebido como JSON dentro del propio HTML — no hay un `.json` aparte. |
| `registro_progreso.json` (uno por carpeta de manga) | Detalle por capítulo: imágenes esperadas/válidas, estado (`completado`/`parcial`), fuente y prioridad que lo descargó, fecha. Es el que permite el cruce multi-fuente y la detección de huecos. |
| `ikigai_estado.json` | Último dominio vigente conocido del listado y del lector de Ikigai (ver sección 7). |
| `scraper.lock` | Evita que corran dos instancias del scraper a la vez. Se borra solo al terminar; si queda huérfano por más de 6 horas, se ignora en el próximo arranque. |
| `debug_selenium.html` | Volcado crudo de la última página donde Selenium no encontró ninguna imagen — solo para diagnóstico manual, se sobreescribe cada vez. |

---

## 9. `seguimiento.json` — referencia de campos

Es una lista (`"mangas": [...]`) de objetos, uno por cada *entrada* (no por manga — un manga con 2 fuentes tiene 2 entradas con el mismo `nombre_carpeta`).

| Campo | Tipo | ¿Quién lo escribe? | Descripción |
|---|---|---|---|
| `nombre_carpeta` | string | **Vos** | Nombre exacto de la carpeta del manga dentro de `MANGA_PATH_SCRAPER`. Si dos entradas comparten este valor, el scraper las trata como el **mismo manga** bajado desde 2 fuentes (sistema multi-fuente de la sección 6). |
| `fuente` | string | **Vos** | Uno de: `olympus`, `nexus`, `temple`, `dragon`, `manhwaweb`, `ikigai`. |
| `url_manga` | string | **Vos** (opcional, recomendado) | URL completa pegada tal cual del navegador. El scraper extrae el `slug` solo a partir de esto — más cómodo que calcularlo a mano. Si lo ponés, no hace falta llenar `slug` (el scraper lo completa él mismo en el primer ciclo). |
| `slug` | string | Vos o automático (si pusiste `url_manga`) | Identificador del manga en el sitio. El formato exacto varía por sitio — ver sección 7 (en particular ManhwasWEB, que usa el `_id` completo, no un nombre simplificado). |
| `ultimo_capitulo` | número (float) | **Vos al crear la entrada** (después lo actualiza el scraper solo) | Último capítulo ya confirmado en disco. Poné `0` para bajar la serie completa desde el capítulo 1. **No lo edites a mano** salvo que quieras forzar que se revise/re-baje todo desde cierto número en adelante. |
| `capitulos_con_error` | lista de floats, o `null` | **Dejalo en `null`** | El scraper la administra solo (ver sección 5). Nunca pongas `[]` ni `0` — eso se reserva para distinguir "sin errores" de "el capítulo 0 tiene un error". |
| `activo` | booleano | **Vos** | `true` = se escanea en cada ciclo. `false` = la entrada se ignora completamente (para pausar un manga sin borrar su configuración). |
| `prioridad_fuente` | número entero | **Vos** (opcional, solo multi-fuente) | Solo hace falta si el mismo manga tiene 2+ entradas con distinta `fuente`. Menor = mejor. Si no lo ponés, se usa el orden automático de la sección 6. **Omitilo** en mangas de una sola fuente. |
| `tipo_contenido` | string | **Vos** (opcional) | Si se pone `"manga"`, activa el **modo manga flexible** al descargar: solo se descartan imágenes con ancho o alto < 100 px. No hay ratio máximo, ni umbral de cuadrado, ni tolerancia de ancho dominante. Útil para mangas en sitios que también publican manhwas (como ManhwasWEB), donde los filtros estándar pueden rechazar páginas legítimas con ancho variable. Si se omite, se aplican los filtros adaptativos normales del sitio. Funciona en cualquier fuente, no solo en `manhwaweb`. |
| `manga_id` | string | **Automático, solo Olympus** | Se autogenera y persiste la primera vez que el scraper escanea un manga de Olympus. No lo pongas a mano ni lo borres — sirve para no depender de que el `slug` siga vigente. |

---

## 10. Ejemplo de `seguimiento.json`

Cubre los casos típicos: manga simple de una sola fuente, manga inactivo, manga con capítulos pendientes de reintento, y un manga trackeado desde 2 fuentes a la vez (multi-fuente con prioridad forzada).

```json
{
  "mangas": [
    {
      "_comentario": "Caso simple: una sola fuente. Recién agregado, ultimo_capitulo=0 baja todo desde el cap 1. No hace falta 'prioridad_fuente' ni 'slug' a mano si pegás 'url_manga'.",
      "nombre_carpeta": "Ejemplo Manga Simple",
      "fuente": "temple",
      "url_manga": "https://aedexnox.akan01.com/serie/ejemplo-manga-simple/",
      "slug": "",
      "ultimo_capitulo": 0,
      "capitulos_con_error": null,
      "activo": true
    },
    {
      "_comentario": "Caso ya en marcha: ya tiene capítulos descargados (ultimo_capitulo=42), y el cap 38 falló por completo en un escaneo anterior — se reintenta solo en cada ciclo hasta que se logre bajar.",
      "nombre_carpeta": "Ejemplo Manga En Curso",
      "fuente": "nexus",
      "url_manga": "https://nexusscanlation.com/series/ejemplo-manga-en-curso",
      "slug": "ejemplo-manga-en-curso",
      "ultimo_capitulo": 42.0,
      "capitulos_con_error": [38.0],
      "activo": true
    },
    {
      "_comentario": "Manga pausado: la entrada se queda en el archivo (no se borra la config) pero el scraper la salta por completo mientras 'activo' esté en false.",
      "nombre_carpeta": "Ejemplo Manga Pausado",
      "fuente": "dragon",
      "url_manga": "https://dragontranslation.org/manga/ejemplo-manga-pausado/",
      "slug": "ejemplo-manga-pausado",
      "ultimo_capitulo": 15.0,
      "capitulos_con_error": null,
      "activo": false
    },
    {
      "_comentario": "Manga en ManhwasWEB: se agrega 'tipo_contenido': 'manga' porque el sitio también publica manhwas y los filtros estándar de ancho dominante pueden rechazar páginas legítimas (paneles a color, capítulos escaneados a distinta resolución). Con el modo flexible solo se descartan imágenes menores a 100x100 px.",
      "nombre_carpeta": "Ejemplo Manga En ManhwasWEB",
      "fuente": "manhwaweb",
      "url_manga": "https://manhwaweb.com/manhwa/ejemplo-manga-en-manhwasweb_1703742073447",
      "slug": "ejemplo-manga-en-manhwasweb_1703742073447",
      "ultimo_capitulo": 0,
      "capitulos_con_error": null,
      "activo": true,
      "tipo_contenido": "manga"
    },
    {
      "nombre_carpeta": "Ejemplo Manga Multi Fuente",
      "fuente": "olympus",
      "url_manga": "https://olympusxyz.com/series/comic-ejemplo-manga-multi-fuente",
      "slug": "comic-ejemplo-manga-multi-fuente",
      "ultimo_capitulo": 30.0,
      "capitulos_con_error": null,
      "activo": true,
      "manga_id": "184321"
    },
    {
      "_comentario": "Multi-fuente, mitad 2 de 2: misma 'nombre_carpeta' que la entrada anterior. Ikigai es la fuente de respaldo, así que se fuerza 'prioridad_fuente'=2 para que quede explícito (Ikigai ya es la peor por defecto en ORDEN_FUENTES, pero forzarlo acá evita cualquier ambigüedad si más adelante se agrega una tercera fuente).",
      "nombre_carpeta": "Ejemplo Manga Multi Fuente",
      "fuente": "ikigai",
      "url_manga": "https://visualikigai.aplikando.com/series/ejemplo-manga-multi-fuente/",
      "slug": "ejemplo-manga-multi-fuente",
      "ultimo_capitulo": 28.0,
      "capitulos_con_error": null,
      "activo": true,
      "prioridad_fuente": 2
    }
  ]
}
```

> **Importante**: los campos `_comentario` de arriba son solo para esta explicación — JSON real no soporta comentarios. El archivo `seguimiento.ejemplo.json` adjunto es la versión limpia y válida, lista para copiar entradas de ahí a tu `seguimiento.json` real.

---

## 11. Mantenimiento conocido / cosas a tener en cuenta

- **Olympus**: los slugs vencen con el tiempo (llevan timestamp). El sistema se auto-recupera buscando por nombre en las páginas de novedades, pero si el nombre del manga cambió mucho en el sitio de origen, esa búsqueda puede no encontrarlo — en ese caso conviene actualizar `url_manga` a mano. Pendiente (no implementado todavía): detectar el banner de "en mantenimiento" de Olympus en el HTML para loguear un mensaje explícito en vez de un 525 genérico.
- **Ikigai**: si algún día todos los dominios rotativos dejan de responder a la vez (cacheados + semilla), no hay recuperación automática — revisar `scraper.log` por un `SitioRotoError` pidiendo un dominio nuevo.
- **Capítulo 0**: se ignora siempre, en cualquier sitio (casi siempre es un placeholder de "fecha de lanzamiento" sin imágenes reales).
- **Capítulos decimales** (`.5`, etc.): soportados en todo el sistema (huecos, multi-fuente, nombre de carpeta `Capitulo_X.Y`).
- Nota menor (cosmética, no funcional): el banner ASCII del header del archivo dice `v1.6`, pero el mensaje de log al arrancar (`main()`) todavía dice `v1.0` — no afecta nada, pero conviene unificarlo en algún momento.
