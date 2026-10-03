#!/usr/bin/env node
// ═════════════════════════════════════════════════════════════════════════
// instalarMotorIA.js — Instala el motor de mejora IA desde la terminal.
//
//   node scripts/instalarMotorIA.js      (o:  npm run instalar-ia)
//
// Es lo mismo que el botón "Instalar motor ahora" del panel de Mejora con IA
// (comparten el módulo server/lib/motorInstaller.js) — este script sirve para
// instalarlo sin abrir el panel, o para un servidor sin interfaz.
// Si un archivo ya está bajado lo saltea, así que se puede volver a correr
// las veces que haga falta (por ejemplo, si se cortó la conexión).
// ═════════════════════════════════════════════════════════════════════════

const { instalarMotor } = require('../server/lib/motorInstaller');

instalarMotor({ onLog: (linea) => console.log(linea) }).catch((e) => {
  console.error('💥 La instalación se cortó:', e.message);
  console.error('   Volvé a correr el comando: lo que ya se bajó no se repite.');
  process.exitCode = 1;
});
