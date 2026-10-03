# M4RTO Scraper

Scraper multi-sitio en Python que descarga capítulos de manga/manhwa/manhua a la carpeta local que sirve el servidor. Corre en loop (escanea, espera, vuelve a escanear) o una sola vez con `--una-vez`, y todo lo que descarga queda inmediatamente disponible para el servidor.

Todo lo que se puede ajustar (intervalos, timeouts, filtros de imagen, prioridad de fuentes, etc.) vive centralizado al principio de `scraper.py`, en la sección `§1 CONFIGURACIÓN`, cada constante con su propio comentario explicando qué hace.

> **Tip:** además de correrlo por terminal como describe este documento, se puede arrancar, detener y controlar fuente por fuente directamente desde el navegador — ver la sección "Panel del scraper" en el `README.md` de la raíz del proyecto. Ese panel termina escribiendo/leyendo los mismos archivos que se describen acá (`seguimiento.json`, `estado_vivo.json`), así que todo lo de este documento aplica igual sin importar cómo lo arranques.

---

## 1. Sitios soportados

| Fuente (`fuente` en seguimiento.json) | Sitio | Tipo | Particularidad principal |
|---|---|---|---|
| `olympus` | Olympus Scanlation | API JSON (Nuxt/Vue) | Auto-recupera el slug si venció (404); el dato real sale de `panel.olympusxyz.com/api`, no del HTML |
| `nexus` | Nexus Scanlation | API JSON (Next.js) | Imágenes a veces vienen "scrambled" (cortadas en grilla y reordenadas) — se reconstruyen con Pillow |
| `temple` | Temple Scan | WordPress/Madara | Dominio se resuelve dinámicamente vía Supabase en cada arranque (el dominio cambia seguido) |
| `dragon` | Dragon Translation | WordPress/Madara | Dominio fijo (`dragontranslation.org`) |
| `ikigai` | Ikigai Mangas | SSR Qwik | Dominios rotativos anti-bloqueo, con auto-detección de los nuevos |
| `taurus` | Tauro Scan | WordPress/Madara | Capítulos "programados" (bloqueados para no-VIP hasta su fecha de liberación gratuita) |
| `leercapitulo` | LeerCapitulo | Plataforma propia | Lista e imágenes en el HTML directo (`data-src`) — solo `requests` + BeautifulSoup, sin Selenium |
| `manhwaweb` | ManhwasWEB | SPA React + API JSON | El HTML del frontend está vacío; todo sale del backend en Railway |
| `tmo` | ZonaTMO | Plataforma propia (Laravel) | Puede tener varios grupos de scanlation subiendo el mismo capítulo — ver `grupo_preferido` en la sección 7 |

Más detalle de cada uno en la sección 7.

---

## 2. Instalación y requisitos

- **Python 3.10+** (probado en 3.10; ojo con f-strings con backslash dentro de `{}`, eso recién se permite desde 3.12).
- Al arrancar, el script instala solo las dependencias que falten: `requests`, `beautifulsoup4`, `Pillow`, `tqdm`, `pycryptodome`, `selenium` y `curl_cffi`. Si preferís instalarlas a mano: `pip install -r requirements.txt` (archivo en esta misma carpeta).
- **Selenium + Brave** (solo para algunas fuentes, ver abajo): Brave debe estar instalado en la ruta estándar de Windows; la versión de ChromeDriver se detecta y descarga sola según la versión de Brave instalada. El scraper usa un perfil de Brave **dedicado** (`C:\brave-scraper`, configurable con `BRAVE_PROFILE_DIR` al comienzo de `scraper.py`), así que no toca tu Brave personal.
  - **Temple Scan requiere sesión iniciada** — el scraper no inicia sesión solo: hereda la cookie de un login que hiciste a mano en ese perfil dedicado (abrí Brave con `--user-data-dir=C:\brave-scraper`, entrá a Temple Scan e iniciá sesión una vez). Cuando la sesión vence hay que repetir el login a mano; mientras tanto el scraper corta esa fuente de forma limpia y sigue con las demás.
  - **Dragon Translation y Tauro Scan** (Madara/WordPress, como Temple) lo necesitan solo *ocasionalmente*, cuando el lector de un capítulo puntual redirige vía JavaScript a otro dominio. Si Selenium no está instalado, esos capítulos puntuales fallan (quedan en `capitulos_con_error` y se reintentan cada ciclo) en vez de usar el fallback — el resto de esos sitios funciona igual sin él.
  - El resto de las fuentes (Olympus, Nexus, Ikigai, LeerCapitulo, ManhwasWEB, ZonaTMO) no lo usan para nada.

---

## 3. Configuración (.env)

