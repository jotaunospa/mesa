// Corre el código real de la Mesa (evaluate, bestOf, analyzeChart) sobre cada fila de cada foto.
// Requisitos: un servidor local que sirva mesa-test.html (la Mesa con window.__mesa expuesto,
// ver README.md) y filas.json (salida de analisis/filas.py). Escribe veredictos.json.
// Uso: BASE=http://localhost:8766 OUT=veredictos.json node analisis/veredictos.js
const { chromium } = require('playwright'); const fs=require('fs');
(async()=>{const b=await chromium.launch();const p=await b.newPage();const e=[];p.on('pageerror',x=>e.push(x.message));
await p.goto((process.env.BASE||'http://localhost:8766')+'/mesa-test.html');await p.waitForFunction(()=>window.__mesa);
const res=await p.evaluate(async()=>{const M=window.__mesa;const filas=await (await fetch('filas.json')).json();
  return filas.map(f=>{const [h,mi]=f.hora.split(':').map(Number);const min=h*60+mi;const cx={min:min,off:180,ph:M.phaseAt(min,'Fri'),opened:0,lossRow:0,key:f.dia};
    const bo=M.bestOf(f.x,cx);const pick=v=>({stage:v.stage,hard:v.hard.map(c=>M.SHORT[c.id]),fails:v.checks.filter(c=>c.st==='fail').map(c=>M.SHORT[c.id])});
    let setup=null;if(f.x.chart){const cs=M.sanitizeCandles(f.x.chart.candles);if(cs.length>=8){const an=M.analyzeChart(cs,2,parseFloat(f.x.dayHigh)||0);if(an.setup)setup={name:an.setup.name,ok:an.setup.ok,live:an.setup.live,broken:an.setup.broken,age:an.setup.age,fallas:an.setup.checks.filter(c=>!c.ok).map(c=>c.l),checks:an.setup.checks.map(c=>c.l),entry:an.setup.entry,stop:an.setup.stop,tgt:an.setup.tgt,pct:+an.setup.pct.toFixed(2)};}}
    return {dia:f.dia,hora:f.hora,tk:f.x.ticker,apto:f.apto,cons:pick(bo.a),mom:pick(bo.b),setup:setup,casi:M.casiOf(bo,f.x).map(c=>c.pid+':'+M.SHORT[c.c.id])};});});
fs.writeFileSync(process.env.OUT||'veredictos.json',JSON.stringify(res));console.log('errs',e,res.length);await b.close();})();
