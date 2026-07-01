@echo off
title MangaServer
cd /d "%~dp0"

:: ── VERIFICAR NODE ────────────────────────────────────────────────────────────
where node >nul 2>&1
if %errorlevel% neq 0 (
  echo [ERROR] Node.js no esta instalado.
  echo Descargalo en: https://nodejs.org/
  pause & exit /b 1
)

:: ── INSTALAR DEPENDENCIAS ─────────────────────────────────────────────────────
if not exist "node_modules\" (
  echo Instalando dependencias...
  npm install
)

:: ── LIMPIAR PROCESOS NODE ANTERIORES ─────────────────────────────────────────
taskkill /f /im node.exe >nul 2>&1
timeout /t 1 /nobreak >nul

:: ── OBTENER PUERTO DEL .ENV ──────────────────────────────────────────────────
set PORT=3000
for /f "tokens=1,* delims==" %%A in (.env) do (
  if "%%A"=="PORT" set PORT=%%B
)

:: ── ABRIR PUERTO EN EL FIREWALL ───────────────────────────────────────────────
netsh advfirewall firewall show rule name="MangaServer Puerto %PORT%" >nul 2>&1
if %errorlevel% neq 0 (
  netsh advfirewall firewall add rule name="MangaServer Puerto %PORT%" ^
    dir=in action=allow protocol=TCP localport=%PORT% ^
    profile=private,domain >nul 2>&1
)

:: ── INICIAR SERVIDOR ──────────────────────────────────────────────────────────
npm start
pause