El scraper lee la carpeta de destino de los mangas desde el `.env` del proyecto del servidor (busca un nivel o dos arriba de `scraper/`):

```env
MANGA_PATH_SCRAPER=D:\Mangas
```

Si no encuentra `MANGA_PATH_SCRAPER`, usa `MANGA_PATH` como fallback (la misma variable que usa el servidor). Si no encuentra ninguna de las dos, cae a `./mangas` dentro del proyecto y avisa por log.

> Si el servidor tiene configuradas varias carpetas de mangas (`MANGA_PATH_2`, `MANGA_PATH_3`, etc.), el scraper **solo** conoce y escribe en la carpeta de `MANGA_PATH_SCRAPER` (o su fallback) — no busca en las demás. Si un manga que seguís ya tiene sus capítulos guardados en otra de esas carpetas, asegurate de que coincida con la que usa el scraper antes de trackearlo, para no terminar con una carpeta duplicada del mismo manga en dos lugares distintos.

---

## 4. Ejecución

```bash
python scraper.py              # loop infinito: escanea, espera 3h, repite
python scraper.py --una-vez    # un solo ciclo de escaneo y sale (para pruebas)
```

- El intervalo entre ciclos es `INTERVALO_HORAS = 3` (ajustable en §1).
- Se crea un `scraper.lock` mientras corre, para que no se pisen dos instancias escribiendo a la vez `seguimiento.json`/`registro_progreso.json`. Si el lock tiene más de 6 horas se asume abandonado (corte de luz, proceso matado) y se ignora solo.
- `seguimiento.json` se guarda de forma **incremental**: después de terminar cada manga, no solo al final del ciclo completo. Si el proceso se corta a mitad de camino, no se pierde lo ya descargado.
- Mientras corre, actualiza `estado_vivo.json` con un heartbeat periódico (progreso del ciclo, manga actual, PID) — es lo que consulta el panel del navegador para mostrar el estado en vivo sin tener que leer los logs.

---

## 5. Cómo funciona un ciclo de escaneo

Cada ciclo (`ciclo_escaneo`) hace, en orden:

1. **Carga `seguimiento.json`** (estructura completa: `fuentes_activas` + `mangas`, ver sección 9) y filtra solo los mangas con `"activo": true`.
2. **Descarta fuentes apagadas globalmente** (`fuentes_activas`, ver más abajo) antes de tocar nada más.
3. **Normaliza `url_manga` → `slug`**: si una entrada tiene el campo `url_manga` con la URL completa pegada del navegador, se extrae el slug automáticamente y se guarda en `slug` (no hace falta calcular el slug a mano).
4. **Avisos de cordura** (`validar_multi_fuente`): si dos entradas comparten `nombre_carpeta`, avisa si hay fuentes repetidas por error de copy-paste, o fuentes distintas con la misma prioridad efectiva sin que todas la hayan fijado a mano (ambigüedad a desambiguar). Nunca frena el escaneo, solo loguea.
5. **Olympus va siempre primero** y aparte: en vez de visitar manga por manga, scrapea las páginas de novedades (`/capitulos?page=1..20`) para tener slugs siempre vigentes, y por cada manga de Olympus consulta igual su página de serie completa (nunca confía solo en "apareció en novedades", para no dejar huecos de capítulos viejos).
6. **El resto de sitios**, agrupados por fuente y escaneados en el orden de `ORDEN_FUENTES` (ver sección 6) — no importa el orden en que estén en el JSON.
7. Por cada manga: pide la lista de capítulos al sitio, descarta el capítulo `0` (casi siempre un placeholder de "fecha de lanzamiento" sin contenido real), detecta huecos, arma la lista de capítulos nuevos a bajar, y descarga.
8. Al final genera/actualiza `reporte.html` con el resumen del ciclo.

### Apagar una fuente completa sin tocar cada manga (`fuentes_activas`)

Además de `"activo": true/false` por manga individual, `seguimiento.json` tiene una llave a nivel raíz para apagar un **sitio entero** de una sola vez, sin editar cada entrada:

```json
{
  "fuentes_activas": { "temple": false },
  "mangas": [ ... ]
}
```

Cualquier fuente que **no** aparezca ahí se asume activa — omitirla en la config nunca la desactiva por accidente. Se relee entero en cada ciclo, así que apagar o prender una fuente no requiere reiniciar el proceso: el próximo ciclo ya lo toma solo. No borra ni toca nada en disco — los capítulos ya descargados quedan intactos, y `ultimo_capitulo` de esos mangas simplemente no avanza mientras la fuente esté apagada; al reactivarla, sigue exactamente donde había quedado. Esto es lo mismo que prende/apaga el `Administrador de fuentes` del panel del scraper en el navegador.

### Detección de huecos

