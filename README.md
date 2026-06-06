# M4RTO Server — Servidor Personal de Manga

Servidor personal para leer tu biblioteca de manga, manhwa y manhua desde cualquier dispositivo en tu red local. Funciona desde el navegador del celular, tablet o PC sin instalar nada en los dispositivos lectores.

---

## Índice

1. [Requisitos](#requisitos)
2. [Instalación](#instalación)
3. [Configuración](#configuración)
4. [Estructura de carpetas de mangas](#estructura-de-carpetas-de-mangas)
5. [Cómo usar el servidor](#cómo-usar-el-servidor)
   - [Inicio](#inicio)
   - [Panel de usuario](#panel-de-usuario)
   - [Series](#series)
   - [Rankings](#rankings)
   - [Últimos Capítulos](#últimos-capítulos)
   - [Búsqueda](#búsqueda)
6. [Herramienta de Metadata](#herramienta-de-metadata)
7. [Acceso desde otros dispositivos](#acceso-desde-otros-dispositivos)
8. [Solución de problemas](#solución-de-problemas)
9. [Estructura del proyecto](#estructura-del-proyecto)

---

## Requisitos

Antes de instalar, asegúrate de tener lo siguiente en la PC que va a funcionar como servidor:

- **Node.js v18 o superior** — [descargar en nodejs.org](https://nodejs.org/)
- **Windows 10 / 11** (el proyecto está optimizado para Windows)
- Una carpeta con tus mangas organizados (ver [estructura de carpetas](#estructura-de-carpetas-de-mangas))
- El servidor y los dispositivos lectores deben estar en la **misma red WiFi**

---

## Instalación

1. Descarga o clona el repositorio en tu PC
2. Copia el archivo `.env.example` y renómbralo a `.env`
3. Completa los valores del `.env` (ver [Configuración](#configuración))
4. Doble clic en **`iniciar_servidor.bat`** (ejecutar como Administrador la primera vez)
5. Abre el navegador en `http://localhost:3000`

> **Primera vez:** El `.bat` instala las dependencias automáticamente con `npm install`. Puede tardar un minuto.

---

## Configuración

Edita el archivo `.env` en la raíz del proyecto:

```env
# Contraseña para entrar al servidor
PASSWORD=tu_contrasena_aqui

# Clave secreta para los tokens de sesión (texto largo y aleatorio)
JWT_SECRET=cambia_esto_por_algo_muy_largo_y_aleatorio_1234567890

# Puerto del servidor (por defecto 3000)
PORT=3000

# Ruta a tu carpeta de mangas principal
MANGA_PATH=D:\Mis Mangas

# Segunda carpeta de mangas (opcional)
MANGA_PATH_2=D:\Mis Mangas 2
```

> **Importante:** El archivo `.env` contiene tu contraseña. Nunca lo subas a GitHub ni lo compartas. Ya está incluido en `.gitignore`.

---

## Estructura de carpetas de mangas

El servidor espera que cada manga esté en su propia carpeta, y dentro de ella los capítulos como subcarpetas con las imágenes:

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
├── 📁 Otro Manga/
│   └── ...
```

**`cover.jpg`** — Portada del manga. Si no existe, el servidor usa la primera imagen del primer capítulo.

**`metadata.json`** — Información adicional del manga. Si no existe, el manga aparece con valores por defecto. Ejemplo:

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

**Añadidos Recientemente** — Los 10 mangas más nuevos de tu biblioteca ordenados por fecha de creación de la carpeta.

---

### Panel de usuario

<img src="docs/screenshots/usuario.jpg" width="320" alt="Panel de usuario">

Accesible tocando el avatar en la esquina superior derecha. Desde aquí puedes:

- **Contenido +18** — Activa o desactiva la visibilidad de mangas marcados como adultos. Cuando está desactivado, esos mangas desaparecen de todas las secciones y sus géneros no aparecen en los filtros.
- **Tema** — Cambia entre 4 temas visuales: Origins (amarillo oscuro), Dark (gris oscuro), Lunar Tide (azul claro) y White (blanco).
- **Estadísticas de lectura** — Ver resumen de tu actividad lectora.
- **Exportar progreso** — Descarga un archivo `.json` con todo tu historial de lectura como backup.
- **Importar progreso** — Restaura el progreso desde un archivo `.json` exportado anteriormente.
- **Gestionar usuarios** *(solo admin)* — Crear, editar y eliminar usuarios.
- **Editor de metadata** *(solo admin)* — Editar la información de los mangas directamente desde el navegador.

---

### Series

<img src="docs/screenshots/series.jpg" width="320" alt="Pantalla de series">

Vista completa de toda tu biblioteca. Muestra cada manga como una card con portada grande, nombre, tipo (Manga/Manhwa/Manhua), estado y número de capítulos.

**Ordenar** — Usa el selector para ordenar por:
- A → Z / Z → A (alfabético)
- Más nuevos (por fecha de creación)
- Más capítulos

**Filtrar** — Toca el botón "Filtrar" para abrir el panel de filtros.

---

### Filtrar por categoría

<img src="docs/screenshots/filtros.jpg" width="320" alt="Panel de filtros">

El panel de filtros permite combinar múltiples criterios al mismo tiempo:

- **Tipo** — Filtra por Manga, Manhwa o Manhua
- **Géneros** — Muestra solo los géneros presentes en tu biblioteca (si el contenido +18 está desactivado, los géneros adultos no aparecen aquí)
- **Estado** — Filtra por Activo, Hiatus o Finalizado

Puedes combinar varios filtros a la vez. El botón "Limpiar" los borra todos. El número en el botón "Filtrar" indica cuántos filtros están activos.

---

### Rankings

<img src="docs/screenshots/rankings.jpg" width="320" alt="Pantalla de rankings">

Muestra los mangas ordenados por el campo `ranking` del `metadata.json`. Los que no tienen ranking asignado aparecen al final ordenados por cantidad de capítulos.

Cada card muestra la portada grande, el nombre, el puesto y los badges de tipo y estado. En PC se muestran en una grilla de 5 columnas; en móvil en 2 columnas.

Para asignar o cambiar rankings, usa el [Editor de metadata](#herramienta-de-metadata).

---

### Últimos Capítulos

<img src="docs/screenshots/capitulos.jpg" width="320" alt="Pantalla de últimos capítulos">

Lista los mangas ordenados por la fecha del capítulo más reciente. Muestra los 2 últimos capítulos de cada manga con:

- Punto de color — **amarillo** = no leído, **gris** = ya leído
- Fecha relativa — "Hace 2 horas", "Hace 3 días", etc.
- Estado del manga (Activo, Hiatus, Finalizado)

Toca el nombre del manga para ir a su detalle. Toca un capítulo para leerlo directamente.

La lista está paginada de 20 en 20. Usa las flechas de paginación en la parte inferior para navegar.

---

### Búsqueda

<img src="docs/screenshots/busqueda.jpg" width="320" alt="Pantalla de búsqueda">

Busca mangas por nombre en tiempo real mientras escribes. Los resultados muestran portada, nombre, tipo, estado y los primeros géneros de cada manga.

La búsqueda normaliza acentos y mayúsculas — buscar "accion" encuentra mangas con el género "Acción".

---

## Herramienta de Metadata

La carpeta `metadata-manager/` contiene una herramienta separada para gestionar tu biblioteca desde la PC. Permite editar metadata, subir portadas, ver capítulos y detectar problemas.

```
Servidor-de-Mangas/
└── metadata-manager/
    └── iniciar.bat   ← doble clic para abrir
```

**Cómo usarla:**

1. Doble clic en `metadata-manager/iniciar.bat`
2. Se abre automáticamente en `http://localhost:3001`
3. Lee el `.env` del proyecto principal automáticamente — no requiere configuración extra

**Funciones:**

- Ver todos los mangas con indicadores de portada, metadata y problemas
- Filtrar por: sin metadata, sin portada, con problemas
- Editar tipo, estado, ranking, géneros (con sugerencias), sinopsis y flag +18
- Subir portada — arrastra una imagen JPG/PNG/WEBP y se guarda como `cover.jpg`
- Ver capítulos con miniatura de la primera página y cantidad de imágenes
- Renombrar carpetas de capítulos
- Detectar capítulos vacíos, pocas páginas y gaps numéricos
- Exportar toda la metadata a un JSON

> El servidor principal y el metadata manager pueden correr al mismo tiempo en puertos distintos (3000 y 3001).

---

## Acceso desde otros dispositivos

Para acceder desde el celular u otro PC en la misma red:

1. El servidor muestra la IP local al arrancar — busca la línea que dice `📱 Red local:`
2. Usa esa URL en el celular: `http://192.168.x.x:3000`
3. El celular y la PC deben estar en la **misma red WiFi**

Si no aparece la IP correcta, encuéntrala manualmente abriendo `cmd` y ejecutando:

```
ipconfig
```

Busca la sección "Adaptador de Wi-Fi" y anota la **Dirección IPv4**.

---

## Solución de problemas

### El servidor no arranca

**Error: `node` no encontrado**
```
Node.js no está instalado o no está en el PATH
```
Instala Node.js desde [nodejs.org](https://nodejs.org/) y reinicia la PC.

---

**Error: `Puerto en uso`**
```
Error: listen EADDRINUSE :::3000
```
Hay otro proceso usando el puerto 3000. Ejecútalo como administrador o cambia el `PORT` en el `.env`.

```powershell
# Matar el proceso que usa el puerto 3000
netstat -ano | findstr :3000
# Anotar el PID del proceso y ejecutar:
taskkill /f /pid <PID>
```

---

**Error: `No se encontraron carpetas de mangas`**

El `MANGA_PATH` del `.env` no existe o está mal escrito. Verifica:
- Que la ruta exista en tu disco
- Que no tenga comillas en el `.env` (`MANGA_PATH=D:\Mis Mangas` no `MANGA_PATH="D:\Mis Mangas"`)
- Que la carpeta no esté vacía

---

### No conecta desde el celular

**Paso 1 — Verificar que el servidor esté corriendo**
Abre `http://localhost:3000` en la PC. Si no carga, el servidor no está iniciado.

**Paso 2 — Verificar que estén en la misma red**
El celular y la PC deben estar conectados al mismo router/WiFi.

**Paso 3 — Abrir el puerto en el Firewall de Windows**

Abre PowerShell como **Administrador** y ejecuta:

```powershell
netsh advfirewall firewall add rule name="MangaServer Puerto 3000" dir=in action=allow protocol=TCP localport=3000 profile=private,domain
```

**Paso 4 — Cambiar la red de Pública a Privada**

Si tu WiFi está marcada como "Pública", Windows bloquea conexiones entrantes.

`Win + I` → Red e Internet → WiFi → clic en tu red → **Perfil de red: Privado**

O por PowerShell:
```powershell
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private
```

**Paso 5 — Verificar que el puerto responde**

```powershell
Test-NetConnection -ComputerName <TU-IP> -Port 3000
```

Si `TcpTestSucceeded` es `True`, el problema no es el firewall sino el router. Algunos routers tienen **Client Isolation** (aislamiento de clientes) que impide la comunicación entre dispositivos. Desactívalo en la configuración del router.

---

### Los mangas no aparecen

- Verifica que las carpetas de mangas sean directorios (no archivos ZIP)
- Verifica que dentro de cada manga haya al menos una subcarpeta de capítulo
- Verifica que las imágenes sean `.jpg`, `.jpeg`, `.png`, `.webp` o `.gif`
- Reinicia el servidor después de agregar mangas nuevos

---

### Las imágenes no cargan

- Verifica que el token de sesión no haya expirado (cierra sesión y vuelve a entrar)
- Verifica que los nombres de archivo no tengan caracteres especiales problemáticos
- Los nombres de carpeta con `#`, `?` o `%` pueden causar problemas en las rutas

---

### El progreso no se guarda

El progreso se guarda en `server/progress.json`. Si el servidor no tiene permisos de escritura en esa carpeta, el progreso no se persistirá. Verifica los permisos de la carpeta `server/`.

---

## Estructura del proyecto

```
Servidor-de-Mangas/
├── client/                    ← Frontend (HTML, CSS, JS)
│   ├── index.html             ← Aplicación principal
│   ├── reader.html            ← Lector de capítulos
│   ├── login.html             ← Pantalla de login
│   ├── stats.html             ← Estadísticas de lectura
│   ├── css/
│   │   └── app.css            ← Estilos (mobile-first, 4 temas)
│   ├── js/
│   │   ├── api.js             ← Cliente HTTP con caché
│   │   ├── app.js             ← Estado global, renders, navegación
│   │   ├── ui.js              ← Componentes visuales y cards
│   │   └── detail.js          ← Vista de detalle de manga
│   └── admin/
│       ├── users.html         ← Gestión de usuarios (admin)
│       └── metadata.html      ← Editor de metadata (admin)
├── server/                    ← Backend (Node.js + Express)
│   ├── index.js               ← Entrada del servidor
│   ├── middleware/
│   │   └── auth.js            ← Verificación JWT
│   ├── routes/
│   │   ├── auth.js            ← Login, usuarios, avatares
│   │   └── manga.js           ← Biblioteca, capítulos, progreso
│   ├── data/
│   │   └── users.json         ← Usuarios registrados
│   └── progress.json          ← Progreso de lectura por usuario
├── metadata-manager/          ← Herramienta de gestión (puerto 3001)
│   ├── server.js
│   ├── public/index.html
│   └── iniciar.bat
├── docs/
│   └── screenshots/           ← Capturas de pantalla
├── .env                       ← Configuración local (NO subir a Git)
├── .env.example               ← Plantilla de configuración
├── metadata.example.json      ← Ejemplo de metadata.json
├── iniciar_servidor.bat       ← Lanzador Windows
└── package.json
```

---

## Notas de seguridad

- El servidor está diseñado para uso en **red local privada**, no para exposición a internet
- El archivo `.env` está en `.gitignore` — nunca lo subas al repositorio
- Los tokens JWT expiran en 30 días
- El login tiene protección contra fuerza bruta: bloquea una IP por 5 minutos tras 10 intentos fallidos
- Las imágenes se sirven autenticadas — no son accesibles sin sesión iniciada

---

*Desarrollado por M4RT05*
