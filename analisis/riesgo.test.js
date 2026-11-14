// Pruebas sin conexión de la compuerta de riesgo y las protecciones (casos armados).
// Uso: node analisis/riesgo.test.js
const assert = require('assert');
const R = require('./riesgo.js');

// Martes 17-11-2026, 09:50 NY (invierno: UTC-5) → 14:50 UTC
const V = new Date('2026-11-17T14:50:00Z');
const base = { capital: 1000, operaciones: [] };       // 1 R = $10
const ok = { ticker: 'ABCD', entrada: 4.00, stop: 3.80, acciones: 50 }; // 50 × 0,20 = $10 = 1 R
let n = 0;
function caso(nombre, fn) { fn(); n++; console.log('ok  ' + nombre); }
const reglas = (r) => r.motivos.map((m) => m.regla);

caso('una orden que pasa', () => {
  const r = R.evaluar(ok, base, V);
  assert.strictEqual(r.veredicto, 'PASA', JSON.stringify(r.motivos));
  assert.strictEqual(r.maxAcciones, 50);
});

caso('se bloquea por pérdida (tamaño mayor a 1 R)', () => {
  const r = R.evaluar({ ...ok, acciones: 80 }, base, V);
  assert.strictEqual(r.veredicto, 'BLOQUEA');
  assert.deepStrictEqual(reglas(r), ['tamano']);
  assert.match(r.motivos[0].arreglo, /Baja a 50 acciones/);
});

caso('el riesgo se mide desde el precio actual si es mayor y reciente', () => {
  const precio = { valor: 4.10, ts: new Date(V.getTime() - 5 * 60000).toISOString() }; // 0,30 por acción
  const r = R.evaluar(ok, { ...base, precio }, V);
  assert.strictEqual(r.veredicto, 'BLOQUEA');
  assert.strictEqual(r.maxAcciones, 33);
  const viejo = { valor: 4.10, ts: new Date(V.getTime() - 20 * 60000).toISOString() };   // > 15 min: no cuenta
  assert.strictEqual(R.evaluar(ok, { ...base, precio: viejo }, V).veredicto, 'PASA');
});

caso('stop obligatorio y del lado correcto', () => {
  assert.deepStrictEqual(reglas(R.evaluar({ ...ok, stop: 0 }, base, V)), ['stop']);
  assert.deepStrictEqual(reglas(R.evaluar({ ...ok, stop: 4.10 }, base, V)), ['stop']);
});

caso('se bloquea por pérdida diaria (−3 R comprometidos)', () => {
  const ops = [1, 2, 3].map((i) => ({ ticker: 'X' + i, dia: '2026-11-17', estado: 'closed', R: 1, netR: i === 1 ? 1.8 : -1, motivo: 'tiempo', salidaTs: '2026-11-17T14:45:00Z' }));
  const r = R.evaluar(ok, { ...base, operaciones: ops }, V);
  assert.strictEqual(r.veredicto, 'BLOQUEA');
  assert.ok(reglas(r).includes('perdida_diaria'), 'cuenta también la que ganó');
});

caso('fuera de la ventana (antes y después), feriado y fin de semana', () => {
  assert.deepStrictEqual(reglas(R.evaluar(ok, base, new Date('2026-11-17T14:30:00Z'))), ['ventana']); // 09:30 NY
  assert.deepStrictEqual(reglas(R.evaluar(ok, base, new Date('2026-11-17T15:16:00Z'))), ['ventana']); // 10:16 NY
  assert.deepStrictEqual(reglas(R.evaluar(ok, base, new Date('2026-11-26T14:50:00Z'))), ['ventana']); // Acción de Gracias
  assert.deepStrictEqual(reglas(R.evaluar(ok, base, new Date('2026-11-21T14:50:00Z'))), ['ventana']); // sábado
  // Verano (UTC-4): 09:50 NY del 7-7-2026 = 13:50 UTC
  assert.strictEqual(R.evaluar(ok, base, new Date('2026-07-07T13:50:00Z')).veredicto, 'PASA');
  assert.strictEqual(R.sesion(new Date('2026-07-07T13:50:00Z'), R.CONFIG).hora, '09:50');
});

