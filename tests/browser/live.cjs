/* Dynamic source updates in a disposable source copy; no real collections. */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const openPanel = require('./workspace.cjs');
const original = path.resolve(__dirname, '../..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bilibili-live-'));
for (const name of ['ascendant', 'static', 'templates']) fs.cpSync(path.join(original, name), path.join(root, name), {
  recursive: true, filter: source => !source.includes('__pycache__') && !source.endsWith('.pyc') && !source.endsWith('.DS_Store')
});
fs.mkdirSync(path.join(root, 'scripts'));
fs.mkdirSync(path.join(root, 'objects'));
fs.writeFileSync(path.join(root, 'objects/creators.json'), '{"creators": []}');
fs.copyFileSync(path.join(original, 'scripts/preview_dashboard.py'), path.join(root, 'scripts/preview_dashboard.py'));
const server = spawn(path.join(original, '.venv/bin/python'), ['-B', '-u', 'scripts/preview_dashboard.py', '--port', '0'], {
  cwd: root, env: { ...process.env, BILIBILI_RUNTIME_DIR: path.join(root, '.runtime') }
});
const finished = once(server, 'exit');
let log = '';
server.stdout.on('data', data => log += data);
server.stderr.on('data', data => log += data);

(async () => {
  let browser;
  try {
    const deadline = Date.now() + 15000;
    while (!log.match(/http:\/\/127\.0\.0\.1:\d+/)) {
      if (Date.now() > deadline || server.exitCode !== null) throw Error(log);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const base = log.match(/http:\/\/127\.0\.0\.1:\d+/)[0];
    browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'chrome' });
    const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let navigations = 0;
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations++; });
    await page.addInitScript(() => { window.reloadSnapshot = sessionStorage.getItem('bilibili-dev-view'); });
    const settled = () => page.waitForFunction(() => !document.documentElement.dataset.actionBusy && document.documentElement.dataset.dashboardReady);
    const version = async () => (await page.request.get(base + '/api/dev-version')).json();
    await page.goto(base);
    await settled();
    const first = await version();
    await page.locator('#position-end').fill('100');
    fs.appendFileSync(path.join(root, 'static/css/dashboard.css'), '\n.workspace-heading h2 { color: rgb(123, 45, 67); }\n');
    await page.waitForFunction(() => getComputedStyle(document.querySelector('#section-title')).color === 'rgb(123, 45, 67)');
    assert.equal(navigations, 1, 'CSS updates in place');
    assert.equal(await page.locator('#position-end').inputValue(), '100');
    assert.equal((await version()).token, first.token);
    await page.locator('#fetch-dataset').click();
    await settled();
    await openPanel(page, 'sampling');
    await page.locator('#sample-mode-weekly').click();
    await page.locator('#fetch-weekly').click();
    await settled();
    await page.locator('#weekly-result [data-use-collection]').click();
    await settled();
    const collectionId = await page.locator('#analysis-collection').inputValue();
    await page.locator('#analysis-min-views').fill('1000');
    await page.locator('#analysis-field').selectOption('likes', { force: true });
    await page.evaluate(() => {
      const privateField = document.createElement('input');
      privateField.type = 'password'; privateField.id = 'private-test'; privateField.value = 'do-not-store-secret';
      document.body.append(privateField);
      document.querySelector('#node-pair-code').value = 'do-not-store-code';
      scrollTo(0, 200);
    });
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/api/video-action', async route => {
      await gate;
      await route.continue();
    });
    await page.locator('#run-analysis').click();
    await page.waitForFunction(() => document.documentElement.dataset.actionBusy === 'true');
    const appFile = path.join(root, 'static/js/app.js');
    const appSource = fs.readFileSync(appFile, 'utf8');
    fs.appendFileSync(appFile, '\n// Live app update.\n');
    await page.locator('#code-update-status').filter({ hasText: 'waiting for the current task' }).waitFor();
    assert.equal(navigations, 1, 'Do not interrupt a browser task');
    release();
    await page.waitForFunction(() => !!window.reloadSnapshot);
    await settled();
    assert.equal(navigations, 2);
    assert.equal((await version()).token, first.token, 'JS refresh keeps Python and RAM collections');
    assert.equal(await page.locator('#panel-analysis').isVisible(), true);
    assert.equal(await page.locator('#analysis-collection').inputValue(), collectionId);
    assert.equal(await page.locator('#analysis-min-views').inputValue(), '1000');
    assert.equal(await page.locator('#analysis-field').inputValue(), 'likes');
    const saved = await page.evaluate(() => window.reloadSnapshot);
    assert.ok(!saved.includes('do-not-store-secret') && !saved.includes('do-not-store-code'), 'Never retain passwords or pairing codes');
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - JSON.parse(saved).scroll) <= 1, 'Restore scroll position');
    const retained = await (await page.request.get(base + '/api/collections')).json();
    assert.equal(retained.collections[0].collection_id, collectionId);
    assert.equal(retained.creator_dataset.count, 24);
    await page.unroute('**/api/video-action');
    // Broken app.js cannot prevent the independent watcher from seeing a repair.
    fs.writeFileSync(appFile, appSource + '\nconst broken = ;\n');
    await page.waitForFunction(() => !document.documentElement.dataset.dashboardReady);
    await page.waitForFunction(() => document.querySelector('#status').textContent === 'Loading...');
    fs.writeFileSync(appFile, appSource + '\n// Repaired live app.\n');
    await settled();
    assert.equal(navigations, 4);
    assert.equal(await page.locator('#analysis-collection').inputValue(), collectionId);
    assert.equal(await page.locator('#analysis-min-views').inputValue(), '1000');
    const templateFile = path.join(root, 'templates/dashboard.html');
    fs.writeFileSync(templateFile, fs.readFileSync(templateFile, 'utf8').replace('<h1>Ascendant</h1>', '<h1>Live HTML update verified</h1>'));
    await page.locator('h1').filter({ hasText: 'Live HTML update verified' }).waitFor();
    await settled();
    assert.equal(navigations, 5);
    fs.appendFileSync(path.join(root, 'ascendant/config.py'), '\n# Stable Python update.\n');
    await page.locator('#code-update-status').filter({ hasText: 'Backend code changed' }).waitFor();
    assert.equal((await version()).token, first.token, 'Stable main never restarts Python on edits');
    assert.ok(errors.every(message => /Unexpected token/.test(message)), `Unexpected errors: ${errors.join('; ')}`);
    console.log('Live CSS, safe HTML/JS refresh, view restoration, syntax-error recovery and stable RAM passed.');
  } finally {
    await browser?.close();
    server.kill('SIGINT');
    await finished;
    fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
