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

:: ── LIMPIAR INSTANCIAS ANTERIORES DE ESTE SERVIDOR ───────────────────────────
:: Antes esto era "taskkill /f /im node.exe", que mataba CUALQUIER proceso
:: Node de la maquina (otros proyectos, VS Code, etc.), no solo este servidor.
:: Ahora filtra por linea de comando: solo mata procesos node.exe que esten
:: corriendo "server/index.js" de ESTE proyecto.
for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'server[\\/]index\.js' } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /f /pid %%P >nul 2>&1
)
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
