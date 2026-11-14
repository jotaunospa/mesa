/* Compuerta de riesgo y protecciones de la Mesa de Papel (solo papel; determinista, sin IA).
 *
 * Una sola fuente de verdad: la Mesa incluye este archivo tal cual y la cuenta sombra
 * (analisis/sombra.js) lo usa en node. Sin dependencias ni acceso al DOM.
 *
 * Origen:
 * - Compuerta y tamaño adaptados de jakeschincariol/day-trader-skill (MIT), trader-risk/check.py y
 *   trader-budget/size.py @ 3d0f4ef698. Copyright (c) Jake Schincariol. Licencia MIT.
 * - Protecciones reimplementadas desde la DESCRIPCIÓN de freqtrade docs/includes/protections.md
 *   @ 5197bedab8 (CooldownPeriod, StoplossGuard, MaxDrawdown). No se copió código (freqtrade es GPL-3.0).
 *
 * Reglas (encargo del 13-11):
 * - Salir nunca se bloquea: los límites solo frenan entradas.
 * - Stop obligatorio y bajo la entrada (solo compras).
 * - Riesgo por acción desde el MAYOR entre la entrada del plan y el precio actual (si tiene < 15 min).
 * - Tamaño máximo = 1 R / riesgo por acción, con 1 R = riesgoPct del capital de papel.
 * - Pérdida diaria: suma la pérdida máxima completa de cada entrada del día (ganara o perdiera), más
 *   la de esta orden. Tope 3 R.
 * - Máximo 2 posiciones abiertas.
 * - Solo en la ventana 09:35–10:15 NY (hora de NY con horario de verano; feriados de NYSE).
 * - El veredicto queda ligado a la orden exacta (firma). Si la orden cambia, se vuelve a evaluar.
 * - Protecciones: enfriamiento por acción tras una pérdida (30 min), pausa tras 2 stops en el día,
 *   freno semanal (−5 R acumulados en la semana: tamaño a la mitad mientras siga así).
 * - Pattern day trader: opción apagada por defecto (Racional no confirma la regla vigente).
 */