Antes de bajar nada nuevo, compara el catálogo **real** del sitio (no un rango de números asumido) contra lo que hay completo en disco. Cualquier capítulo con número ≤ `ultimo_capitulo` que el sitio dice que existe pero no está completo en disco (lo borraste a mano, quedó corrupto, etc.) se marca para reintentar. Usar el catálogo real evita falsos positivos en mangas con numeración no secuencial (capítulos `.5`, saltos, especiales).

### Capítulos marcados con error

Si un capítulo falla por completo (no logra guardar ni una imagen), no queda ningún rastro en `registro_progreso.json` — por eso esos números se guardan aparte en `capitulos_con_error` dentro de `seguimiento.json`, y se reintentan en cada ciclo siguiente hasta que se logren bajar bien (momento en el que se sacan solos de la lista).

### Descarga de un capítulo

- Descarga en paralelo (4 hilos).
- Filtros adaptativos por sitio (ver `FILTROS` en §1): tamaño mínimo, ratio máximo (para descartar banners panorámicos), umbral de "ícono cuadrado", y tolerancia de ancho respecto al ancho dominante del capítulo (para descartar imágenes sueltas que no son páginas reales). Los webtoons (predominantemente verticales) usan una tolerancia de ancho más laxa (mínimo 35%) porque varían más de página a página. Si la entrada en `seguimiento.json` tiene `"tipo_contenido": "manga"`, se activa el **modo manga flexible**: el único filtro de dimensiones que aplica es que el ancho y el alto sean ≥ 100 px; no hay ratio máximo, ni umbral de cuadrado, ni tolerancia de ancho dominante. Esto cubre paneles a color, páginas dobles y capítulos escaneados con diferente equipo (que cambian el ancho de capítulo a capítulo) sin perder páginas legítimas. El filtro de ruido por nombre de archivo/URL sigue corriendo igual independientemente del modo. Funciona en cualquier fuente, no solo en las que publican manhwa por defecto (ManhwasWEB, ZonaTMO).
- Filtro de ruido por nombre de archivo/ruta de URL (`banner`, `logo-`, `discord`, `/ads/`, `/avatar/`, etc.), independiente de las dimensiones.
- Si después de filtrar quedan muy pocas imágenes (menos que `fallback_min`, normalmente 2), entra en **modo relajado** y reintenta recuperar las rechazadas (salvo las de ratio extremo) — red de seguridad para capítulos con páginas de tamaño inusual que igual son legítimas.
- Cada imagen guardada se verifica con Pillow (`img.verify()`) para descartar archivos truncados o HTML de error guardado con extensión de imagen.
- El resultado queda en `registro_progreso.json` (dentro de la carpeta del manga) como `completado` (todas las imágenes esperadas están íntegras) o `parcial` (faltan algunas — se reintenta en el próximo ciclo).

### BlockDetector (anti-bloqueo)

Si un dominio específico acumula `BLOQUEO_MAX_ERRORES_CONSECUTIVOS = 8` errores seguidos (403/410/429/503, sin ningún éxito en el medio), se pausan las descargas de **ese dominio** durante `BLOQUEO_PAUSA_SEG = 30` segundos antes de seguir. Es por dominio, no global, así que un CDN de imágenes bloqueado no frena el resto.

---

## 6. Sistema multi-fuente y prioridad de descarga

Un mismo manga puede estar trackeado desde **2 o más sitios distintos a la vez**, apuntando a la misma carpeta (mismo `nombre_carpeta`, dos o más entradas con distinta `fuente`). Esto sirve para quedarte siempre con la mejor versión disponible: si el sitio prioritario falla un capítulo puntual, el de respaldo lo cubre, y si más adelante el prioritario lo recupera, lo reemplaza automáticamente.

### Orden de prioridad

Definido en un único lugar (`ORDEN_FUENTES`), de mejor a peor:

```
1. olympus
2. nexus
3. temple
4. dragon
5. ikigai
6. taurus
7. leercapitulo
8. manhwaweb
9. tmo
```

Este mismo orden se usa para dos cosas: en qué orden se escanean los sitios dentro de un ciclo, y la prioridad automática de cada manga si no se fuerza nada a mano. Una fuente nueva que no esté en esta lista cae al final, sin romper nada.

Se puede **forzar manualmente** la prioridad de una entrada puntual con el campo opcional `prioridad_fuente` (número entero, menor = mejor) en `seguimiento.json` — útil para overridear el orden automático en un manga específico sin tocar el orden global.

Existe además una prioridad especial, `externa` (rango `0`), reservada para capítulos que aparecieron en la carpeta de un manga **sin que ningún scraper los haya bajado** (los pusiste a mano, o son de antes de tener este sistema). Es mejor que cualquier fuente real: nada de lo que bajen los scrapers puede reemplazarlo nunca. Esto se asigna solo automáticamente cuando el sistema regenera el registro de un manga sin `registro_progreso.json` previo — **no es un valor que se ponga en el campo `fuente` de una entrada de seguimiento.json**.

