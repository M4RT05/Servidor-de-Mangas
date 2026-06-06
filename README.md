# MangaServer

Tu biblioteca personal de manga, manhwa y manhua. Servidor local con interfaz web para celular y PC.

<div align="center">

![Login](docs/screenshots/login.jpg)

</div>

---

## Capturas de pantalla

<table>
  <tr>
    <td align="center"><b>Series</b></td>
    <td align="center"><b>Detalle del manga</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/series.jpg" width="300"/></td>
    <td><img src="docs/screenshots/detalle.jpg" width="300"/></td>
  </tr>
  <tr>
    <td align="center"><b>Rankings</b></td>
    <td align="center"><b>Últimos capítulos</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/rankings.jpg" width="300"/></td>
    <td><img src="docs/screenshots/capitulos.jpg" width="300"/></td>
  </tr>
  <tr>
    <td align="center" colspan="2"><b>Panel de usuario</b></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><img src="docs/screenshots/panel.jpg" width="300"/></td>
  </tr>
</table>

---

## Características

- Navegación por **Inicio**, **Series**, **Rankings**, **Capítulos** y **Búsqueda**
- Sistema de **múltiples usuarios** con roles (Admin / Lector)
- **6 temas visuales**: Origins, Void Matrix, Ember Dark, Gilded Forest, Arctic Ink, Lunar Tide
- Lector de capítulos con modo **scroll** y modo **páginas**
- Filtros por tipo (Manga / Manhwa / Manhua), género y estado
- Control de **contenido +18** por usuario
- Progreso de lectura guardado por capítulo
- Compatible con **celular y PC**

---

## Instalación

### Requisitos

