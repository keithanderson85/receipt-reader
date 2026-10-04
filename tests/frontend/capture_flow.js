// Drives the real /capture page in jsdom with a scripted "server".
// Usage: node capture_flow.js <path-to-rendered-capture.html>   (run by tests/test_frontend.py)
const { JSDOM } = require('jsdom');
const fs = require('fs');

const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(name, cond, extra) {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : '  -> ' + (extra || '')));
  if (!cond) failures++;
}

async function scenario(title, statuses, expectations) {
  console.log('\n== ' + title);
  const html = fs.readFileSync(process.argv[2], 'utf-8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/capture' });
  const w = dom.window;
  let polls = 0, submitted = null;

  // fake browser APIs the page relies on
  w.createImageBitmap = async () => ({ width: 800, height: 1200, close() {} });
  w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {} });
  w.HTMLCanvasElement.prototype.toBlob = function (cb) { cb(new w.Blob([new Uint8Array(2000).fill(polls + 7)], { type: 'image/jpeg' })); };
  w.URL.createObjectURL = () => 'blob:fake';
  w.URL.revokeObjectURL = () => {};
  w.scrollTo = () => {};
  w.confirm = () => true;
  w.fetch = async (url, opts) => {
    if (url.includes('/capture/submit')) {
      submitted = opts.body;
      return { ok: true, status: 200, json: async () => ({ success: true, scan_id: 'abc', status_url: '/scans/abc/status' }) };
    }
    if (url.includes('/scans/abc/status')) {
      const s = statuses[Math.min(polls++, statuses.length - 1)];
      return { ok: true, status: 200, json: async () => s };
    }
    throw new Error('unexpected fetch ' + url);
  };
  await sleep(300);                                               // let DOMContentLoaded handlers run
  const d = w.document, $ = id => d.getElementById(id);

  // add one photo through the real file input
  const input = $('galleryInput');
  const file = new w.File([new Uint8Array(3000)], 'r.jpg', { type: 'image/jpeg' });
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  input.dispatchEvent(new w.Event('change'));
  await sleep(400);
  check('photo added, Save enabled', !$('saveBtn').disabled && $('photoList').children.length === 1);

  $('saveBtn').click();
  await sleep(300);
  check('capture screen swapped for result screen', $('captureView').classList.contains('d-none') && !$('resultView').classList.contains('d-none'));
  check('upload carried the photo and the auto-crop flag', submitted && submitted.getAll('photos').length === 1 && submitted.get('autocrop') === '1');

  await expectations(w, $, async () => { await sleep(1700); });
  w.close();
}

(async () => {
  const quickNew = { success: true, status: 'processing', quick_status: 'done', dup_status: 'new', matches: [], merchant: 'Metro Pawn', amount: 682.07, date: '2026-10-01', reasons: [], review_url: null };
  const done = Object.assign({}, quickNew, { status: 'ready', review_url: '/inbox/abc/review' });

  await scenario('new receipt',
    [{ success: true, status: 'processing', quick_status: 'pending', dup_status: 'unknown', matches: [], merchant: null, amount: null, date: null }, quickNew, done],
    async (w, $, tick) => {
      check('shows the spinner while reading', $('verdict').textContent.includes('Reading the total and date'));
      await tick();
      check('quick result shown', $('verdict').textContent.includes('Metro Pawn') && $('verdict').textContent.includes('$682.07') && $('verdict').textContent.includes('2026-10-01'), $('verdict').textContent);
      check('green "new receipt" verdict', $('verdict').classList.contains('new') && $('verdict').textContent.includes('New receipt'));
      check('still reading items', $('fullStatus').textContent.includes('Reading the items'));
      await tick();
      check('full read done message', $('fullStatus').textContent.includes('ready to approve'), $('fullStatus').textContent);
      check('review link appears', !$('reviewNow').classList.contains('d-none') && $('reviewNow').getAttribute('href') === '/inbox/abc/review');
    });

  const dup = Object.assign({}, quickNew, { dup_status: 'duplicate', matches: [{ kind: 'expense', reason: 'same_amount_date', id: 7, merchant: 'Metro Pawn', amount: 682.07, date: '2026-10-01' }] });
  await scenario('duplicate of a saved expense', [dup, Object.assign({}, dup, { status: 'needs_review', review_url: '/inbox/abc/review' })],
    async (w, $, tick) => {
      await tick();
      check('red duplicate verdict naming the match', $('verdict').classList.contains('duplicate') && $('verdict').textContent.includes('Possible duplicate') && $('verdict').textContent.includes('(saved)'), $('verdict').textContent);
    });

  const pending = Object.assign({}, quickNew, { dup_status: 'duplicate', matches: [{ kind: 'scan', reason: 'same_file', id: 'x', merchant: 'Metro Pawn', amount: 682.07, date: '2026-10-01' }] });
  await scenario('duplicate of another waiting receipt', [pending], async (w, $) => {
    await sleep(100);
    check('says it is in the inbox', $('verdict').textContent.includes('in your inbox'), $('verdict').textContent);
  });

  const maybe = Object.assign({}, quickNew, { dup_status: 'maybe', matches: [{ kind: 'expense', reason: 'same_amount_date_other_store', id: 2, merchant: 'Other', amount: 682.07, date: '2026-10-01' }] });
  await scenario('same total and date, different store', [maybe], async (w, $) => {
    await sleep(100);
    check('amber verdict', $('verdict').classList.contains('maybe') && $('verdict').textContent.includes('Same total and date'), $('verdict').textContent);
  });

  const unreadable = { success: true, status: 'processing', quick_status: 'failed', dup_status: 'unknown', matches: [], merchant: null, amount: null, date: null };
  await scenario('quick read could not read it', [unreadable, { success: true, status: 'error', quick_status: 'failed', dup_status: 'unknown', matches: [], error: 'OpenAI timed out', merchant: null, amount: null, date: null }],
    async (w, $, tick) => {
      await sleep(100);
      check('explains it could not read total/date', $('verdict').textContent.includes("Couldn't read the total or date"), $('verdict').textContent);
      await tick();
      check('shows the error and the retry hint', $('fullStatus').textContent.includes('OpenAI timed out') && $('fullStatus').textContent.includes('retry'), $('fullStatus').textContent);
    });

  await scenario('scan another resets the screen', [quickNew], async (w, $) => {
    await sleep(100);
    $('againBtn').click();
    await sleep(100);
    check('back on the capture screen with no photos', !$('captureView').classList.contains('d-none') && $('resultView').classList.contains('d-none') && $('photoList').children.length === 0 && $('saveBtn').disabled);
    check('result panel reset for next time', $('verdict').textContent.includes('Reading the total and date') && $('reviewNow').classList.contains('d-none'));
  });

  console.log(failures ? `\n${failures} FAILED` : '\nALL PASSED');
  process.exit(failures ? 1 : 0);
})();
