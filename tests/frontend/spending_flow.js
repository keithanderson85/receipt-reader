// Drives the real /spending page in jsdom: tooltip on hover/focus, chart <-> table, safe text.
// Usage: node spending_flow.js <path-to-rendered-spending.html>   (run by tests/test_frontend.py)
const fs = require('fs');
const { JSDOM } = require('jsdom');

const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(name, cond, extra) {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : '  -> ' + (extra || '')));
  if (!cond) failures++;
}

(async () => {
  const html = fs.readFileSync(process.argv[2], 'utf-8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/spending' });
  const w = dom.window, d = w.document;
  await sleep(300);

  const tip = d.getElementById('vizTip');
  const bars = Array.from(d.querySelectorAll('.hbar-row[data-tip]'));
  const chart = Array.from(d.querySelectorAll('[data-viz="chart"]'));
  const tables = Array.from(d.querySelectorAll('[data-viz="table"]'));

  check('page has bars, columns and a table twin for each chart', bars.length >= 2 && chart.length === 2 && tables.length === 2);
  check('chart shown, table hidden by default', chart.every(e => !e.hidden) && tables.every(e => e.hidden));
  check('tooltip hidden to start', tip.hidden);

  // hover shows value first, then name, then detail - built with textContent
  bars[0].dispatchEvent(new w.Event('pointerenter'));
  check('hover shows the tooltip', !tip.hidden);
  check('value leads, name follows',
        tip.querySelector('.tip-value').textContent === bars[0].dataset.value &&
        tip.querySelector('.tip-name').textContent === bars[0].dataset.name);
  check('detail line shows receipts / average / share', /receipt/.test(tip.querySelector('.tip-detail').textContent));
  bars[0].dispatchEvent(new w.Event('pointerleave'));
  check('leaving hides it', tip.hidden);

  // keyboard focus shows the same thing as hover
  bars[1].dispatchEvent(new w.Event('focus'));
  check('focus shows the same tooltip', !tip.hidden && tip.querySelector('.tip-name').textContent === bars[1].dataset.name);
  d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape' }));
  check('Escape closes it', tip.hidden);

  // names are untrusted: markup in a store name must stay text
  const evil = bars.find(b => b.dataset.name.includes('<'));
  if (evil) {
    evil.dispatchEvent(new w.Event('pointerenter'));
    check('a store name containing HTML is shown as text, not parsed',
          tip.querySelector('.tip-name').textContent === evil.dataset.name && tip.querySelector('.tip-name img, .tip-name b, .tip-name script') === null);
    evil.dispatchEvent(new w.Event('pointerleave'));
  }

  // columns: tooltip on a column
  const col = d.querySelector('.col-slot[data-tip]');
  col.dispatchEvent(new w.Event('focus'));
  check('column tooltip shows its period and value', !tip.hidden && tip.querySelector('.tip-value').textContent.startsWith('$'));
  col.dispatchEvent(new w.Event('blur'));

  // chart <-> table
  const [chartBtn, tableBtn] = d.querySelectorAll('.viz-view [data-view]');
  tableBtn.click();
  check('Table switches every chart to its table', chart.every(e => e.hidden) && tables.every(e => !e.hidden));
  check('buttons reflect the state', tableBtn.getAttribute('aria-pressed') === 'true' && chartBtn.getAttribute('aria-pressed') === 'false');
  check('the choice is remembered', w.localStorage.getItem('spendView') === 'table');
  chartBtn.click();
  check('Chart switches back', chart.every(e => !e.hidden) && tables.every(e => e.hidden));

  console.log(failures ? `\n${failures} FAILED` : '\nALL PASSED');
  process.exit(failures ? 1 : 0);
})();
