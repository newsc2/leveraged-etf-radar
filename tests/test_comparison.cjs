const {test} = require('node:test');
const assert = require('node:assert/strict');
const {calculate, anniversary, annualize} = require('../src/comparison.js');
const fixture = prices => ({start:'1996-01-01',end:prices.at(-1)[0],funds:[{symbol:'TEST',prices}]});
const close = (a,b) => assert.ok(Math.abs(a-b)<1e-8, `${a} != ${b}`);

test('first available close and next trading anniversary, not a previous price',()=>{
  const result=calculate(fixture([['2021-01-04',100],['2021-12-31',120],['2022-01-04',125],['2024-01-04',150]]),'1996-01-01')[0];
  assert.equal(result.start,'2021-01-04');
  assert.equal(result.horizons[0].end,'2022-01-04');
  close(result.horizons[0].total,25);
  close(result.horizons[1].total,50);
  assert.equal(result.horizons[2],null);
  close(result.value,15000);
});
test('annual returns use previous year last close and compound to the total',()=>{
  const result=calculate(fixture([['2020-12-30',100],['2020-12-31',110],['2021-01-04',121],['2021-12-31',132],['2022-12-30',99]]),'2020-12-30')[0];
  close(result.annual[2021].total,20);
  close(result.annual[2022].total,-25);
  const chain=Object.values(result.annual).reduce((v,a)=>v*(1+a.total/100),10000);
  close(chain,result.value);
});
test('leap-day anniversaries clamp to February 28',()=>{
  assert.equal(anniversary('2024-02-29',1),'2025-02-28');
  assert.equal(anniversary('2024-02-29',4),'2028-02-29');
});
test('single observation has no CAGR or completed windows',()=>{
  const result=calculate(fixture([['2026-09-21',100]]),'2026-09-21')[0];
  assert.equal(result.value,10000);assert.equal(result.cagr,null);
  assert.deepEqual(result.horizons,[null,null,null,null]);
});
test('weekend BTC starts immediately while equity starts on Monday',()=>{
  const data={start:'1996-01-01',end:'2024-01-08',funds:[
    {symbol:'BTC',prices:[['2024-01-06',100],['2024-01-07',110],['2024-01-08',120]]},
    {symbol:'ETF',prices:[['2024-01-05',90],['2024-01-08',100]]},
  ]};
  const results=calculate(data,'2024-01-06');
  assert.equal(results[0].start,'2024-01-06');assert.equal(results[1].start,'2024-01-08');
  close(results[0].value,12000);close(results[1].value,10000);
});
test('CAGR uses actual elapsed days and does not scale the cumulative return',()=>{
  close(annualize(2,'2020-01-01','2025-01-01'),100*(2**(365.2425/1827)-1));
});
test('invalid and future starts are rejected',()=>{
  const data=fixture([['2024-01-01',100],['2026-09-21',120]]);
  for(const date of ['2023-02-29','bad','1995-01-01','2026-09-22']) assert.throws(()=>calculate(data,date));
});