- [Node.js](https://nodejs.org/) v18 o superior
- Windows / Linux / macOS

### Pasos

```bash
# 1. Clonar o descomprimir el proyecto
cd MangaServer

# 2. Instalar dependencias
npm install

# 3. Configurar el archivo .env (ver sección más abajo)

# 4. Iniciar el servidor
node server/index.js
```

Abrí el navegador en `http://localhost:3000`

Para acceder desde el celular en la misma red WiFi:
```
http://TU_IP_LOCAL:3000
```
> Podés ver tu IP con `ipconfig` (Windows) o `ip a` (Linux/Mac). Suele ser algo como `192.168.1.X`

---

## Configuración del `.env`

Creá un archivo llamado `.env` en la raíz del proyecto:

```env
# Contraseña del usuario administrador (M4RTO)
# Solo se usa la primera vez que se inicia el servidor
PASSWORD=tu_contrasena_aqui

# Clave secreta para los tokens JWT
# Usá algo largo y aleatorio — no lo compartas con nadie
JWT_SECRET=algo_muy_largo_y_secreto_1234567890abcdef

# Puerto del servidor (por defecto 3000)
PORT=3000

# Ruta a la carpeta donde tenés los mangas
MANGA_PATH=C:\Users\TU_USUARIO\Desktop\Mangas
```

> **Windows:** podés usar barras normales `/` o dobles barras invertidas `\\`  
> **Linux/Mac:** usá barras normales, por ejemplo `/home/usuario/mangas`

---

## Estructura de carpetas de mangas

Organizá tu colección de esta manera dentro de la carpeta definida en `MANGA_PATH`:

```
📁 Mangas/                          ← Esta es tu MANGA_PATH
│
├── 📁 Solo Leveling/
│   ├── 🖼️  cover.jpg               ← Portada (obligatorio para que se vea en la app)
│   ├── 📄 metadata.json            ← Info del manga (opcional pero recomendado)
│   ├── 📁 Capítulo 1/
│   │   ├── 🖼️  001.jpg
│   │   ├── 🖼️  002.jpg
│   │   └── 🖼️  003.jpg
│   ├── 📁 Capítulo 2/
│   │   └── 🖼️  ...
│   └── 📁 Capítulo 100/
│       └── 🖼️  ...
│
├── 📁 One Piece/
│   ├── 🖼️  cover.jpg
│   ├── 📄 metadata.json
│   └── 📁 Capítulo 1/
│       └── 🖼️  ...
│
└── 📁 Otro Manga/
    └── 📁 ...
```

**Reglas:**
- El nombre de la carpeta del manga es el título que aparece en la app
- Los capítulos son subcarpetas dentro de la carpeta del manga
- Las imágenes pueden ser `.jpg`, `.jpeg`, `.png` o `.webp`
- La portada debe llamarse exactamente `cover.jpg`

---

## Archivo `metadata.json`

Creá este archivo dentro de cada manga para mostrar información detallada:

```json
{
  "status": "Activo",
  "type": "Manhwa",
  "genres": ["Acción", "Fantasía", "Aventura"],
  "synopsis": "Descripción del manga aquí. Puede ser tan larga como quieras.",
  "ranking": 1,
  "adult": false
}
```

### Referencia de campos

| Campo | Tipo | Valores aceptados | Descripción |
|---|---|---|---|
| `status` | string | `"Activo"` · `"Hiatus"` · `"Finalizado"` | Estado de publicación |
| `type` | string | `"Manga"` · `"Manhwa"` · `"Manhua"` | Tipo de obra |
| `genres` | array | Cualquier texto | Lista de géneros para filtrar |
| `synopsis` | string | Texto libre | Sinopsis en el detalle del manga |
| `ranking` | número | `1`, `2`, `3`... | Posición en el ranking (omitir si no querés usarlo) |
| `adult` | boolean | `true` · `false` | Marca el contenido como +18 |

### Géneros recomendados

```json
"genres": [
  "Acción", "Aventura", "Comedia", "Drama", "Ecchi",
  "Fantasía", "Harem", "Isekai", "Misterio", "Psicológico",
  "Romance", "Retorno", "Sci-Fi", "Seinen", "Shonen",
  "Slice of Life", "Sobrenatural", "Terror"
]
```

---

## Sistema de usuarios

### Primer inicio

Al arrancar el servidor por primera vez, se crea automáticamente el usuario administrador:

| Usuario | Contraseña |
|---|---|
| `M4RTO` | El valor de `PASSWORD` en tu `.env` |

> Si no definís `PASSWORD`, la contraseña por defecto es `admin`

### Roles

| Rol | Puede hacer |
|---|---|
| **Admin** | Todo: ver contenido, gestionar usuarios, activar +18 |
| **Lector** | Ver el contenido permitido, guardar progreso |

### Cómo crear usuarios

**Desde la app (recomendado):**

1. Iniciá sesión como admin
2. Tocá tu avatar en la esquina superior derecha
3. Andá a **Cuenta → Gestionar usuarios**
4. Completá el formulario: nombre de usuario, contraseña y rol
5. Hacé clic en **Crear usuario**

```
Avatar  →  Gestionar usuarios  →  Completar formulario  →  Crear usuario
```

**Desde el navegador:**

Abrí directamente `/admin/users.html` — solo funciona si estás logueado como admin.

> Las contraseñas se guardan hasheadas con SHA-256 + salt. Nunca se almacenan en texto plano.

---

## Contenido +18

El toggle de **Contenido +18** filtra los mangas marcados como adultos.

### Cómo funciona

```
metadata.json con "adult": true
        ↓
  Toggle APAGADO  →  El manga NO aparece en ninguna sección
  Toggle ENCENDIDO →  El manga aparece normalmente
```

- El estado se guarda por navegador (independiente por dispositivo)
- Afecta: Inicio, Series, Rankings, Capítulos y Búsqueda
- Por defecto está **desactivado**

### Activar el contenido +18

```
Avatar  →  Panel de usuario  →  Configuración  →  activar "Contenido +18"
```

### Marcar un manga como +18

Agregá esto al `metadata.json` del manga:
```json
{
  "adult": true
}
```

---

## Temas disponibles

| Nombre | Estilo |
|---|---|
| **Origins** | Oscuro con acento dorado (por defecto) |
| **Void Matrix** | Negro profundo con acento cyan |
| **Ember Dark** | Oscuro cálido con acento rojo-naranja |
| **Gilded Forest** | Claro beige con acento verde oscuro |
| **Arctic Ink** | Blanco puro con acento negro |
| **Lunar Tide** | Azul grisáceo con acento azul marino |

Para cambiar: **Avatar → Panel de usuario → Tema**

---

## Estructura del proyecto

```
📁 MangaServer/
├── 📄 .env                    ← Tu configuración (no subir a Git)
├── 📄 .env.example            ← Plantilla de configuración
├── 📄 package.json
├── 📄 README.md
│
├── 📁 server/
│   ├── 📄 index.js            ← Punto de entrada del servidor
│   ├── 📁 routes/
│   │   ├── 📄 auth.js         ← Login y gestión de usuarios
│   │   └── 📄 manga.js        ← API de mangas y capítulos
│   ├── 📁 middleware/
│   │   └── 📄 auth.js         ← Validación de tokens JWT
│   └── 📁 data/
│       └── 📄 users.json      ← Base de datos de usuarios (auto-generado)
│
└── 📁 client/
    ├── 📄 index.html          ← App principal
    ├── 📄 login.html          ← Pantalla de login
    ├── 📄 reader.html         ← Lector de capítulos
    ├── 📁 admin/
    │   └── 📄 users.html      ← Panel de gestión de usuarios
    ├── 📁 css/
    │   └── 📄 app.css         ← Estilos y temas
    ├── 📁 js/
    │   ├── 📄 app.js          ← Lógica principal
    │   └── 📄 api.js          ← Cliente de la API
    └── 📁 docs/
        └── 📁 screenshots/    ← Capturas del README
```

---

## Preguntas frecuentes

**¿Por qué no veo mis mangas?**  
Verificá que `MANGA_PATH` en el `.env` apunte a la carpeta correcta y que cada manga tenga su propia subcarpeta con los capítulos adentro.

**¿Por qué no cargan las imágenes en el celular?**  
Accedé con la IP local de tu PC (ej. `192.168.1.100:3000`) y no con `localhost`. El servidor tiene que estar corriendo mientras usás la app.

**¿Cómo arranco el servidor correctamente?**
```bash
# Siempre desde la carpeta raíz del proyecto
cd MangaServer
node server/index.js
```

**¿El progreso de lectura se guarda?**  
Sí. El servidor guarda qué capítulos leíste por usuario. La sección "Seguir Leyendo" muestra los mangas en progreso con barra de porcentaje.

**¿Puedo correrlo en un servidor externo?**  
Sí. El servidor escucha en `0.0.0.0` por lo que funciona en cualquier máquina. Asegurate de que el `JWT_SECRET` sea largo y seguro.

---

<div align="center">

Coded by **M4RT05**

</div>