(function (raiz) {
  'use strict';

  var CONFIG = {
    riesgoPct: 1,              // 1 R = 1% del capital de papel
    perdidaDiariaR: 3,         // tope de pérdida máxima comprometida en el día
    maxAbiertas: 2,
    ventana: ['09:35', '10:15'], // hora de Nueva York
    precioVigenteMin: 15,      // el precio actual cuenta si tiene menos de esto
    enfriamientoMin: 30,       // tras una pérdida en una acción
    maxStopsDia: 2,            // con 2 stops en el día, no hay más entradas
    frenoSemanalR: -5,         // con −5 R en la semana, tamaño a la mitad
    pdt: false                 // verificador de pattern day trader: apagado
  };

  // Feriados de NYSE (calendario publicado). Agregar los años siguientes cuando corresponda.
  var FERIADOS = {
    '2026-01-01': 1, '2026-01-19': 1, '2026-02-16': 1, '2026-04-03': 1, '2026-05-25': 1, '2026-06-19': 1,
    '2026-07-03': 1, '2026-09-07': 1, '2026-11-26': 1, '2026-12-25': 1,
    '2027-01-01': 1, '2027-01-18': 1, '2027-02-15': 1, '2027-03-26': 1, '2027-05-31': 1, '2027-06-18': 1,
    '2027-07-05': 1, '2027-09-06': 1, '2027-11-25': 1, '2027-12-24': 1
  };
  // Cierres anticipados (13:00 NY). No tocan la ventana de la mañana; quedan para la hora de salida.
  var CIERRE_ANTICIPADO = { '2026-11-27': '13:00', '2026-12-24': '13:00', '2027-11-26': '13:00' };

  function p2(n) { return (n < 10 ? '0' : '') + n; }
  function aMin(hhmm) { var a = String(hhmm).split(':'); return (+a[0]) * 60 + (+a[1]); }
  function hhmm(m) { m = ((m % 1440) + 1440) % 1440; return p2(Math.floor(m / 60)) + ':' + p2(m % 60); }

  // Fecha y hora de Nueva York para un instante (Intl maneja el horario de verano).
  function enNY(fecha) {
    var f = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', weekday: 'short' });
    var o = {};
    f.formatToParts(fecha).forEach(function (p) { o[p.type] = p.value; });
    var dia = o.year + '-' + o.month + '-' + o.day;
    return { dia: dia, min: (+o.hour) * 60 + (+o.minute), dow: o.weekday };
  }

  function sesion(fecha, cfg) {
    var ny = enNY(fecha), ini = aMin(cfg.ventana[0]), fin = aMin(cfg.ventana[1]);
    var habil = ny.dow !== 'Sat' && ny.dow !== 'Sun' && !FERIADOS[ny.dia];
    return { dia: ny.dia, min: ny.min, hora: hhmm(ny.min), habil: habil, enVentana: habil && ny.min >= ini && ny.min <= fin,
      ini: ini, fin: fin, feriado: !!FERIADOS[ny.dia], cierre: CIERRE_ANTICIPADO[ny.dia] || '16:00' };
  }

  // Lunes de la semana (fecha 'AAAA-MM-DD' de NY), para el freno semanal.
  function lunesDe(dia) {
    var d = new Date(dia + 'T12:00:00Z'), w = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - w);
    return d.toISOString().slice(0, 10);
  }

  function num(x) { var v = Number(x); return isFinite(v) ? v : NaN; }
  function redondear(x, n) { var k = Math.pow(10, n || 2); return Math.round(x * k) / k; }

  // Firma de la orden: si cambia cualquier campo, el veredicto anterior ya no sirve.
  function firma(o) {
    return [o.lado || 'compra', String(o.ticker || '').toUpperCase(), num(o.entrada), num(o.stop), num(o.acciones)].join('|');
  }

  /* Protecciones activas a la hora `ahora`, para una acción (o null para las generales).
   * operaciones: [{ticker, dia (NY), estado 'open'|'closed', R (pérdida máx. comprometida en R),
   *                netR, motivo ('stop'|...), salidaTs (ISO)}] */
  function protecciones(operaciones, ahora, ticker, cfg) {
    cfg = Object.assign({}, CONFIG, cfg || {});
    var s = sesion(ahora, cfg), out = [], tNow = ahora.getTime();
    var hoy = operaciones.filter(function (t) { return t.dia === s.dia; });
    // Pausa por stops
    var stops = hoy.filter(function (t) { return t.estado === 'closed' && t.motivo === 'stop'; }).length;
    if (stops >= cfg.maxStopsDia) {
      out.push({ id: 'pausa_stops', bloquea: true, motivo: stops + ' stops hoy: no hay más entradas por el día.',
        levanta: 'Mañana, al abrir la ventana (' + cfg.ventana[0] + ' NY).' });
    }
    // Enfriamiento por acción
    if (ticker) {
      var tk = String(ticker).toUpperCase(), ult = null;
      operaciones.forEach(function (t) {
        if (String(t.ticker).toUpperCase() === tk && t.estado === 'closed' && num(t.netR) < 0 && t.salidaTs) {
          var ts = new Date(t.salidaTs).getTime();
          if (!ult || ts > ult) ult = ts;
        }
      });
      if (ult && tNow - ult < cfg.enfriamientoMin * 60000) {
        var hasta = new Date(ult + cfg.enfriamientoMin * 60000);
        out.push({ id: 'enfriamiento', bloquea: true, motivo: 'Perdiste en ' + tk + ' hace ' + Math.floor((tNow - ult) / 60000) +
          ' min: no se vuelve a entrar en la misma acción todavía.', levanta: 'A las ' + hhmm(enNY(hasta).min) + ' NY.' });
      }
    }
    // Freno semanal
    var lun = lunesDe(s.dia);
    var semR = operaciones.filter(function (t) { return t.estado === 'closed' && t.dia >= lun && t.dia <= s.dia && isFinite(num(t.netR)); })
      .reduce(function (a, t) { return a + num(t.netR); }, 0);
    if (semR <= cfg.frenoSemanalR) {
      out.push({ id: 'freno_semanal', bloquea: false, factor: 0.5, motivo: 'Vas ' + redondear(semR, 2) + ' R esta semana: el tamaño baja a la mitad.',
        levanta: 'Cuando la semana vuelva sobre ' + cfg.frenoSemanalR + ' R, o el lunes.' });
    }
    return out;
  }

  /* Evalúa una orden de papel.
   * orden: {lado: 'compra'|'venta', ticker, entrada, stop, acciones}
   * estado: {capital, operaciones: [...como en protecciones], precio: {valor, ts (ISO)}}
   * Devuelve {veredicto: 'PASA'|'BLOQUEA', firma, motivos: [{regla, texto, arreglo}], maxAcciones, riesgoR, protecciones} */
  function evaluar(orden, estado, ahora, cfg) {
    cfg = Object.assign({}, CONFIG, cfg || {});
    ahora = ahora || new Date();
    var f = firma(orden), m = [];
    var res = { firma: f, motivos: m, ahora: ahora.toISOString() };

    if ((orden.lado || 'compra') === 'venta') {   // salir nunca se bloquea
      res.veredicto = 'PASA'; res.nota = 'Salir siempre está permitido.';
      return res;
    }

    var s = sesion(ahora, cfg), cap = num(estado.capital), e = num(orden.entrada), st = num(orden.stop), q = num(orden.acciones);
    var ops = estado.operaciones || [];
    var prots = protecciones(ops, ahora, orden.ticker, cfg);
    res.protecciones = prots;
    var factor = prots.reduce(function (a, p) { return p.factor ? a * p.factor : a; }, 1);
    var R1 = cap * cfg.riesgoPct / 100 * factor;
    res.R1 = redondear(R1, 2);

    function bloquea(regla, texto, arreglo) { m.push({ regla: regla, texto: texto, arreglo: arreglo }); }

    // Ventana
    if (!s.habil) bloquea('ventana', s.feriado ? 'Hoy es feriado en la bolsa de EE.UU.' : 'Hoy no abre la bolsa.', 'Espera el próximo día hábil.');
    else if (!s.enVentana) bloquea('ventana', 'Son las ' + s.hora + ' NY, fuera de la ventana ' + cfg.ventana[0] + '–' + cfg.ventana[1] + '.',
      s.min < s.ini ? 'Espera a las ' + cfg.ventana[0] + ' NY.' : 'No la tomes: la ventana de hoy ya cerró.');

    // Stop
    if (!(st > 0)) bloquea('stop', 'La orden no tiene stop.', 'Define un stop bajo la entrada antes de entrar.');
    else if (!(e > 0) || st >= e) bloquea('stop', 'El stop (' + st + ') no está bajo la entrada (' + e + ').', 'Pon el stop bajo la entrada.');
    if (!(q >= 1)) bloquea('tamano', 'La orden no tiene cantidad de acciones.', 'Indica cuántas acciones.');

    // Riesgo desde el mayor entre la entrada y el precio actual vigente
    var base = e, px = estado.precio || {};
    if (num(px.valor) > 0 && px.ts && ahora.getTime() - new Date(px.ts).getTime() < cfg.precioVigenteMin * 60000 && num(px.valor) > base) base = num(px.valor);
    res.precioBase = base;
    if (st > 0 && base > st && q >= 1) {
      var rps = redondear(base - st, 6), maxQ = Math.floor(redondear(R1 / rps, 6)), riesgoR = redondear(q * rps / R1, 6); // sin error de coma flotante (4,00 − 3,80)
      res.maxAcciones = maxQ; res.riesgoR = redondear(riesgoR, 2);
      if (q > maxQ) bloquea('tamano', q + ' acciones arriesgan ' + redondear(riesgoR, 2) + ' R (1 R = $' + redondear(R1, 2) + ').',
        maxQ >= 1 ? 'Baja a ' + maxQ + ' acciones, o acerca el stop.' : 'Ni 1 acción cabe en 1 R: acerca el stop o no la tomes.');
      // Pérdida diaria
      var usado = ops.filter(function (t) { return t.dia === s.dia; }).reduce(function (a, t) { return a + (isFinite(num(t.R)) ? num(t.R) : 1); }, 0);
      res.usadoHoyR = redondear(usado, 2);
      if (usado + Math.min(riesgoR, 1) > cfg.perdidaDiariaR + 1e-9) bloquea('perdida_diaria', 'Hoy ya comprometiste ' + redondear(usado, 2) +
        ' R de ' + cfg.perdidaDiariaR + ' R; esta entrada sumaría ' + redondear(Math.min(riesgoR, 1), 2) + ' R más.', 'No la tomes: el día ya usó su presupuesto de riesgo.');
    }

    // Posiciones abiertas
    var abiertas = ops.filter(function (t) { return t.estado === 'open'; }).length;
    if (abiertas >= cfg.maxAbiertas) bloquea('abiertas', 'Ya tienes ' + abiertas + ' posiciones abiertas (máximo ' + cfg.maxAbiertas + ').', 'Cierra una antes de abrir otra.');

    // Protecciones que bloquean
    prots.forEach(function (p) { if (p.bloquea) bloquea(p.id, p.motivo, 'Se levanta: ' + p.levanta); });

    if (cfg.pdt) { /* reservado: verificador de pattern day trader, apagado hasta que Racional confirme la regla */ }

    res.veredicto = m.length ? 'BLOQUEA' : 'PASA';
    return res;
  }

  // ¿El veredicto sirve para esta orden? (si la orden cambió, hay que evaluar de nuevo)
  function vigente(veredicto, orden) { return !!veredicto && veredicto.firma === firma(orden); }

  var API = { CONFIG: CONFIG, FERIADOS: FERIADOS, CIERRE_ANTICIPADO: CIERRE_ANTICIPADO, enNY: enNY, sesion: sesion,
    firma: firma, evaluar: evaluar, protecciones: protecciones, vigente: vigente, lunesDe: lunesDe };
  if (typeof module !== 'undefined' && module.exports) module.exports = API; else raiz.Riesgo = API;
})(typeof window !== 'undefined' ? window : this);