caso('una salida pasa aunque el día esté en −3 R y fuera de la ventana', () => {
  const ops = [1, 2, 3].map((i) => ({ ticker: 'X' + i, dia: '2026-11-17', estado: i === 3 ? 'open' : 'closed', R: 1, netR: -1, motivo: 'stop', salidaTs: '2026-11-17T14:40:00Z' }));
  const r = R.evaluar({ lado: 'venta', ticker: 'X3', entrada: 4, stop: 3.8, acciones: 50 }, { ...base, operaciones: ops }, new Date('2026-11-17T20:00:00Z'));
  assert.strictEqual(r.veredicto, 'PASA');
});

caso('máximo 2 posiciones abiertas', () => {
  const ops = [1, 2].map((i) => ({ ticker: 'Y' + i, dia: '2026-11-17', estado: 'open', R: 0.5 }));
  assert.deepStrictEqual(reglas(R.evaluar(ok, { ...base, operaciones: ops }, V)), ['abiertas']);
});

caso('enfriamiento activo tras perder en la misma acción', () => {
  const ops = [{ ticker: 'ABCD', dia: '2026-11-17', estado: 'closed', R: 1, netR: -1, motivo: 'stop', salidaTs: '2026-11-17T14:40:00Z' }]; // 09:40 NY
  const r = R.evaluar(ok, { ...base, operaciones: ops }, V);
  assert.deepStrictEqual(reglas(r), ['enfriamiento']);
  assert.match(r.motivos[0].arreglo, /10:10 NY/);
  assert.strictEqual(R.evaluar({ ...ok, ticker: 'OTRA' }, { ...base, operaciones: ops }, V).veredicto, 'PASA');
  // 30 min después ya no aplica
  assert.strictEqual(R.evaluar(ok, { ...base, operaciones: ops }, new Date('2026-11-17T15:11:00Z')).veredicto, 'PASA');
});

caso('pausa tras 2 stops en el día', () => {
  const ops = ['A', 'B'].map((t) => ({ ticker: t, dia: '2026-11-17', estado: 'closed', R: 1, netR: -1, motivo: 'stop', salidaTs: '2026-11-17T14:40:00Z' }));
  const r = R.evaluar(ok, { ...base, operaciones: ops }, V);
  assert.ok(reglas(r).includes('pausa_stops'));
});

caso('freno semanal: con −5 R en la semana el tamaño baja a la mitad', () => {
  const ops = ['2026-11-16', '2026-11-16', '2026-11-16', '2026-11-16', '2026-11-16'].map((d, i) => ({ ticker: 'Z' + i, dia: d, estado: 'closed', R: 1, netR: -1, motivo: 'tiempo', salidaTs: '2026-11-16T15:00:00Z' }));
  const r = R.evaluar(ok, { ...base, operaciones: ops }, V);
  assert.ok(r.protecciones.some((p) => p.id === 'freno_semanal'));
  assert.strictEqual(r.maxAcciones, 25);
  assert.deepStrictEqual(reglas(r), ['tamano']);
  assert.strictEqual(R.evaluar({ ...ok, acciones: 25 }, { ...base, operaciones: ops }, V).veredicto, 'PASA');
  // La semana siguiente ya no aplica
  assert.ok(!R.protecciones(ops, new Date('2026-11-23T14:50:00Z'), 'ABCD').some((p) => p.id === 'freno_semanal'));
});

caso('el veredicto queda ligado a la orden exacta', () => {
  const v = R.evaluar(ok, base, V);
  assert.ok(R.vigente(v, ok));
  assert.ok(!R.vigente(v, { ...ok, acciones: 51 }));
  assert.ok(!R.vigente(v, { ...ok, stop: 3.79 }));
});

caso('cada bloqueo trae motivo y arreglo', () => {
  const r = R.evaluar({ ...ok, acciones: 80, stop: 0 }, base, new Date('2026-11-17T16:00:00Z'));
  r.motivos.forEach((m) => { assert.ok(m.texto && m.arreglo, JSON.stringify(m)); });
});

console.log(`\n${n} casos, todos pasan.`);
