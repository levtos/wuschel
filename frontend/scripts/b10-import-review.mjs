/** Firefox desktop proof against the real serial writer; synthetic data only. */
import { firefox, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.resolve(process.argv[2] || path.join(root, 'b10-browser-proof'));
const child = spawn(path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'), ['-u', 'dev/b10_import_review.py'], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let browser; let log = '';
child.stderr.on('data', data => { log += data; });
try {
  await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', code => reject(new Error(`Synthetic API exited ${code}: ${log}`))); child.stdout.on('data', data => { if (String(data).includes('"ready": true')) resolve(); }); });
  const packageText = await (await fetch('http://127.0.0.1:8140/package')).text();
  browser = await firefox.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:8139/#/import-export');
  await page.getByLabel('Datei auswählen').setInputFiles({ name: 'synthetisches-vollpaket.json', mimeType: 'application/json', buffer: Buffer.from(packageText) });
  await page.getByRole('button', { name: 'Datei prüfen und Vorschau anzeigen' }).click();
  await expect(page.getByText('28 bestehende Quellen bleiben unverändert')).toBeVisible();
  const stage = page.getByRole('button', { name: 'Als Entwurf einlesen' });
  await expect(stage).toBeEnabled();
  await expect(page.getByLabel('Sensortyp bestätigen')).toHaveValue('');
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: path.join(output, 'firefox-desktop-file-rules.png') });
  await page.getByLabel('Sensortyp bestätigen').selectOption('contact');
  await page.getByLabel('Aktualität zuordnen').selectOption('freshness.contact');
  await expect(page.getByText('Auswahl geändert – Vorschau wird aktualisiert')).toBeVisible();
  await expect(stage).toBeDisabled();
  await expect(stage).toBeEnabled();
  await expect(stage).toBeInViewport();
  await page.screenshot({ path: path.join(output, 'firefox-desktop-ready.png') });
  const request = page.waitForRequest(request => request.url().endsWith('/portable/stage'));
  await stage.click();
  const staged = (await request).postDataJSON();
  await expect(page).toHaveURL(/#\/changes\/drafts\//);
  expect(staged.plan_digest).toMatch(/^[0-9a-f]{64}$/);
  expect(Object.keys(staged.freshness_choices)).toEqual(['source.synthetic.28']);
  expect(staged.freshness_choices['source.synthetic.28'].template_id).toBe('freshness.contact');
  const active = await (await page.request.get('http://127.0.0.1:8139/api/v1/registry/active')).json();
  expect(active.revision).toBe(1);
  expect(errors).toEqual([]);
  await writeFile(path.join(output, 'proof.json'), JSON.stringify({ synthetic: true, browser: 'Firefox', version: browser.version(), viewport: { width: 1920, height: 1080 }, unchangedSources: 28, assignedSources: 1, automaticPreview: true, stageWithoutSecondPreviewClick: true, activeRevisionUnchanged: true, errors, passed: true }, null, 2));
  console.log('Firefox desktop import proof passed: 28 reused + 1 new, automatic preview, real digest accepted, active revision unchanged.');
} finally {
  await browser?.close();
  child.kill();
}
