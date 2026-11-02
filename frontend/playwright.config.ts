import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './browser',
  fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:4174' },
  projects: [{ name: 'firefox', use: { ...devices['Desktop Firefox'] } }],
  webServer: { command: 'node browser/serve.mjs', url: 'http://127.0.0.1:4174', reuseExistingServer: false },
});
