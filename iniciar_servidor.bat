@echo off
:: Ir a la carpeta donde está este archivo (raíz del proyecto)
cd /d "%~dp0"

:: Verificar que node esté instalado
where node >nul 2>&1
if %errorlevel% neq 0 (
  echo [ERROR] Node.js no está instalado o no está en el PATH.
  echo Descárgalo en: https://nodejs.org/
  pause
  exit /b 1
)

:: Instalar dependencias si node_modules no existe
if not exist "node_modules\" (
  echo Instalando dependencias...
  npm install
)

:: Iniciar el servidor
echo.
echo  Iniciando MangaServer...
echo  Cierra esta ventana para detener el servidor.
echo.
npm start

pause
