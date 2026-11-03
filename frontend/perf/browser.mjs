// Start Vite separately, then: node perf/browser.mjs
import { chromium } from '@playwright/test';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
try {
  const page = await browser.newPage();
  page.on('pageerror', error => console.error('synthetic page error:', error.stack));
  page.on('console', message => { if (message.type() === 'error') console.error('synthetic console error:', message.text()); });
  await page.goto('http://127.0.0.1:5173/perf/');
  try {
    await page.getByRole('button', { name: /source.synthetic0/ }).first().click({ timeout: 10000 });
  } catch (error) {
    console.error('synthetic requests', await page.evaluate(() => window.syntheticCalls));
    console.error('synthetic body', (await page.locator('body').innerText()).slice(0, 1200));
    throw error;
  }
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Profiler.enable');
  await cdp.send('Profiler.start');
  const before = await page.evaluate(() => window.syntheticCalls.length);
  const start = performance.now();
  await page.getByRole('button', { name: 'Entität ändern' }).click();
  await page.getByRole('dialog').waitFor();
  const wall = performance.now() - start;
  const { profile } = await cdp.send('Profiler.stop');
  const nodes = new Map(profile.nodes.map(node => [node.id, node]));
  const counts = new Map();
  for (const id of profile.samples ?? []) counts.set(id, (counts.get(id) ?? 0) + 1);
  const mainThreadMs = (profile.timeDeltas ?? []).reduce((sum, micros, i) => {
    const frame = nodes.get(profile.samples[i])?.callFrame;
    return sum + (frame?.functionName === '(idle)' ? 0 : micros / 1000);
  }, 0);
  const hot = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([id, samples]) => {
    const frame = nodes.get(id).callFrame;
    return { function: frame.functionName || '(anonymous)', file: frame.url.split('/').at(-1), line: frame.lineNumber + 1, samples };
  });
  console.log(JSON.stringify({ scenario: 'chrome 6000 unmatched entities, nine mixed drafts', wall_ms: wall, main_thread_sampled_ms: mainThreadMs, total_samples: profile.samples?.length ?? 0, hot,
    requests: (await page.evaluate(() => window.syntheticCalls)).slice(before) }, null, 2));
} finally {
  await browser.close();
}
