/** Real Chrome/Gecko review on owned, synthetic, in-memory processes only. */
import { chromium, firefox, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { post1Pages } from './post1-review.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const args = process.argv.slice(2);
const argument = (name, fallback) => { const index = args.indexOf(name); return index < 0 ? fallback : args[index + 1]; };
const baseline = argument('--baseline', null);
const phaseChoice = argument('--phase', 'both');
const browserChoice = argument('--browser', 'both');
const corrections = phaseChoice === 'corrections';
const post1 = phaseChoice === 'post1';
const out = path.join(root, post1 ? 'docs/workbench/alpha7-post1/screenshots' : 'docs/workbench/alpha7/screenshots');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const children = new Set();
const manifest = { viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, locale: 'de-DE', timezone: 'Europe/Berlin', syntheticOnly: true, startedAt: new Date().toISOString(), sourceHead: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(), browsers: [], screenshots: [], checks: [], errors: [] };
if (post1) manifest.viewport = { width: 1568, height: 710 };
if (baseline) {
  manifest.baselineHead = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: path.resolve(baseline), encoding: 'utf8' }).trim();
  manifest.baselineTree = execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { cwd: path.resolve(baseline), encoding: 'utf8' }).trim();
  const releaseTree = execFileSync('git', ['rev-parse', '4687c3556618de3c4b14a309c317bf41893986c4^{tree}'], { cwd: root, encoding: 'utf8' }).trim();
  if (manifest.baselineTree !== releaseTree) throw new Error('Before checkout must have exactly the released Alpha-6 tree');
}

// Each new subpage is compared with its actual Alpha-6 predecessor, never a fabricated before-image.
const pages = [
  ['overview', '/', '/'],
  ['contracts-list', '/contracts/list/all', '/contracts'],
  ['contract-choice', '/contracts/select/contract', '/contracts'],
  ['contract-detail', '/contracts/view/fixture.flow', '/contracts/fixture.flow'],
  ['contract-import', '/contracts/import/json', '/registry'],
  ['relations', '/contracts/relations/graph', '/graph'],
  ['templates-own', '/templates/own', '/definitions'],
  ['templates-built-in', '/templates/built-in', '/definitions'],
  ['template-detail', '/templates/details/test.flow/1', '/definitions/test.flow/1'],
  ['sources-devices', '/sources/devices?item=0', '/sources?tab=sources&item=0'],
  ['sources-bindings', '/sources/bindings?item=0', '/sources?tab=bindings&item=0'],
  ['sources-catalogs', '/sources/catalogs?item=0', '/sources?tab=catalogs&item=0'],
  ['sources-dependencies', '/sources/dependencies?item=0', '/sources?tab=dependencies&item=0'],
  ['changes-drafts', '/changes/drafts', '/registry?tab=drafts'],
  ['changes-versions', '/changes/versions', '/registry?tab=revisions'],
  ['changes-transfer', '/changes/transfer', '/registry'],
  ['settings-system', '/settings/system', '/admin'],
  ['settings-installation', '/settings/installation', '/admin'],
  ['settings-connections', '/settings/connections', '/admin'],
  ['settings-keys', '/settings/keys', '/admin'],
  ['settings-log', '/settings/log', '/admin'],
  ...['state', 'diagnosis', 'history', 'publications', 'evidence', 'commands'].map(tab => [`diagnostics-${tab}`, `/settings/diagnostics?tab=${tab}&contract=fixture.flow`, `/diagnostics?tab=${tab}&contract=fixture.flow`]),
];