### Cómo se resuelve un capítulo cuando hay 2+ fuentes

Antes de bajar cada capítulo, se consulta `registro_progreso.json` (compartido por todas las fuentes de ese manga, vive en la carpeta del manga) para ver qué fuente lo tiene actualmente:

- **Ya cubierto por una fuente de prioridad estrictamente mejor** → se omite, sin gastar ni una request. Importante: la entrada actual (de peor prioridad) **no** avanza su propio `ultimo_capitulo` para ese número, para seguir revisándolo en escaneos futuros (red de seguridad por si la fuente mejor llegara a perder ese capítulo más adelante). La única excepción es cuando lo cubre la prioridad especial `externa` (rango 0): ahí sí se avanza `ultimo_capitulo`, porque nada le puede ganar nunca a `externa` y no tiene sentido seguir revisando ese número para siempre.
- **Cubierto por una fuente peor, o no cubierto todavía** → se descarga, pero **nunca directo en la carpeta real**: va primero a una carpeta de staging (`_staging_{fuente}_{Capitulo_N}`). Solo si la descarga termina 100% completa se borra la versión vieja y se reemplaza por la nueva. Así, dos sitios que dividen el mismo capítulo en distinta cantidad de imágenes nunca dejan páginas mezcladas, y si la descarga falla a mitad de camino la versión vieja (que funciona) queda intacta.

### "Doble prioridad": dos fuentes declaradas igual de confiables

Si dos (o más) entradas del mismo manga fijan el **mismo** `prioridad_fuente` a mano (por ejemplo, las dos en `1`), el sistema lo trata como una configuración soportada a propósito, no como un error: cuando un capítulo nuevo aparece en ambas fuentes a la vez, **gana la que lo descargue primero** (en ese ciclo o en uno anterior), y esa versión **queda fija** — la fuente empatada nunca la reemplaza después, sin importar el orden de escaneo.

Esto es distinto a dejar que las dos usen la prioridad automática de `ORDEN_FUENTES`: esa nunca empata entre fuentes distintas (cada una tiene su propia posición en la lista). Un empate en la práctica **siempre** fue puesto a mano. Por eso `validar_multi_fuente` distingue dos casos al arrancar un escaneo:

- Si **todas** las entradas empatadas fijaron `prioridad_fuente` a mano → lo informa como "doble prioridad soportada", sin sugerir arreglar nada.
- Si **alguna** quedó sin fijar y coincidió por casualidad con la de otra → avisa con un warning, porque probablemente no fue intencional.

Si un manga **no** tiene `prioridad_fuente` y solo tiene una entrada en `seguimiento.json` (caso normal, sin multi-fuente), nada de este cruce cambia el comportamiento de siempre — el manga participa igual con la prioridad automática de su sitio, pero como nunca aparece una entrada rival con la que comparar, es indistinguible del comportamiento simple de una sola fuente.

---

## 7. Detalle por sitio

**Olympus Scanlation** — Se consulta la API real del backend, nunca el HTML directo (es una SPA Nuxt/Vue). El dominio de esa API es `panel.olympusxyz.com` — el sitio lo cambió sin aviso en julio de 2026 (antes era `dashboard.olympusxyz.com`; el dominio principal `olympusxyz.com` nunca cambió, solo el subdominio del backend). El catálogo completo de series (`/api/series/list`, proxeado por el propio frontend) es el primer método para recuperar un slug vencido: primero busca por `manga_id` (si ya lo tenía guardado) y, si no aparece, por coincidencia exacta de nombre. Ese `manga_id` es un identificador numérico estable que se persiste solo la primera vez que el scraper escanea el manga — una vez que existe en `seguimiento.json`, ya no depende de que el slug (que rota con timestamps) siga vigente. **No conviene resolver el dominio vía `olympus.pages.dev`**: ese redirect puede llevar a dominios "decoy" (se vio redirigiendo a una página de aviso de "nos mudamos" que no era el backend real).

**Nexus Scanlation** — API JSON propia (Next.js). Algunas imágenes vienen "scrambled": la API entrega, junto con la URL, un objeto `sc = {c, r, s}` (columnas, filas, semilla) que indica que la imagen está cortada en una grilla y las celdas reordenadas con un shuffle determinista (Fisher-Yates con PRNG mulberry32). El scraper reconstruye la imagen original con Pillow antes de guardarla — el algoritmo es el mismo que usa el lector web oficial (sacado de la extensión de Tachiyomi/Mihon para este sitio).

