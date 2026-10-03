# Servidor Personal de Manga

Servidor personal para leer tu biblioteca de manga, manhwa y manhua desde cualquier dispositivo en tu red local. Funciona desde el navegador del celular, tablet o PC sin instalar nada en los dispositivos lectores.

---

## Índice

1. [Requisitos](#requisitos)
2. [Instalación](#instalación)
3. [Configuración](#configuración)
4. [Estructura de carpetas de mangas](#estructura-de-carpetas-de-mangas)
5. [Cómo usar el servidor](#cómo-usar-el-servidor)
   - [Inicio](#inicio)
   - [Series](#series)
   - [Rankings](#rankings)
   - [Últimos Capítulos](#últimos-capítulos)
   - [Búsqueda](#búsqueda)
   - [Detalle del manga](#detalle-del-manga)
   - [Lector de capítulos](#lector-de-capítulos)
   - [Panel de usuario](#panel-de-usuario)
6. [Usuarios y permisos](#usuarios-y-permisos)
7. [Scraper automático](#scraper-automático)
8. [Mejora de calidad con IA](#mejora-de-calidad-con-ia)
9. [Acceso desde otros dispositivos](#acceso-desde-otros-dispositivos)
10. [Acceso remoto con Tailscale](#acceso-remoto-con-tailscale)
11. [Solución de problemas](#solución-de-problemas)
12. [Estructura del proyecto](#estructura-del-proyecto)
13. [Notas de seguridad](#notas-de-seguridad)
14. [Aviso legal](#aviso-legal)

---

## Requisitos

Antes de instalar, asegúrate de tener lo siguiente en la PC que va a funcionar como servidor:

- **Node.js v20.9 o superior** (se recomienda la versión LTS) — [descargar en nodejs.org](https://nodejs.org/). Con versiones anteriores la instalación falla: la librería de miniaturas (`sharp`) exige Node 20.9+
- **Windows 10 / 11** — el lanzador `iniciar_servidor.bat` y el scraper con Brave están pensados para Windows. El servidor en sí también arranca en Linux/macOS con `npm install` y `npm start` (en ese caso configurá el `.env` a mano)
- Una carpeta con tus mangas organizados (ver [estructura de carpetas](#estructura-de-carpetas-de-mangas))
- Para acceso desde otros dispositivos: todos deben estar en la **misma red WiFi** (o usar [Tailscale](#acceso-remoto-con-tailscale) para acceder desde afuera)
- **Opcional** — para el [scraper automático](#scraper-automático): **Python 3.10+** y, para algunas fuentes, el navegador **Brave** (ver [`scraper/README.md`](scraper/README.md)). Las dependencias de Python se instalan solas la primera vez, o a mano con `pip install -r scraper/requirements.txt`. Sin esto el resto del servidor funciona igual.
- **Opcional** — para [mejorar la calidad de capítulos con IA](#mejora-de-calidad-con-ia): una GPU con soporte Vulkan (la gran mayoría de GPUs modernas, de cualquier fabricante, lo tienen). Sin esto el resto del servidor funciona igual; esa función puntual no va a estar disponible.

---

## Instalación

1. Descarga o clona el repositorio en tu PC: `git clone https://github.com/M4RT05/Servidor-de-Mangas.git`
2. Copia el archivo **`.env.example`** y renómbralo a **`.env`**
3. Completa los valores del `.env` (ver [Configuración](#configuración))
4. Doble clic en **`iniciar_servidor.bat`** — ejecutar como **Administrador** la primera vez para que configure el firewall automáticamente
5. Abre el navegador en `http://localhost:3000`

> **Primera vez:** El `.bat` instala las dependencias automáticamente con `npm install`. Puede tardar un minuto.

> **Nota:** cada vez que arrancás el servidor con el `.bat`, este limpia cualquier instancia anterior del propio servidor que haya quedado corriendo de una sesión previa (por ejemplo, si cerraste la ventana sin cortar el proceso). Esa limpieza es selectiva — solo apaga procesos de Node que sean *este* servidor, no toca otros programas Node.js que tengas abiertos en la misma PC.

---

## Configuración

Edita el archivo `.env` en la raíz del proyecto. El `.env.example` incluido tiene todos los campos con su explicación:

```env
# Credenciales del administrador principal
ADMIN_USERNAME=tu_usuario_aqui
ADMIN_PASSWORD=tu_contraseña_aqui

# Clave secreta para los tokens de sesión (texto largo y aleatorio)
# Si no se define, el servidor genera una en cada arranque (invalida todas las sesiones al reiniciar)
JWT_SECRET=cambia_esto_por_algo_muy_largo_y_aleatorio_1234567890

# Puerto del servidor (por defecto 3000)
PORT=3000

# Ruta a tu carpeta de mangas principal
MANGA_PATH=D:\Mis Mangas

# Carpetas adicionales de mangas (opcional — podés agregar MANGA_PATH_3, MANGA_PATH_4, etc.)
# El servidor las fusiona y las sirve todas juntas como una sola biblioteca.
# MANGA_PATH_2=E:\Mis Mangas 2

# Ruta donde el scraper descarga los capítulos (opcional — si no se define, usa MANGA_PATH)
# MANGA_PATH_SCRAPER=D:\Mis Mangas

# Carpeta donde se guardan las imágenes ORIGINALES antes de mejorarlas con IA
# (opcional — ver "Mejora de calidad con IA" más abajo). Si no se define, se
# usa una carpeta "_backups_ia" al lado de tu MANGA_PATH.
# IA_BACKUP_DIR=D:\Mis Mangas Originales

# Comando para invocar Python al arrancar el scraper desde el panel de administración
# (opcional — por defecto "python"; cambialo si no está en tu PATH)
# PYTHON_BIN=python

# Tamaño máximo (en MB) de la caché en memoria de imágenes (opcional — por defecto 200)
# IMAGE_CACHE_MB=200

# Poné true si tus mangas están en un disco de red o USB que no avisa de los cambios
# (opcional — ver "Los mangas no aparecen" en Solución de problemas)
# MANGA_WATCH_POLLING=true
```

> **Importante:** El archivo `.env` contiene tu contraseña y la clave de sesión. Nunca lo subas a GitHub. Ya está incluido en `.gitignore`.

> **Si tenés varias carpetas de mangas (`MANGA_PATH`, `MANGA_PATH_2`, etc.):** asegurate de que `MANGA_PATH_SCRAPER` apunte a la **misma** raíz donde ya vive cada manga existente antes de correr el scraper sobre él. El scraper descarga siempre a una única carpeta de destino — si un manga que seguís tiene sus capítulos ya guardados en una raíz distinta a `MANGA_PATH_SCRAPER`, el scraper puede terminar creando una carpeta duplicada con el mismo nombre en la carpeta equivocada, y esos capítulos nuevos podrían no aparecer en la biblioteca.

---

## Estructura de carpetas de mangas

El servidor espera que cada manga esté en su propia carpeta, con los capítulos como subcarpetas que contienen las imágenes:

```
📁 Mis Mangas/
├── 📁 Solo Leveling/
│   ├── 📄 cover.jpg          ← portada (opcional)
│   ├── 📄 metadata.json      ← metadata (opcional)
│   ├── 📁 Capitulo_1/
│   │   ├── 001.jpg
│   │   ├── 002.jpg
│   │   └── ...
│   ├── 📁 Capitulo_2/
│   └── ...
└── 📁 Otro Manga/
    └── ...
```

**`cover.jpg`** — Portada del manga. Si no existe, el servidor usa la primera imagen del primer capítulo automáticamente.

**`metadata.json`** — Información del manga. Si no existe, aparece con valores por defecto. Ejemplo (ver también `metadata.json.example` en la raíz del proyecto):

```json
{
  "type": "Manhwa",
  "status": "Activo",
  "genres": ["Acción", "Aventura", "Sistema"],
  "synopsis": "Sinopsis del manga...",
  "ranking": 1,
  "adult": false
}
```

| Campo | Valores posibles |
|---|---|
| `type` | `"Manga"`, `"Manhwa"`, `"Manhua"` |
| `status` | `"Activo"`, `"Hiatus"`, `"Finalizado"` |
| `genres` | Array de strings |
| `synopsis` | Texto libre |
| `ranking` | Número entero (1 = primero) o `null` |
| `adult` | `true` o `false` |

Este archivo se puede editar a mano o, más cómodo, desde el **Editor de metadata** del panel de administración (ver [Panel de usuario](#panel-de-usuario)) sin tocar archivos manualmente.

---

## Cómo usar el servidor

### Inicio

<img src="docs/screenshots/inicio.jpg" width="320" alt="Pantalla de inicio">

La pantalla principal tiene tres secciones, en este orden:

**Carrusel destacado** — Rota automáticamente entre 8 mangas al azar de tu biblioteca (solo los que tienen portada), mostrando sinopsis y géneros. Se puede navegar con las flechas, los puntos indicadores, o tocando "Ver detalles" para ir directo a esa serie. Se pausa solo si la pestaña pierde el foco, para no seguir avanzando en segundo plano.

**Seguir Leyendo** — Mangas que empezaste pero no terminaste, con barra de progreso y el último capítulo leído. Muestra hasta 8 por defecto; si tenés más en progreso, aparece una flecha para desplegar la lista completa. Toca una tarjeta para ir directamente al detalle del manga.

**Añadidos Recientemente** — Los 10 mangas más nuevos de tu biblioteca, ordenados por fecha de creación de la carpeta.

---

### Series

<img src="docs/screenshots/series.jpg" width="320" alt="Pantalla de series">

Vista completa de tu biblioteca. Cada manga se muestra como una card con portada, nombre, tipo, estado y número de capítulos.

**Ordenar** — El selector permite ordenar por A→Z, Z→A, más nuevos o más capítulos.

**Filtrar** — Toca el botón "Filtrar" para abrir el panel de filtros.

<img src="docs/screenshots/filtros.jpg" width="320" alt="Panel de filtros">

El panel de filtros permite combinar múltiples criterios:

- **Tipo** — Manga, Manhwa o Manhua
- **Géneros** — Solo muestra los géneros presentes en tu biblioteca. Si el contenido +18 está desactivado, los géneros adultos no aparecen aquí
- **Estado** — Activo, Hiatus o Finalizado

El número en el botón "Filtrar" indica cuántos filtros están activos. El botón "Limpiar" los borra todos.

---

### Rankings

<img src="docs/screenshots/rankings.jpg" width="320" alt="Pantalla de rankings">

Muestra los mangas ordenados por el campo `ranking` del `metadata.json`. Los que no tienen ranking asignado aparecen al final.

Cada card muestra la portada, el número de puesto y los badges de tipo y estado. El ranking se asigna editando el `metadata.json` de cada manga o usando el Editor de metadata desde el panel de usuario.

---

### Últimos Capítulos

<img src="docs/screenshots/capitulos.jpg" width="320" alt="Pantalla de últimos capítulos">

Lista los mangas ordenados por la fecha del capítulo más reciente, mostrando los 2 últimos capítulos de cada uno:

- **Punto amarillo** — capítulo no leído
- **Punto gris** — capítulo ya leído
- La fecha es relativa: "Hace 2 horas", "Hace 3 días", etc.

Toca el nombre del manga para ir a su detalle. Toca un capítulo para leerlo directamente. La lista está paginada de 20 en 20.

---

### Búsqueda

<img src="docs/screenshots/busqueda.jpg" width="320" alt="Pantalla de búsqueda">

Busca mangas por nombre en tiempo real mientras escribís. Muestra portada, nombre, tipo, estado y géneros de cada resultado.

La búsqueda normaliza acentos y mayúsculas — buscar `"accion"` encuentra mangas con el género `"Acción"`.

---

### Detalle del manga

<img src="docs/screenshots/detalle.jpg" width="320" alt="Detalle del manga">

Al tocar cualquier manga desde Inicio, Series, Rankings o Búsqueda se abre la vista de detalle con:

- **Portada** con efecto de degradado
- **Badges** de tipo, estado y ranking
- **Géneros** del manga
- **Sinopsis** con botón "más" si es larga
- **Lista de capítulos** con páginas, fecha y punto de color (amarillo = no leído, gris = leído)
- **Botones** para buscar capítulo por número, invertir el orden y marcar/desmarcar todos como leídos
- **Primer Capítulo** para empezar a leer directamente

---

### Lector de capítulos

El lector vive integrado dentro de la misma aplicación (ya no es una página aparte que recarga el navegador en cada capítulo) — cambiar de capítulo es instantáneo, y el botón "atrás" del navegador o del celular funciona de forma natural para volver al detalle del manga.

**Navegación de capítulos** — Flechas de anterior/siguiente tanto arriba como abajo de la pantalla, y un botón central que abre un selector para saltar directo a cualquier capítulo de la serie.

**Opciones de lectura** (botón de engranaje, arriba o abajo):

- **Modo de lectura** — *Scroll* (todas las páginas en una tira continua) o *Páginas* (una imagen a la vez, con toques a los costados para avanzar/retroceder).
- **Desplazamiento automático** — Solo disponible en modo Scroll. Avanza la página sola a una velocidad ajustable (10 a 500 px/s).
- **Modo noche** — Reduce el brillo y la luz azul de las imágenes mientras leés.
- **Mostrar en pantalla** — Activa o desactiva un panel flotante con accesos rápidos a modo noche y desplazamiento automático, sin tener que abrir el panel de opciones cada vez.

Todas estas preferencias se guardan en el dispositivo y se mantienen entre sesiones. Dentro del lector también podés hacer zoom con los dedos para ver el detalle del arte — algo que el resto de la app bloquea a propósito para que no interfiera con la navegación táctil.

El progreso de lectura se guarda automáticamente a medida que avanzás, capítulo por capítulo.

---

### Panel de usuario

<img src="docs/screenshots/usuario.jpg" width="320" alt="Panel de usuario">

Accesible tocando el avatar en la esquina superior derecha. Desde aquí podés:

**Configuración:**
- **Foto de perfil** — Tocá tu avatar dentro del panel para subir una imagen propia
- **Contenido +18** — Activa o desactiva la visibilidad de mangas marcados como adultos. Cuando está desactivado, esos mangas desaparecen de todas las secciones incluyendo búsqueda, filtros y rankings

**Temas visuales:**
- **Origins** — Fondo oscuro azulado con acento amarillo
- **Dark** — Fondo negro puro con acento gris claro
- **Lunar Tide** — Fondo azul claro
- **White** — Fondo blanco

**Mi actividad:**
- **Estadísticas de lectura** — Resumen de tu progreso: total de capítulos leídos, mangas completados, en progreso y no iniciados, distribución por tipo y estado
- **Exportar progreso** — Descarga un archivo `.json` con todo tu historial de lectura como backup
- **Importar progreso** — Restaura el progreso desde un archivo `.json` exportado anteriormente. Dos modos: *Reemplazar* (borra el actual) o *Combinar* (une ambos sin perder datos)

**Cuenta:**
- **Gestionar usuarios** *(solo admin)* — Crear, editar y eliminar usuarios, y configurar sus restricciones de contenido (ver [Usuarios y permisos](#usuarios-y-permisos))
- **Editor de metadata** *(solo admin)* — Editar tipo, estado, ranking, géneros, sinopsis y flag +18 de cada manga directamente desde el navegador, sin tocar archivos manualmente
- **Panel del scraper** *(solo admin)* — Arrancar y detener el scraper de descargas, ver su consola en vivo mientras corre, y activar/desactivar fuentes individuales, todo desde el navegador (celular incluido), sin necesidad de la terminal de la PC
- **Editor de seguimiento** *(solo admin)* — Agregar, editar y quitar los mangas que el scraper sigue, con detección automática de duplicados (incluyendo variantes con/sin tildes) al cargar uno nuevo
- **Cerrar sesión**

---

## Usuarios y permisos

El servidor soporta múltiples usuarios con dos roles: **admin** y **lector**.

### Roles

| | Admin | Lector |
|---|---|---|
| Ver biblioteca | ✅ | ✅ |
| Guardar progreso de lectura | ✅ | ✅ |
| Gestionar usuarios | ✅ | ❌ |
| Editar metadata de mangas | ✅ | ❌ |
| Controlar el scraper (arrancar/detener/fuentes) | ✅ | ❌ |
| Editar la lista de seguimiento del scraper | ✅ | ❌ |
| Configurar restricciones de otros usuarios | ✅ | ❌ |

El **admin principal** se define en el `.env` con `ADMIN_USERNAME` y `ADMIN_PASSWORD`. Los usuarios adicionales se crean desde el panel de administración en `Gestionar usuarios`.

### Restricciones por usuario

El admin puede configurar dos tipos de restricciones para cada lector desde el panel de administración:

**Contenido +18** — Si está desactivado para un usuario, todos los mangas con `"adult": true` en su `metadata.json` desaparecen completamente para ese usuario: no aparecen en la biblioteca, búsqueda, rankings, últimos capítulos ni estadísticas. El servidor devuelve 404 en todos los endpoints afectados.

**Mangas vetados** — Lista individual de mangas bloqueados para un usuario específico, independientemente del flag `adult`. Útil para ocultar series puntuales sin marcarlas como +18. Igual que con el contenido adulto, el servidor devuelve 404 en todos los endpoints para esos mangas.

Ambas restricciones se aplican completamente del lado del servidor — no es solo filtrado visual en el frontend.

### Progreso de lectura

Cada usuario tiene su propio historial de lectura independiente. El progreso de un usuario no afecta ni es visible para los demás.

---

## Scraper automático

El repositorio incluye un scraper en Python (`scraper/scraper.py`) que descarga capítulos automáticamente desde múltiples sitios de scanlation y los deja directamente en la carpeta que sirve el servidor.

**Sitios soportados:** Olympus, Nexus Scanlation, Temple Scan, Dragon Translation, Ikigai Mangas, Taurus, LeerCapitulo, ManhwasWEB y ZonaTMO.

**Características principales:**
- Corre en loop (escanea, espera, repite) o una sola vez con `--una-vez`
- Sistema multi-fuente: el mismo manga puede trackearse desde varios sitios a la vez, con prioridad configurable — si el sitio principal falla un capítulo, el de respaldo lo cubre
- Descarga en staging atómico: nunca deja capítulos a medio bajar en la carpeta real
- Reintentos automáticos de capítulos fallidos
- Detección de huecos comparando contra el catálogo real del sitio
- Lee `MANGA_PATH_SCRAPER` del `.env` del servidor (fallback a `MANGA_PATH`)
- Instala solo las dependencias de Python que falten la primera vez que corre (o a mano: `pip install -r scraper/requirements.txt`)

**Dos formas de correrlo:**

1. **Desde el navegador (recomendado para el día a día)** — Como admin, entrá a `Panel del scraper` desde tu perfil. Ahí podés arrancarlo en modo loop o un solo ciclo, verlo trabajar en vivo con una consola en tiempo real, y prenderle o apagarle fuentes individuales sin tocar ningún archivo. Funciona igual desde el celular que desde la PC.
2. **Desde la terminal** —
   ```bash
   cd scraper
   python scraper.py           # loop continuo
   python scraper.py --una-vez # un solo escaneo
   ```

Los mangas a seguir se configuran en `scraper/seguimiento.json` (copiar desde `scraper/seguimiento.json.example`, o agregarlos directamente desde el `Editor de seguimiento` del panel de administración). Para la documentación completa del scraper, los campos de configuración, el sistema multi-fuente y el detalle de cada sitio, ver [`scraper/README.md`](scraper/README.md).

---

## Mejora de calidad con IA

El servidor puede mejorar la calidad de los capítulos ya descargados usando el motor de [Upscayl](https://github.com/upscayl/upscayl) (basado en Real-ESRGAN), eligiendo un ancho objetivo para toda la corrida en vez de un factor de escala fijo.

**Características principales:**
- 7 modelos a elegir (Digital Art, Upscayl Standard, Upscayl Lite, High Fidelity, Remacri, Ultramix Balanced, Ultrasharp) — Digital Art viene por defecto, pensado para línea/arte digital como el manga
- Ancho de salida configurable (px), igual para toda la corrida; formato de salida WebP, PNG o JPG
- Por página: si ya está en el ancho y formato pedidos (o mejor), se saltea sola — reintentar un capítulo incompleto no repite trabajo de más
- Se puede elegir mejorar un manga completo, un rango de capítulos o capítulos puntuales (tocándolos en la lista visual del panel)
- 2 formas de detener una corrida: terminar el capítulo actual y parar, o parar ya (ese capítulo queda incompleto y se reintenta solo en la próxima corrida)
- El original de cada página se resguarda siempre antes de reemplazarla (nunca se pisa un backup ya existente), en la carpeta que definas en `IA_BACKUP_DIR` o, si no la definís, en una carpeta `_backups_ia` al lado de tu `MANGA_PATH`
- Si el servidor se corta a mitad de una mejora (corte de luz, cierre forzado), el capítulo afectado se detecta solo como incompleto y se retoma en la próxima corrida, sin intervención manual

**El motor (binario de Upscayl + modelos, ~170-200MB) no viene incluido en el repositorio** — se descarga aparte, desde el repo oficial de Upscayl, la primera vez que lo necesitás. Dos formas de instalarlo:

1. **Desde el panel (recomendado)** — Como admin, entrá a `Mejora con IA` desde tu perfil. Si el motor no está instalado vas a ver un aviso con el botón **"Instalar motor ahora"**, que descarga todo con una barra de progreso y log en vivo, sin tocar la terminal.
2. **Desde la terminal** —
   ```bash
   npm run instalar-ia
   ```
   Si se corta la descarga a la mitad, volvé a correr el mismo comando (o tocá el botón de nuevo): lo que ya se bajó no se repite.

> **Nota:** Upscayl es software libre bajo licencia AGPL-3.0. El binario y los modelos se descargan directo de su repositorio oficial en GitHub en vez de comitearse a este repo — así no hay que redistribuirlos acá y es más fácil mantenerlos al día.

---

## Acceso desde otros dispositivos

Para acceder desde el celular u otro PC en la misma red WiFi:

1. El servidor muestra la IP local al arrancar — buscá la línea `📱 Red local:`
2. Escribí esa URL en el celular: `http://192.168.x.x:3000`
3. El celular y la PC deben estar en el **mismo router/WiFi**

Si necesitás encontrar la IP manualmente, abrí `cmd` y ejecutá:
```
ipconfig
```
Buscá la sección "Adaptador de Wi-Fi" → **Dirección IPv4**.

---

## Acceso remoto con Tailscale

Para leer desde fuera de tu casa (datos móviles, otra red WiFi) sin exponer el servidor a internet, podés usar **Tailscale** — una VPN gratuita que conecta tus dispositivos como si estuvieran en la misma red local.

**Configuración:**

1. Instalá Tailscale en la PC del servidor: [tailscale.com/download](https://tailscale.com/download)
2. Instalá Tailscale en el celular (disponible en App Store y Play Store)
3. Iniciá sesión con la misma cuenta en ambos dispositivos
4. En Tailscale, cada dispositivo recibe una IP fija del rango `100.x.x.x`
5. Usá la IP de Tailscale de tu PC para acceder al servidor: `http://100.x.x.x:3000`

**Ventajas:**
- No necesitás abrir puertos en el router
- El tráfico va cifrado
- La IP de Tailscale no cambia aunque te muevas de red

**Notas:**
- La PC del servidor debe tener Tailscale activo y estar encendida
- Si la página carga pero no muestra las imágenes, asegurate de que el servidor esté escuchando en `0.0.0.0` (ya está configurado así por defecto)
- Tailscale puede convivir con el acceso por red local sin problema

---

## Solución de problemas

### El servidor no arranca

**`Node.js no está instalado`**

Instalá Node.js v18 o superior desde [nodejs.org](https://nodejs.org/) y reiniciá la PC.

---

**`Error: listen EADDRINUSE :::3000`** — Puerto en uso

Hay otro proceso usando el puerto 3000. Cerralo o cambiá el `PORT` en el `.env`:

```powershell
# Ver qué proceso usa el puerto
netstat -ano | findstr :3000
# Matar el proceso (reemplazá <PID> por el número que apareció)
taskkill /f /pid <PID>
```

---

**`No se encontraron carpetas de mangas`**

El `MANGA_PATH` del `.env` no existe o está mal escrito. Verificá:
- Que la ruta exista en tu disco
- Que no tenga comillas: `MANGA_PATH=D:\Mis Mangas` ✅ — `MANGA_PATH="D:\Mis Mangas"` ❌
- Que la carpeta no esté vacía

---

### No conecta desde el celular

**1 — Verificar que el servidor esté corriendo**

Abrí `http://localhost:3000` en la PC. Si no carga, el servidor no está iniciado.

**2 — Verificar que estén en la misma red**

El celular y la PC deben estar conectados al mismo router.

**3 — Abrir el puerto en el Firewall de Windows**

Abrí PowerShell como **Administrador** y ejecutá:

```powershell
netsh advfirewall firewall add rule name="MangaServer Puerto 3000" dir=in action=allow protocol=TCP localport=3000 profile=private,domain
```

**4 — Cambiar la red de Pública a Privada**

`Win + I` → Red e Internet → WiFi → clic en tu red → **Perfil de red: Privado**

O por PowerShell:
```powershell
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private
```

**5 — Verificar conectividad**

```powershell
# Reemplazá con la IP de tu PC
Test-NetConnection -ComputerName 192.168.1.x -Port 3000
```

Si `TcpTestSucceeded` es `True`, el servidor es accesible. Si el celular aún no conecta, el router puede tener **Client Isolation** activado — desactivalo en la configuración del router.

**6 — Eliminar la regla genérica "Node.js JavaScript Runtime" del Firewall**

Cuando corriste `node.exe` por primera vez (con este proyecto o con cualquier otro), es común que Windows haya creado —sola, o a través del cuadro de diálogo "Windows Defender Firewall bloqueó algunas características de Node.js Javascript Runtime"— una regla **genérica** con ese nombre, que aplica a *todos* los procesos de Node.js de la PC, no solo a este servidor. Esa regla puede haber quedado mal configurada (por ejemplo, permitiendo solo la red privada, o habiendo sido bloqueada sin querer al cerrar el aviso), y como Windows Firewall no avisa cuál regla terminó ganando cuando hay varias que aplican al mismo proceso, puede tapar en silencio a la regla específica que `iniciar_servidor.bat` crea automáticamente (`MangaServer Puerto 3000`) — el síntoma típico es que `localhost` en la propia PC funciona siempre, pero desde el celular u otro dispositivo a veces conecta y a veces no, sin que cambies nada.

La solución es borrar esa regla genérica y dejar que mande únicamente la regla específica del puerto, que es más confiable porque solo se activa para ese puerto en particular. Abrí PowerShell como **Administrador** y ejecutá:

```powershell
# Elimina la regla genérica de Node.js
Remove-NetFirewallRule -DisplayName "Node.js JavaScript Runtime"

# Confirma que ya no existe (si no imprime nada, se borró correctamente)
Get-NetFirewallRule -DisplayName "Node.js JavaScript Runtime" -ErrorAction SilentlyContinue
```

`-ErrorAction SilentlyContinue` en el segundo comando es solo para que no tire un error rojo en pantalla si la regla ya no existe — no hace falta preocuparse si no devuelve nada, es el resultado esperado.

Después de borrarla, no necesitás crear nada a mano: la próxima vez que arranques el servidor con `iniciar_servidor.bat`, este ya verifica si la regla `MangaServer Puerto %PORT%` existe y la vuelve a crear si hace falta (ver el paso 3 más arriba). Si Windows te muestra de nuevo el aviso de "Windows Defender Firewall bloqueó..." la próxima vez que arranques el servidor, tocá **Permitir el acceso** y marcá **Redes privadas** — eso vuelve a crear la regla genérica, así que si el problema reaparece, repetí este paso.

---

### Los mangas no aparecen

- Verificá que las carpetas de mangas sean directorios (no archivos ZIP sin descomprimir)
- Verificá que dentro de cada manga haya al menos una subcarpeta de capítulo con imágenes
- Los formatos de imagen válidos son: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`
- Si tenés varias carpetas de mangas (`MANGA_PATH_2`, `MANGA_PATH_3`, etc.) y el manga faltante lo bajó el scraper hace poco, revisá que no haya quedado duplicado en la carpeta equivocada (ver la nota en [Configuración](#configuración))
- Los mangas y capítulos nuevos se detectan solos en pocos segundos. Si tu biblioteca está en un disco de red o en un HDD USB que no avisa de los cambios, agregá `MANGA_WATCH_POLLING=true` al `.env`; como último recurso, reiniciá el servidor

---

### Las imágenes no cargan

- Cerrá sesión y volvé a entrar para renovar el token
- Evitá caracteres especiales como `#`, `?` o `%` en los nombres de carpetas

---

### No puedo entrar a un panel de administración

Los paneles de `Gestionar usuarios`, `Editor de metadata`, `Panel del scraper` y `Editor de seguimiento` son exclusivos para cuentas con rol **admin**. Si entrás con una cuenta de lector, el servidor rechaza cualquier acción sobre esos paneles aunque tengas sesión iniciada.

---

### El progreso no se guarda

El progreso se guarda en `server/progress.json`. Verificá que la carpeta `server/` tenga permisos de escritura.

---

## Estructura del proyecto

```
Servidor-de-Mangas/
├── client/                        ← Frontend (SPA sin build step)
│   ├── index.html                 ← Aplicación principal (incluye el lector integrado)
│   ├── login.html                 ← Pantalla de login
│   ├── stats.html                 ← Estadísticas de lectura
│   ├── reader.html                ← Redirección de compatibilidad a "/" (el lector real vive en index.html)
│   ├── css/
│   │   ├── app.css                ← Estilos (mobile-first)
│   │   └── themes.css             ← Los 4 temas visuales (Origins, Dark, Lunar Tide, White)
│   ├── js/
│   │   ├── api.js                 ← Cliente HTTP con caché, tokens y API.ready
│   │   ├── router.js              ← Ruteo de la SPA (URLs con slug, historial del navegador)
│   │   ├── app.js                 ← Estado global, home, carrusel, series, rankings, capítulos
│   │   ├── ui.js                  ← Componentes visuales y cards reutilizables
│   │   ├── detail.js              ← Vista de detalle de manga
│   │   └── reader.js              ← Lector de capítulos (scroll/páginas, auto-scroll, modo noche)
│   └── admin/
│       ├── users.html             ← Gestión de usuarios y permisos
│       ├── metadata.html          ← Editor de metadata
│       ├── scraper.html           ← Panel de control del scraper (consola en vivo, fuentes)
│       ├── seguimiento.html       ← Editor de la lista de seguimiento del scraper
│       └── mejora-ia.html         ← Panel de mejora de calidad con IA
├── scraper/                       ← Scraper automático (Python)
│   ├── scraper.py                 ← Script principal (~8.000 líneas, 9 fuentes)
│   ├── requirements.txt           ← Dependencias de Python (también se instalan solas)
│   ├── seguimiento.json.example   ← Plantilla de mangas a seguir
│   ├── instrucciones_nuevo_sitio.txt ← Guía para agregar un sitio nuevo al scraper
│   ├── dominios_fuente.json       ← Dominios válidos por fuente (usado para validar en el editor web)
│   └── README.md                  ← Documentación completa del scraper
├── server/                        ← Backend (Node.js + Express)
│   ├── index.js                   ← Entrada del servidor, estáticos, imágenes protegidas
│   ├── middleware/
│   │   ├── auth.js                ← Verificación JWT (header Authorization)
│   │   ├── cookieOrHeaderAuth.js  ← Igual que auth.js, pero también acepta cookie httpOnly (para <img>)
│   │   └── restrictions.js        ← Aplicación de restricciones de contenido por usuario
│   ├── routes/
│   │   ├── auth.js                ← Login, usuarios, avatares
│   │   ├── manga.js                ← Biblioteca, capítulos, progreso, metadata
│   │   ├── scraperControl.js      ← Arrancar/detener el scraper, consola en vivo (SSE), fuentes, seguimiento
│   │   └── upscaleControl.js      ← Control de la mejora IA: cola, 2 modos de parada, instalación del motor
│   ├── data/
│   │   ├── usersStore.js          ← Lectura/escritura de usuarios
│   │   ├── catalogIndex.js        ← Índice de la biblioteca en memoria (evita releer el disco en cada request)
│   │   └── users.json             ← Usuarios registrados (se crea solo; no se sube a Git)
│   ├── motor-ia/                   ← Binario + modelos de Upscayl (NO versionado, ver "Mejora de calidad con IA")
│   ├── upscale-state/              ← Estado en vivo de la mejora IA (NO versionado)
│   └── lib/
│       ├── slug.js                ← Generación de slugs para URLs amigables
│       ├── sources.js             ← Lista de fuentes de descarga conocidas (espejo de las del scraper)
│       ├── fsHelpers.js           ← Utilidades de sistema de archivos (escritura atómica, orden natural)
│       ├── imageCache.js          ← Caché LRU en memoria para imágenes servidas
│       ├── thumbnails.js          ← Generación de miniaturas WebP
│       ├── visibility.js          ← Regla única de "¿este usuario puede ver este manga?"
│       ├── upscaler.js            ← Motor de mejora IA: resolución del binario, clasificación por página, spawn
│       └── motorInstaller.js      ← Descarga el binario/modelos de Upscayl (botón del panel y script de terminal)
├── scripts/
│   └── instalarMotorIA.js         ← Instala el motor de IA desde la terminal (npm run instalar-ia)
├── docs/screenshots/               ← Capturas de pantalla
├── .env                            ← Configuración local (NO subir a Git)
├── .env.example                    ← Plantilla de configuración
├── .gitignore / .gitattributes     ← Reglas de Git
├── metadata.json.example           ← Ejemplo de metadata.json
├── iniciar_servidor.bat            ← Lanzador Windows
├── package.json
└── package-lock.json               ← Versiones exactas de las dependencias
```

---

## Notas de seguridad

- Diseñado para **red local privada** (directamente o vía Tailscale), no para exposición directa a internet
- El archivo `.env` está en `.gitignore` — nunca lo subás al repositorio
- Si no definís `ADMIN_PASSWORD`, el servidor crea el admin con la contraseña `admin` (y avisa por consola): definila siempre en el `.env` antes de abrir el servidor a otros dispositivos
- Si no definís `JWT_SECRET`, se genera una nueva en cada arranque y todas las sesiones se cierran al reiniciar
- Los tokens JWT expiran en 30 días
- Protección contra fuerza bruta en el login: bloquea una IP por 5 minutos tras 10 intentos fallidos
- Las imágenes requieren sesión activa para ser accesibles: al iniciar sesión el navegador recibe una cookie httpOnly que las autentica automáticamente; el token en la URL de cada imagen queda solo como respaldo, por si el navegador bloquea esa cookie
- Las restricciones de contenido (+18 y mangas vetados) se aplican del lado del servidor en todos los endpoints, no solo como filtro visual
- Los paneles de administración (usuarios, metadata, scraper, seguimiento, mejora con IA) verifican el rol de admin tanto en el navegador como, de forma independiente y obligatoria, en el servidor

---

## Aviso legal

Este proyecto es una herramienta de uso personal y **no incluye ni distribuye ningún manga**. El scraper descarga contenido de sitios de terceros: usalo bajo tu propia responsabilidad, respetando los términos de uso de cada sitio y las leyes de derechos de autor de tu país. Las capturas de `docs/screenshots` muestran portadas de obras que pertenecen a sus respectivos autores y editoriales, y están solo con fines ilustrativos.

---

*Desarrollado por M4RT05*