async function start(checkout, port) {
  await new Promise((resolve, reject) => { const probe = createServer(); probe.once('error', reject); probe.listen(port, '127.0.0.1', () => probe.close(resolve)); });
  const python = path.join(checkout, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const child = spawn(python, ['-u', 'dev/workbench_demo.py', ...(post1 ? ['--post1'] : ['--seed', '--feed', '--interval', '2']), '--port', String(port)], { cwd: checkout, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  children.add(child);
  let startError;
  child.on('error', error => { startError = error; });
  let log = ''; child.stdout.on('data', data => { log += data; }); child.stderr.on('data', data => { log += data; });
  for (let attempt = 0; attempt < 150; attempt++) {
    if (startError) throw startError;
    if (child.exitCode !== null) throw new Error(`Owned review process exited: ${log.slice(-2000)}`);
    try { if ((await fetch(`http://127.0.0.1:${port}/health/live`)).ok) return { child, url: `http://127.0.0.1:${port}/`, log: () => log }; } catch { /* only wait for our process */ }
    await delay(100);
  }
  throw new Error(`Owned review process failed to become ready: ${log.slice(-2000)}`);
}
async function stop(child) {
  if (!child.pid || child.exitCode !== null) { children.delete(child); return; }
  await new Promise(resolve => { child.once('exit', resolve); child.kill(); });
  children.delete(child);
}
async function visit(page, base, route) {
  await page.goto(`${base}#${route}`);
  await page.locator('main h1').waitFor({ timeout: 70_000 });
  await expect(page.locator('main')).not.toContainText(/^(?:Lädt|Entwurf wird geöffnet|Server prüft)/, { timeout: 70_000 });
  await page.locator('main').evaluate(element => { element.scrollTop = 0; });
  await delay(450);
}
async function capture(page, phase, browser, name, comparison) {
  const file = `${phase}/${browser}/${name}.png`;
  const target = path.join(out, file);
  await mkdir(path.dirname(target), { recursive: true });
  const image = await page.screenshot({ path: target, fullPage: false, animations: 'disabled' });
  const dimensions = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, documentWidth: document.documentElement.scrollWidth }));
  if (dimensions.width !== manifest.viewport.width || dimensions.height !== manifest.viewport.height || dimensions.documentWidth > manifest.viewport.width) throw new Error(`Invalid viewport or document overflow on ${name}: ${JSON.stringify(dimensions)}`);
  if (phase !== 'before') {
    for (const menu of await page.getByLabel('Weitere Seitenaktionen', { exact: true }).all()) {
      const overlaps = await menu.evaluate(element => {
        const rect = element.getBoundingClientRect();
        return Array.from(document.querySelector('main').querySelectorAll('button,a,select')).filter(candidate => {
          if (candidate === element || element.contains(candidate)) return false;
          const other = candidate.getBoundingClientRect();
          return other.width > 0 && other.height > 0 && Math.min(rect.right, other.right) - Math.max(rect.left, other.left) > 1 && Math.min(rect.bottom, other.bottom) - Math.max(rect.top, other.top) > 1;
        }).map(candidate => candidate.textContent.trim());
      });
      if (overlaps.length) throw new Error(`Page menu overlaps actions on ${name}: ${overlaps.join(', ')}`);
    }
  }
  manifest.screenshots.push({ file, phase, browser, task: name, route: new URL(page.url()).hash, comparison, sha256: createHash('sha256').update(image).digest('hex'), ...dimensions });
  console.log(`Captured ${file}`);
}
async function apply(page, expectedVersion, note) {
  await page.getByRole('button', { name: 'Übernehmen', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Konfiguration übernehmen', exact: true });
  if (note) await dialog.getByLabel('Notiz zur neuen Version (freiwillig)').fill(note);
  await dialog.getByRole('checkbox', { name: /Auswirkungen geprüft/ }).click({ timeout: 70_000 });
  const button = dialog.getByRole('button', { name: 'Jetzt übernehmen', exact: true });
  await expect(button).toBeInViewport();
  await button.click();
  await expect(dialog.getByText(`Version ${expectedVersion} ist aktiv`, { exact: true })).toBeVisible({ timeout: 70_000 });
  await dialog.getByRole('button', { name: 'Schließen', exact: true }).last().click();
}
async function guidedContract(page, server, browser) {
  await visit(page, server.url, '/contracts/list/all');
  let clicks = 0;
  const click = async locator => { await locator.click(); clicks++; };
  await click(page.getByRole('button', { name: 'Neuer Vertrag', exact: true }));
  const dialog = page.getByRole('dialog', { name: 'Neuer Vertrag', exact: true });
  const radio = dialog.getByRole('radio', { name: 'Bad Temperatur', exact: true });
  await radio.waitFor({ timeout: 70_000 });
  await click(radio);
  await capture(page, 'after', browser, 'device-guide', 'P6: echte Geräteauswahl vor dem Anlegen');
  await click(dialog.getByRole('button', { name: 'In Entwurf speichern', exact: true }));
  await click(page.getByRole('button', { name: 'Übernehmen', exact: true }));
  const confirm = page.getByRole('dialog', { name: 'Konfiguration übernehmen', exact: true });
  await click(confirm.getByRole('checkbox', { name: /Auswirkungen geprüft/ }));
  await capture(page, 'after', browser, 'takeover-confirmation', 'P5: Bestätigung und Hauptknopf im sichtbaren Fenster');
  const button = confirm.getByRole('button', { name: 'Jetzt übernehmen', exact: true });
  await expect(button).toBeInViewport(); await click(button);
  await expect(confirm.getByText('Version 2 ist aktiv', { exact: true })).toBeVisible({ timeout: 70_000 });
  if (clicks !== 6) throw new Error(`Temperature guide took ${clicks} clicks`);
  manifest.checks.push({ browser, check: 'P6 first temperature contract without IDs or manual transforms', clicks, result: 'passed' });
  await confirm.getByRole('button', { name: 'Schließen', exact: true }).last().click();
  await page.getByRole('button', { name: 'Live ansehen', exact: true }).click();
}
async function taskPages(page, server, phase, browser) {
  for (const [name, afterRoute, beforeRoute] of pages) {
    const route = phase === 'after' ? afterRoute : beforeRoute;
    await visit(page, server.url, route);
    if (phase === 'before' && name === 'changes-versions') await page.getByRole('tab', { name: 'Versionen', exact: true }).click();
    if (phase === 'before' && name === 'contract-import') {
      await page.getByRole('button', { name: 'Neuer Entwurf', exact: true }).click();
      await page.getByRole('dialog').getByRole('radio', { name: 'Einlesen (JSON)', exact: true }).check();
    }
    if (name === 'diagnostics-evidence') {
      if (phase === 'after') { await page.getByLabel('Quelle auswählen').selectOption('source.a'); await page.getByRole('table', { name: 'Gespeicherter Nachweis' }).waitFor({ timeout: 70_000 }); }
      else { await page.getByLabel('Quelle-ID', { exact: true }).fill('source.a'); await page.getByRole('button', { name: 'Laden', exact: true }).click(); }
    }
    await capture(page, phase, browser, name, phase === 'after' ? `Alpha 6: ${beforeRoute}` : `Alpha 7: ${afterRoute}`);
    if (phase === 'before' && name === 'contract-import') await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen', exact: true }).click();
    if (name === 'contract-detail') {
      for (const [key, label] of [['config', 'Konfiguration'], ['dependencies', 'Abhängigkeiten']]) {
        await page.getByRole('tab', { name: label, exact: true }).click(); await delay(150);
        await capture(page, phase, browser, `contract-${key}`, 'Vertrags-Unterbereich vor/nach Alpha 7');
      }
    }
    if (phase === 'after' && name === 'template-detail') await templateTabs(page, phase, browser);
    if (phase === 'after' && name === 'overview') {
      await page.getByRole('button', { name: /^(Nicht bereit|Bereit)/ }).click();
      await capture(page, phase, browser, 'readiness-conditions', 'P2: Bereitschaft und Lösungslinks im Kopf');
      await page.keyboard.press('Escape');
    }
  }
  await visit(page, server.url, phase === 'after' ? '/changes/drafts' : '/registry?tab=drafts');
  await page.getByRole('button', { name: 'Bearbeiten', exact: true }).last().click();
  await expect(page).toHaveURL(/drafts\//);
  await delay(500); await capture(page, phase, browser, 'draft-details', 'Entwurfsdetail vor/nach Alpha 7');
  if (phase === 'after') {
    await visit(page, server.url, '/contracts/relations/graph');
    await capture(page, phase, browser, 'relations-draft', 'P11: geplante Zusammenhänge ohne zugemischte Live-Zustände');
  } else {
    await visit(page, server.url, '/definitions/test.flow/1');
    await templateTabs(page, phase, browser);
  }
}
async function templateTabs(page, phase, browser) {
  for (const [name, label] of [['inputs', /^Eingänge/], ['outputs', /^Ausgaben/], ['rules', /^Benannte Regeln/], ['machine', /^Ablauf/], ['json', /^JSON/]]) {
    await page.getByRole('tab', { name: label }).click(); await delay(150);
    if (phase === 'after' && name === 'inputs') {
      const disclosure = page.getByRole('tabpanel').locator('[role="button"][aria-expanded]').first();
      await disclosure.click(); await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
      await disclosure.press('Enter'); await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
      await expect(page.getByRole('tabpanel').getByLabel('Name', { exact: true })).toBeDisabled();
    }
    if (phase === 'after' && name === 'outputs') {
      const collapsed = page.getByRole('tabpanel').locator('[data-op]').first().locator('[role="button"][aria-expanded]').first();
      await collapsed.click(); await expect(collapsed).toHaveAttribute('aria-expanded', 'false');
      await collapsed.press('Space'); await expect(collapsed).toHaveAttribute('aria-expanded', 'true');
      await expect(page.getByLabel('Operator', { exact: true }).first()).toBeDisabled();
      manifest.checks.push({ browser, check: 'Live template disclosures respond to mouse and keyboard while inputs remain disabled', result: 'passed' });
    }
    await capture(page, phase, browser, `template-${name}`, 'Vorlagen-Unterbereich; Alpha 6 im geöffneten Entwurf, Alpha 7 lesend im Live-Modus');
  }
}
async function extraAfter(page, server, browser) {
  await visit(page, server.url, '/templates/details/test.flow/1');
  await page.getByRole('button', { name: 'Neue Version', exact: true }).click();
  const version = page.getByRole('dialog');
  await capture(page, 'after', browser, 'template-new-version', 'Neue Vorlagenversion mit gezielter Vertragsauswahl');
  await version.getByRole('button', { name: 'Version anlegen', exact: true }).click();
  await page.getByRole('tab', { name: /Ablauf/ }).click();
  await page.getByRole('checkbox', { name: 'Administratoren dürfen diesen Ablauf manuell neu starten', exact: true }).check();
  await capture(page, 'after', browser, 'template-restart-permission', 'P9: ausdrückliche Freigabe in neuer Ablaufversion');
  await page.getByLabel('Weitere Entwurfsaktionen').click();
  await page.getByRole('button', { name: 'Speichern', exact: true }).click();
  await apply(page, 3, 'Synthetische Browserprüfung: begrenzten Neustart freigegeben');
  await page.getByRole('button', { name: 'Live ansehen', exact: true }).click();
  await visit(page, server.url, '/contracts/view/fixture.flow');
  await page.getByRole('button', { name: 'Ablauf neu starten', exact: true }).click({ timeout: 70_000 });
  const restart = page.getByRole('dialog', { name: 'Ablauf neu starten', exact: true });
  await restart.getByLabel('Warum startest du den Ablauf neu?').fill('Isolierte Browserprüfung mit synthetischen Daten');
  await restart.getByRole('checkbox', { name: /Ich habe die Auswirkungen geprüft/ }).check();
  await capture(page, 'after', browser, 'manual-restart-impact', 'P9: Auswirkungen, Grund und ausdrückliche Bestätigung');
  await restart.getByRole('button', { name: 'Jetzt manuell neu starten', exact: true }).click();
  await expect(restart.getByText(/Der Ablauf wurde manuell neu gestartet/)).toBeVisible({ timeout: 70_000 });
  await capture(page, 'after', browser, 'manual-restart-result', 'P9: gespeichertes Ergebnis der manuellen Anfrage');
  await restart.getByRole('button', { name: 'Schließen', exact: true }).last().click();
  await visit(page, server.url, '/changes/versions');
  await capture(page, 'after', browser, 'versions-with-note', 'P10: Zeit, tatsächliche Rolle und freiwillige Versionsnotiz');
  const firstVersion = page.locator('li').filter({ has: page.getByRole('checkbox', { name: 'Version 1 zum Vergleich wählen', exact: true }) });
  await firstVersion.getByRole('button', { name: 'Auf diese Version zurücksetzen', exact: true }).click();
  const rollback = page.getByRole('dialog');
  await rollback.getByRole('checkbox', { name: /Ich möchte Version/ }).check();
  await expect(rollback.getByRole('button', { name: 'Zurücksetzen', exact: true })).toBeInViewport();
  await capture(page, 'after', browser, 'rollback-confirmation', 'P10: geprüfter älterer Stand und sichtbare Dialogbestätigung');
  await rollback.getByRole('button', { name: 'Abbrechen', exact: true }).click();
  await visit(page, server.url, '/sources/devices?item=0');
  await page.getByRole('button', { name: 'Quelle mit anderem Typ anlegen', exact: true }).click();
  await capture(page, 'after', browser, 'source-move', 'P8: neue Quelle, ausdrückliche Vertragsauswahl, Historie bleibt');
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen', exact: true }).click();
  manifest.checks.push({ browser, check: 'P9 real version opt-in and confirmed administrative restart; P10 note shown', result: 'passed' });
}

/** K.06: re-photograph the correction scope without replacing historical proof. */
async function correctionPages(page, server, browser) {
  const photo = (name, reason) => capture(page, 'corrections', browser, name, reason);
  await visit(page, server.url, '/changes/drafts');
  await page.getByRole('button', { name: 'Bearbeiten', exact: true }).last().click();
  await expect(page).toHaveURL(/drafts\//);
  const draftRoute = new URL(page.url()).hash.slice(1);
  await page.getByRole('button', { name: 'Prüfen', exact: true }).click();
  await expect(page.getByLabel('Anzahl der Änderungen')).toHaveText(['0 Änderungen', '0 Änderungen']);
  await expect(page.locator('header').first()).toContainText('Entwurf ohne Namen');
  await photo('draft-details', 'K.01/K.02/K.04: identischer Stand, beide Zähler 0, ein Prüfkasten und Übernahme nur in der Leiste');
  await visit(page, server.url, '/');
  await expect(page.getByRole('button', { name: 'Änderungen übernehmen', exact: true })).toBeVisible();
  const actions = ['Neuer Vertrag', 'Vertrag importieren', 'Änderungen übernehmen'];
  const heights = await Promise.all(actions.map(name => page.getByRole('button', { name, exact: true }).evaluate(element => element.getBoundingClientRect().height)));
  if (new Set(heights).size !== 1) throw new Error(`Unequal quick-action heights: ${heights}`);
  await photo('overview', 'K.05: Statuszahlen und gleich große Schnellaktionen bei offenem Entwurf');
  await visit(page, server.url, '/contracts/list/all');
  await photo('contracts-list', 'K.03/K.05: Meine Verträge, Zustandsfilter und deutsche Zeitwerte');
  await visit(page, server.url, '/contracts/view/fixture.flow');
  await expect(page.getByRole('link', { name: 'Meine Verträge', exact: true })).toHaveAttribute('aria-current', 'page');
  await photo('contract-detail', 'K.03/K.05: Detail markiert Meine Verträge; Zeitwerte und Herkunft');
  const nav = page.getByRole('navigation', { name: 'Hauptnavigation' });
  await nav.getByRole('button', { name: 'Quellen', exact: true }).click();
  await expect(nav.getByRole('link', { name: 'Vertrag', exact: true })).toHaveCount(0);
  await photo('sidebar', 'K.03/K.04: kein doppelter Vertrag-Menüpunkt; Wertelisten im Quellenmodul');
  await visit(page, server.url, '/changes/versions');
  await photo('versions', 'K.04/K.05: Versionssprache und deutsche Datumsangaben');
  await visit(page, server.url, '/contracts/list/all');
  await page.getByRole('button', { name: 'Neuer Vertrag', exact: true }).click();
  const wizard = page.getByRole('dialog', { name: 'Neuer Vertrag', exact: true });
  await wizard.getByRole('radio', { name: 'Bad Temperatur', exact: true }).click();
  await expect(wizard.getByText('= 15 Minuten', { exact: true })).toBeVisible();
  await photo('device-guide', 'K.05: Frist 900 Sekunden mit Hilfe = 15 Minuten');
  await wizard.getByRole('button', { name: 'In Entwurf speichern', exact: true }).click();
  await page.getByRole('button', { name: 'Übernehmen', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: 'Konfiguration übernehmen', exact: true });
  const consent = confirmation.getByRole('checkbox', { name: /Auswirkungen geprüft/ });
  await consent.check(); await expect(consent).toBeChecked();
  await expect(confirmation.getByRole('button', { name: 'Jetzt übernehmen', exact: true })).toBeInViewport();
  await expect(page.getByLabel('Anzahl der Änderungen')).not.toContainText(['…', '…']);
  const comparison = await confirmation.getByLabel('Anzahl der Änderungen').textContent();
  await expect(page.locator('header').getByLabel('Anzahl der Änderungen')).toHaveText(comparison);
  await photo('takeover-confirmation', 'K.01/K.04: gleiche Serverzahl in Leiste/Übernahme; verständlicher Bestätigungsdialog');
  await confirmation.getByRole('button', { name: 'Abbrechen', exact: true }).click();
  await visit(page, server.url, draftRoute);
  await page.getByRole('button', { name: 'Prüfen', exact: true }).click();
  await expect(page.getByLabel('Anzahl der Änderungen')).toHaveText([comparison, comparison]);
  await photo('draft-changed', 'K.01: geführter neuer Vertrag, identische Objektzahl in Leiste und Was sich ändert');
  manifest.checks.push({ browser, check: 'K.01 zero comparison and guided contract use identical server object counts', result: 'passed', guidedCount: comparison });
  manifest.checks.push({ browser, check: 'K.02-K.05: sidebar selection, no chooser, minute help, equally sized actions, confirmation visible', result: 'passed', actionHeights: heights });
}

await mkdir(out, { recursive: true });
const heartbeat = setInterval(() => console.log(`Browser review alive: ${manifest.screenshots.length} screenshots captured`), 20_000);
try {
  for (const name of ['chrome', 'firefox'].filter(name => browserChoice === 'both' || name === browserChoice)) {
    const browser = name === 'chrome' ? await chromium.launch({ channel: 'chrome', headless: true }) : await firefox.launch({ headless: true });
    manifest.browsers.push({ name, version: browser.version(), engine: name === 'chrome' ? 'Google Chrome (installed channel)' : 'Mozilla Firefox (Playwright build)', headless: true });
    try {
      for (const phase of ['before', 'after'].filter(phase => corrections || post1 ? phase === 'after' : phaseChoice === 'both' || phase === phaseChoice)) {
        if (phase === 'before' && !baseline) throw new Error('--baseline checkout required for real before-images');
        const server = await start(phase === 'before' ? path.resolve(baseline) : root, phase === 'before' ? 8196 : 8198);
        const context = await browser.newContext({ viewport: manifest.viewport, deviceScaleFactor: 1, locale: manifest.locale, timezoneId: manifest.timezone, colorScheme: 'dark', reducedMotion: 'reduce' });
        const page = await context.newPage(); page.setDefaultTimeout(70_000);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(`${message.text()} (${message.location().url})`); });
        page.on('response', response => { if (response.status() >= 400 && !(post1 && ([400, 422].includes(response.status()) && new URL(response.url()).pathname === '/api/v1/registry/validate' || response.status() === 409 && /\/api\/v1\/registry\/drafts\//.test(response.url()))) && !(phase === 'before' && new URL(response.url()).pathname === '/favicon.ico')) errors.push(`HTTP ${response.status()} ${response.url()}`); });
        try {
          if (post1) await post1Pages(page, server, name, { visit, capture, manifest });
          else if (corrections) await correctionPages(page, server, name);
          else {
            if (phase === 'after') await guidedContract(page, server, name);
            await taskPages(page, server, phase, name);
            if (phase === 'after') await extraAfter(page, server, name);
          }
          const unexpected = phase === 'before' ? errors.filter(error => !error.includes('/favicon.ico')) : errors;
          if (unexpected.length) throw new Error(`Browser console errors: ${unexpected.join('; ')}`);
          if (errors.length) manifest.checks.push({ browser: name, phase, check: 'Historical Alpha-6 favicon 404', result: 'recorded pre-existing baseline defect' });
          manifest.checks.push({ browser: name, phase, check: `${corrections ? 'All correction pages' : 'All task pages'} render without JavaScript errors or document overflow`, result: 'passed' });
        } catch (error) {
          manifest.errors.push({ browser: name, phase, message: String(error), route: new URL(page.url()).hash });
          await page.screenshot({ path: path.join(out, `failure-${name}-${phase}.png`), animations: 'disabled' });
          throw error;
        } finally { await context.close(); await stop(server.child); }
      }
    } finally { await browser.close(); }
  }
} finally {
  clearInterval(heartbeat);
  for (const child of children) await stop(child);
  manifest.finishedAt = new Date().toISOString();
  await writeFile(path.join(out, `manifest-${browserChoice}-${phaseChoice}.json`), JSON.stringify(manifest, null, 2) + '\n');
}
console.log(JSON.stringify({ screenshots: manifest.screenshots.length, browsers: manifest.browsers, checks: manifest.checks, errors: manifest.errors }, null, 2));