**Temple Scan / Dragon Translation / Tauro Scan (Madara)** — Comparten la misma base de WordPress/Madara. Primero intentan parsear los capítulos directo del HTML de la página del manga; si el sitio solo los expone vía AJAX, caen a un POST a `/wp-admin/admin-ajax.php`. Temple además resuelve su dominio actual en cada arranque consultando un endpoint de Supabase (el dominio cambia seguido), con un dominio de respaldo fijo si Supabase no responde; Dragon y Tauro tienen dominio fijo. Si el lector de un capítulo redirige vía JavaScript a otro dominio, los tres caen al fallback de Selenium+Brave descrito en la sección 2. Tauro tiene una particularidad propia: marca en el HTML los capítulos "programados" (bloqueados para no-VIP hasta su fecha de liberación gratuita) con la clase `scheduled` — el parseo de Tauro también necesita un ajuste especial porque cada capítulo trae tres enlaces (título, fecha, vistas) en vez de uno solo, y hay que tomar únicamente el del título para no confundir "hace 1 día" con el número de capítulo.

**Ikigai Mangas** — No necesita Selenium (el HTML ya viene server-rendered). El listado de series y el lector de capítulos viven en dominios distintos que rotan de forma independiente por anti-bloqueo; el scraper detecta los cambios de dominio solos (siguiendo las redirecciones) y los persiste en `ikigai_estado.json`, sin depender de ningún dominio fijo más que una semilla inicial. Si algún día todos los dominios conocidos dejan de responder a la vez, no hay forma automática de recuperarse — el scraper loguea un error explícito pidiendo un dominio nuevo a mano en vez de fallar en silencio. Los banners promocionales se descartan comparando la ruta exacta de la URL (viven en una carpeta `posts/misc/` separada de las páginas reales), no por dimensiones, porque comparten tamaño y clase CSS con páginas legítimas.

**LeerCapitulo** — Plataforma propia (no Madara/WordPress). La lista de capítulos viene directo en el HTML (`#chapterList a.lc-chapter-row`, sin AJAX) y las imágenes vienen como `data-src` dentro de `#lcPages` en el HTML inicial, así que alcanza con `requests` + BeautifulSoup, igual que las demás fuentes simples: no necesita Selenium ni navegador. El sitio tuvo un desafío de Cloudflare entre el 19 y el 23 de septiembre de 2026 que obligaba a usar un navegador real; lo sacó por su cuenta. Si volviera a aparecer, el workaround (Brave + Selenium + perfil dedicado) quedó en el historial de git de `scraper.py`.

**ManhwasWEB** — El frontend (`manhwaweb.com`) es una SPA React sin contenido en el HTML; todo sale del backend (Railway). El `slug` en `seguimiento.json` es el `_id` completo del manga tal como aparece en la URL (ej: `gata-rebelde_1780473612629`), no un nombre simplificado. A diferencia del resto de sitios, ManhwasWEB publica tanto **manhwas** (tiras verticales de ancho uniforme) como **mangas** (páginas de ancho variable, paneles a color, etc.) — para estos últimos conviene agregar `"tipo_contenido": "manga"` en su entrada (ver sección 5), sin el cual los filtros de ancho dominante pueden rechazar páginas legítimas cuando un capítulo viene escaneado a resolución distinta del resto.

**ZonaTMO** — Plataforma propia (Laravel, sin nada de Madara/wp-manga). Todo server-rendered: la lista completa de capítulos viene en el HTML de la página del manga, sin paginar, y las imágenes salen directo del CDN (`storage.zonatmo.org`) sin lazy-load. La particularidad de este sitio es que **el mismo capítulo puede tener varias versiones subidas por distintos grupos de scanlation** — para eso existe el campo opcional `grupo_preferido`:

- **Solo entra en juego capítulo por capítulo**, y únicamente cuando ESE capítulo puntual tiene 2 o más versiones. Si tiene una sola, se usa directo — ni siquiera se mira `grupo_preferido`. En la práctica esto suele ser raro: de un manga con 149 capítulos, es común que solo 1 o 2 tengan más de una versión subida.
- **Con `grupo_preferido` configurado**: entre las versiones disponibles de ese capítulo, busca si el nombre de algún grupo contiene el texto configurado — comparación en minúsculas y por substring, no exacta (`"black"` matchea igual contra `"Blackdragonscan"` que `"Blackdragon"` o `"BLACKDRAGONSCAN"`). Si matchea, usa esa versión.
- **Si no matchea** (ese capítulo puntual no lo subió el grupo preferido, o no configuraste nada) → cae a la versión **más antigua**: compara por la fecha de subida real que trae cada versión; si por algún motivo no se puede leer la fecha, desempata por el ID numérico de `view_uploads` (que en la práctica sube en el mismo orden cronológico que la fecha).
- **El fallback a "más antigua" es silencioso a propósito** (es normal que un grupo no suba todos los capítulos), así que un typo en `grupo_preferido` podría pasar desapercibido para siempre. Por eso, al terminar de recorrer **todo** el manga, si configuraste `grupo_preferido`, hubo al menos un capítulo con varias versiones, y **nunca** matcheó ni una sola vez en ninguno → se tira **un solo warning al final** (no uno por capítulo) avisando que probablemente el nombre está mal escrito, y que se usó la versión más antigua en todos los casos.

