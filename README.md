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
   - [Panel de usuario](#panel-de-usuario)
6. [Usuarios y permisos](#usuarios-y-permisos)
7. [Scraper automático](#scraper-automático)
8. [Acceso desde otros dispositivos](#acceso-desde-otros-dispositivos)
9. [Acceso remoto con Tailscale](#acceso-remoto-con-tailscale)
10. [Solución de problemas](#solución-de-problemas)
11. [Estructura del proyecto](#estructura-del-proyecto)

---

## Requisitos

Antes de instalar, asegúrate de tener lo siguiente en la PC que va a funcionar como servidor:

- **Node.js v18 o superior** — [descargar en nodejs.org](https://nodejs.org/)
- **Windows 10 / 11**
- Una carpeta con tus mangas organizados (ver [estructura de carpetas](#estructura-de-carpetas-de-mangas))
- Para acceso desde otros dispositivos: todos deben estar en la **misma red WiFi**

---

## Instalación

1. Descarga o clona el repositorio en tu PC
2. Copia el archivo `.env.example` y renómbralo a `.env`
3. Completa los valores del `.env` (ver [Configuración](#configuración))
4. Doble clic en **`iniciar_servidor.bat`** — ejecutar como **Administrador** la primera vez para que configure el firewall automáticamente
5. Abre el navegador en `http://localhost:3000`

> **Primera vez:** El `.bat` instala las dependencias automáticamente con `npm install`. Puede tardar un minuto.

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

# Segunda carpeta de mangas (opcional — podés agregar MANGA_PATH_3, MANGA_PATH_4, etc.)
# MANGA_PATH_2=E:\Mis Mangas 2

# Ruta donde el scraper descarga los capítulos (opcional — si no se define, usa MANGA_PATH)
# MANGA_PATH_SCRAPER=D:\Mis Mangas
```

> **Importante:** El archivo `.env` contiene tu contraseña. Nunca lo subas a GitHub. Ya está incluido en `.gitignore`.

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

**`metadata.json`** — Información del manga. Si no existe, aparece con valores por defecto. Ejemplo:

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

---

## Cómo usar el servidor

### Inicio

<img src="docs/screenshots/inicio.jpg" width="320" alt="Pantalla de inicio">

La pantalla principal muestra dos secciones:

**Seguir Leyendo** — Mangas que empezaste pero no terminaste, con barra de progreso y el último capítulo leído. Toca una tarjeta para ir directamente al detalle del manga.

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

Busca mangas por nombre en tiempo real mientras escribés. Muestra portada, nombre, tipo, estado y géneros de cada resultado.

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

### Panel de usuario

<img src="docs/screenshots/usuario.jpg" width="320" alt="Panel de usuario">

Accesible tocando el avatar en la esquina superior derecha. Desde aquí podés:

**Configuración:**
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

**Sitios soportados:** Olympus Scanlation, Nexus Scanlation, Temple Scan, Dragon Translation, ManhwasWEB e Ikigai Mangas.

**Características principales:**
- Corre en loop (escanea, espera, repite) o una sola vez con `--una-vez`
- Sistema multi-fuente: el mismo manga puede trackearse desde 2 sitios a la vez, con prioridad configurable — si el sitio principal falla un capítulo, el de respaldo lo cubre
- Descarga en staging atómico: nunca deja capítulos a medio bajar en la carpeta real
- Reintentos automáticos de capítulos fallidos
- Detección de huecos comparando contra el catálogo real del sitio
- Lee `MANGA_PATH_SCRAPER` del `.env` del servidor (fallback a `MANGA_PATH`)

**Inicio rápido:**
```bash
cd scraper
python scraper.py           # loop continuo
python scraper.py --una-vez # un solo escaneo
```

Los mangas a seguir se configuran en `scraper/seguimiento.json` (copiar desde `scraper/seguimiento.example.json`). Para la documentación completa del scraper, los campos de configuración, el sistema multi-fuente y el detalle de cada sitio, ver [`scraper/README.md`](scraper/README.md).

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

---

### Los mangas no aparecen

- Verificá que las carpetas de mangas sean directorios (no archivos ZIP sin descomprimir)
- Verificá que dentro de cada manga haya al menos una subcarpeta de capítulo con imágenes
- Los formatos de imagen válidos son: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`
- Reiniciá el servidor después de agregar mangas nuevos

---

### Las imágenes no cargan

- Cerrá sesión y volvé a entrar para renovar el token
- Evitá caracteres especiales como `#`, `?` o `%` en los nombres de carpetas

---

### El progreso no se guarda

El progreso se guarda en `server/progress.json`. Verificá que la carpeta `server/` tenga permisos de escritura.

---

## Estructura del proyecto

```
Servidor-de-Mangas/
├── client/                    ← Frontend
│   ├── index.html             ← Aplicación principal
│   ├── reader.html            ← Lector de capítulos
│   ├── login.html             ← Pantalla de login
│   ├── stats.html             ← Estadísticas de lectura
│   ├── css/app.css            ← Estilos (mobile-first, 4 temas)
│   ├── js/
│   │   ├── api.js             ← Cliente HTTP con caché
│   │   ├── app.js             ← Estado global, renders, navegación
│   │   ├── ui.js              ← Componentes visuales y cards
│   │   └── detail.js          ← Vista de detalle de manga
│   └── admin/
│       ├── users.html         ← Gestión de usuarios y permisos
│       └── metadata.html      ← Editor de metadata
├── scraper/                   ← Scraper automático (Python)
│   ├── scraper.py             ← Script principal (~4200 líneas)
│   ├── seguimiento.example.json ← Plantilla de mangas a seguir
│   └── README.md              ← Documentación completa del scraper
├── server/                    ← Backend (Node.js + Express)
│   ├── index.js               ← Entrada del servidor
│   ├── middleware/
│   │   ├── auth.js            ← Verificación JWT
│   │   └── restrictions.js    ← Aplicación de restricciones por usuario
│   ├── routes/
│   │   ├── auth.js            ← Login, usuarios, avatares
│   │   └── manga.js           ← Biblioteca, capítulos, progreso
│   └── data/
│       ├── usersStore.js      ← Lectura/escritura de usuarios
│       └── users.json         ← Usuarios registrados (no subir a Git)
├── docs/screenshots/          ← Capturas de pantalla
├── .env                       ← Configuración local (NO subir a Git)
├── .env.example               ← Plantilla de configuración
├── metadata.example.json      ← Ejemplo de metadata.json
├── iniciar_servidor.bat       ← Lanzador Windows
└── package.json
```

---

## Notas de seguridad

- Diseñado para **red local privada**, no para exposición directa a internet
- El archivo `.env` está en `.gitignore` — nunca lo subás al repositorio
- Los tokens JWT expiran en 30 días
- Protección contra fuerza bruta en el login: bloquea una IP por 5 minutos tras 10 intentos fallidos
- Las imágenes requieren sesión activa para ser accesibles
- Las restricciones de contenido se aplican del lado del servidor en todos los endpoints

---

*Desarrollado por M4RT05*
