# 📚 MangaServer

Servidor personal para leer manga, manhwa y manhua desde cualquier dispositivo en tu red local.

## ⚙️ Requisitos

- [Node.js](https://nodejs.org/) v18 o superior
- Tus mangas en formato `.jpg` organizados en carpetas

## 📁 Estructura de carpetas de mangas

```
main/
  Nombre del Manga/
    1/
      01.jpg
      02.jpg
      03.jpg
    2/
      01.jpg
      ...
```

## 🚀 Instalación

### 1. Clonar el repositorio
```bash
git clone https://github.com/tu-usuario/manga-server.git
cd manga-server
```

### 2. Instalar dependencias
```bash
npm install
```

### 3. Configurar variables de entorno
```bash
# Copiar el archivo de ejemplo
cp .env.example .env

# Editar .env con tu contraseña y configuración
```

Contenido del `.env`:
```
PASSWORD=tu_contraseña_aqui
JWT_SECRET=una_cadena_larga_y_aleatoria
PORT=3000
MANGA_PATH=./main
```

### 4. Poner tus mangas
Coloca tu carpeta `main/` con todos los mangas en la raíz del proyecto.

### 5. Iniciar el servidor
```bash
# Producción
npm start

# Desarrollo (reinicia automáticamente al guardar cambios)
npm run dev
```

## 📱 Acceso desde el celular

1. El PC y el celular deben estar en la **misma red WiFi**
2. Busca la IP local de tu PC:
   - **Windows:** `ipconfig` → busca "Dirección IPv4"
   - **Linux:** `ip a` → busca la IP de tu interfaz de red
3. Abre en el celular: `http://192.168.1.X:3000`

## 🔒 Seguridad

- El acceso está protegido por contraseña
- El token de sesión dura 30 días
- La carpeta `main/` y el archivo `.env` están excluidos del repositorio

## 📝 Notas

- Las imágenes se sirven directamente desde disco, sin copias
- El progreso de lectura se guarda en `server/progress.json`
- Modos de lectura: **Scroll** (manhwa) y **Páginas** (manga)