Ejemplo real: en un manga donde solo el capítulo 144 tiene dos versiones (`TMO Bot` del 26/07 y `Blackdragonscan` del 02/08), con `"grupo_preferido": "blackdragon"` el capítulo 144 se descarga de `Blackdragonscan` (matcheó) y los demás de `TMO Bot` (única versión disponible, ni compara). Sin `grupo_preferido`, o con uno mal escrito, los 149 capítulos se bajan de `TMO Bot` porque su versión del 144 es la más antigua — y con un `grupo_preferido` mal escrito, además aparece el warning una vez al final avisando que nunca encontró ese grupo.

> Ver también `instrucciones_nuevo_sitio.txt` en esta misma carpeta — es la guía interna de reconocimiento y desarrollo usada para agregar cada uno de estos sitios, útil como referencia si en algún momento se suma un sitio nuevo.

---

## 8. Archivos que genera el scraper

Todos viven en la misma carpeta que `scraper.py`, salvo `registro_progreso.json` que es por manga.

| Archivo | Para qué sirve |
|---|---|
| `scraper.log` | Log rotativo (10 MB × 3 backups = 40 MB máximo). |
| `reporte.html` | Reporte visual acumulativo de todos los escaneos (hasta 200 escaneos o 5 MB, lo que se cumpla primero). El historial completo está embebido como JSON dentro del propio HTML — no hay un `.json` aparte. |
| `registro_progreso.json` (uno por carpeta de manga) | Detalle por capítulo: imágenes esperadas/válidas, estado (`completado`/`parcial`), fuente y prioridad que lo descargó, fecha. Es el que permite el cruce multi-fuente y la detección de huecos. |
| `ikigai_estado.json` | Último dominio vigente conocido del listado y del lector de Ikigai (ver sección 7). |
| `estado_vivo.json` | Heartbeat en vivo del ciclo actual (estado, manga en curso, progreso, PID) — lo consulta el panel del scraper en el navegador para mostrar la consola en tiempo real sin leer el log. No se versiona en Git (se reescribe constantemente). |
| `scraper.lock` | Evita que corran dos instancias del scraper a la vez. Se borra solo al terminar; si queda huérfano por más de 6 horas, se ignora en el próximo arranque. |
| `debug_selenium.html` | Volcado crudo de la última página donde Selenium no encontró ninguna imagen — solo para diagnóstico manual, se sobreescribe cada vez. |

---

## 9. `seguimiento.json` — referencia de campos

El archivo completo tiene dos partes a nivel raíz:

```json
{
  "fuentes_activas": { "temple": false },
  "mangas": [ ... ]
}
```

- **`fuentes_activas`** *(opcional)* — objeto `{ "fuente": true/false }` para apagar un sitio entero de una vez sin tocar cada manga (ver sección 5). Cualquier fuente que no aparezca acá se asume activa.
- **`mangas`** — lista de objetos, uno por cada *entrada* (no por manga — un manga con 2 fuentes tiene 2 entradas con el mismo `nombre_carpeta`).

Campos de cada entrada dentro de `mangas`:

| Campo | Tipo | ¿Quién lo escribe? | Descripción |
|---|---|---|---|
| `nombre_carpeta` | string | **Vos** | Nombre exacto de la carpeta del manga dentro de `MANGA_PATH_SCRAPER`. Si dos entradas comparten este valor, el scraper las trata como el **mismo manga** bajado desde 2 fuentes (sistema multi-fuente de la sección 6). |
| `fuente` | string | **Vos** | Uno de: `olympus`, `nexus`, `temple`, `dragon`, `ikigai`, `taurus`, `leercapitulo`, `manhwaweb`, `tmo`. |
| `url_manga` | string | **Vos** (opcional, recomendado) | URL completa pegada tal cual del navegador. El scraper extrae el `slug` solo a partir de esto — más cómodo que calcularlo a mano. Si lo ponés, no hace falta llenar `slug` (el scraper lo completa él mismo en el primer ciclo). |
| `slug` | string | Vos o automático (si pusiste `url_manga`) | Identificador del manga en el sitio. El formato exacto varía por sitio — ver sección 7 (Olympus, LeerCapitulo y ZonaTMO combinan `{id}/{nombre-slug}`; ManhwasWEB usa el `_id` completo, no un nombre simplificado). |
| `ultimo_capitulo` | número (float) | **Vos al crear la entrada** (después lo actualiza el scraper solo) | Último capítulo ya confirmado en disco. Poné `0` para bajar la serie completa desde el capítulo 1. **No lo edites a mano** salvo que quieras forzar que se revise/re-baje todo desde cierto número en adelante. |
| `capitulos_con_error` | lista de floats, o `null` | **Dejalo en `null`** | El scraper la administra solo (ver sección 5). Nunca pongas `[]` ni `0` — eso se reserva para distinguir "sin errores" de "el capítulo 0 tiene un error". |
| `activo` | booleano | **Vos** | `true` = se escanea en cada ciclo. `false` = la entrada se ignora completamente (para pausar un manga sin borrar su configuración). |
| `prioridad_fuente` | número entero | **Vos** (opcional, solo multi-fuente) | Solo hace falta si el mismo manga tiene 2+ entradas con distinta `fuente`. Menor = mejor. Si no lo ponés, se usa el orden automático de la sección 6. Poner el **mismo** valor en 2+ entradas activa el modo "doble prioridad" (ver sección 6). **Omitilo** en mangas de una sola fuente. |
| `tipo_contenido` | string | **Vos** (opcional) | Si se pone `"manga"`, activa el **modo manga flexible** al descargar: solo se descartan imágenes con ancho o alto < 100 px. No hay ratio máximo, ni umbral de cuadrado, ni tolerancia de ancho dominante. Útil para mangas en sitios que también publican manhwas (ManhwasWEB, ZonaTMO), donde los filtros estándar pueden rechazar páginas legítimas con ancho variable. Si se omite, se aplican los filtros adaptativos normales del sitio. Funciona en cualquier fuente. |
| `manga_id` | string | **Automático, solo Olympus** | Se autogenera y persiste la primera vez que el scraper escanea un manga de Olympus. No lo pongas a mano ni lo borres — sirve para no depender de que el `slug` siga vigente. |
| `grupo_preferido` | string | **Vos** (opcional, solo ZonaTMO) | Solo tiene efecto con `"fuente": "tmo"`. Nombre (o parte del nombre) del grupo de scanlation a preferir cuando un capítulo tiene varias versiones subidas — comparación por substring, insensible a mayúsculas. Ver el detalle completo en la sección 7. Si se omite, o no matchea, se usa siempre la versión más antigua disponible. |

---

## 10. Ejemplo de `seguimiento.json`

El archivo `seguimiento.json.example` (en esta misma carpeta) es la versión limpia y válida — copiá entradas de ahí a tu `seguimiento.json` real. Cubre: manga simple de una sola fuente, manga inactivo, manga con capítulos pendientes de reintento, `tipo_contenido: "manga"`, `fuentes_activas`, multi-fuente con prioridad forzada, y dos mangas de ZonaTMO con `grupo_preferido`.

Acá el mismo contenido pero **anotado** para explicar cada caso (los campos `_comentario` son solo para esta explicación — JSON real no soporta comentarios, así que no están en el `.example.json` de verdad):

```json
{
  "fuentes_activas": {
    "_comentario": "Apaga temple por completo sin tocar cada manga individual. Cualquier fuente que no aparezca acá (nexus, olympus, etc.) se asume activa.",
    "temple": false
  },
  "mangas": [
    {
      "_comentario": "Caso simple: una sola fuente. Recién agregado, ultimo_capitulo=0 baja todo desde el cap 1. No hace falta 'prioridad_fuente' ni 'slug' a mano si pegás 'url_manga'.",
      "nombre_carpeta": "Ejemplo Manga Simple",
      "fuente": "nexus",
      "url_manga": "https://nexusscanlation.com/series/ejemplo-manga-simple",
      "slug": "",
      "ultimo_capitulo": 0,
      "capitulos_con_error": null,
      "activo": true
    },
    {
      "_comentario": "Caso ya en marcha: ya tiene capítulos descargados (ultimo_capitulo=42), y el cap 38 falló por completo en un escaneo anterior — se reintenta solo en cada ciclo hasta que se logre bajar.",
      "nombre_carpeta": "Ejemplo Manga En Curso",
      "fuente": "temple",
      "url_manga": "https://aedexnox.akan01.com/serie/ejemplo-manga-en-curso/",
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
      "_comentario": "Multi-fuente, mitad 1 de 2: Olympus es la fuente principal (prioridad automática 1, la mejor de ORDEN_FUENTES). manga_id lo completa el scraper solo en el primer escaneo.",
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
      "_comentario": "Multi-fuente, mitad 2 de 2: misma 'nombre_carpeta' que la entrada anterior. Ikigai es la fuente de respaldo, así que se fuerza 'prioridad_fuente'=2 para que quede explícito (Ikigai ya es peor que Olympus por defecto en ORDEN_FUENTES, pero forzarlo acá evita cualquier ambigüedad si más adelante se agrega una tercera fuente).",
      "nombre_carpeta": "Ejemplo Manga Multi Fuente",
      "fuente": "ikigai",
      "url_manga": "https://visualikigai.aplikando.com/series/ejemplo-manga-multi-fuente/",
      "slug": "ejemplo-manga-multi-fuente",
      "ultimo_capitulo": 28.0,
      "capitulos_con_error": null,
      "activo": true,
      "prioridad_fuente": 2
    },
    {
      "_comentario": "ZonaTMO sin grupo_preferido: si algún capítulo llega a tener 2+ versiones, se queda siempre con la más antigua. Válido y común — la mayoría de los mangas de TMO no necesitan este campo.",
      "nombre_carpeta": "Camino A Buscar A Mi Madre",
      "fuente": "tmo",
      "url_manga": "https://zonatmo.org/library/manhwa/4155/camino-a-ver-a-mi-madre",
      "slug": "4155/camino-a-ver-a-mi-madre",
      "ultimo_capitulo": 0,
      "capitulos_con_error": null,
      "activo": true,
      "grupo_preferido": "TMO Bot"
    },
    {
      "_comentario": "ZonaTMO con grupo_preferido: de este manga, solo el capítulo 144 tiene 2 versiones subidas (TMO Bot y otro grupo) — es el único donde este campo hace algo. Los demás capítulos se bajan igual sin comparar nada, porque tienen una sola versión.",
      "nombre_carpeta": "Tensei Shitara Slime Datta Ken",
      "fuente": "tmo",
      "url_manga": "https://zonatmo.org/library/manga/30593/tensei-shitara-slime-datta-ken",
      "slug": "30593/tensei-shitara-slime-datta-ken",
      "ultimo_capitulo": 0,
      "capitulos_con_error": null,
      "activo": true,
      "grupo_preferido": "Blackdragonsscan"
    }
  ]
}
```

> **Importante**: los campos `_comentario` de arriba son solo para esta explicación — JSON real no soporta comentarios. El archivo `seguimiento.json.example` adjunto es la versión limpia y válida, lista para copiar entradas de ahí a tu `seguimiento.json` real.

---

## 11. Mantenimiento conocido / cosas a tener en cuenta

- **Olympus**: los slugs vencen con el tiempo (llevan timestamp). El sistema se auto-recupera por `manga_id` o por nombre exacto contra el catálogo, pero si el nombre del manga cambió mucho en el sitio de origen, esa búsqueda puede no encontrarlo — en ese caso conviene actualizar `url_manga` a mano. El dominio de la API (`panel.olympusxyz.com`) ya cambió una vez sin aviso (julio 2026) — si vuelve a pasar, revisar `scraper.log` por errores 401/404 masivos de esta fuente en particular.
- **Ikigai**: si algún día todos los dominios rotativos dejan de responder a la vez (cacheados + semilla), no hay recuperación automática — revisar `scraper.log` por un `SitioRotoError` pidiendo un dominio nuevo.
- **ZonaTMO / `grupo_preferido`**: si nunca aparece el warning de "no matcheó ninguna versión" pero tampoco parece estar tomando el grupo esperado, comparar el texto configurado contra el nombre EXACTO del grupo tal como aparece en el sitio — la comparación es por substring, así que un nombre demasiado genérico podría matchear con un grupo distinto al que se pensaba.
- **Tauro**: el manejo de capítulos "programados" (bloqueados para no-VIP) todavía no tiene un chequeo explícito de la clase `scheduled` — si en la práctica un capítulo bloqueado devuelve un aviso de "hazte VIP" en vez de simplemente no tener imágenes, hay que agregar ese chequeo a mano (ver el docstring de `TauroScraper` en `scraper.py`).
- **Capítulo 0**: se ignora siempre, en cualquier sitio (casi siempre es un placeholder de "fecha de lanzamiento" sin imágenes reales).
- **Capítulos decimales** (`.5`, etc.): soportados en todo el sistema (huecos, multi-fuente, nombre de carpeta `Capitulo_X.Y`).
- Nota menor (cosmética, no funcional): el banner ASCII del header del archivo dice `v1.8` y no incluye a ZonaTMO en su lista de sitios soportados, pero el mensaje de log al arrancar (`main()`) ya dice `v1.9` y el scraper de ZonaTMO está completo y activo — no afecta nada funcionalmente, pero conviene unificar el banner en algún momento.
