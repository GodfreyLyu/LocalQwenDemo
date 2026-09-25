import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Isolate fake accounts/history/artifacts from every existing operator data directory.
const testRoot =
  process.env.LOCAL_REVIEW_TEST_ROOT ??
  mkdtempSync(join(tmpdir(), 'local-qwen-e2e-'));
const dataDirectory =
  "'" + join(testRoot, 'data').replaceAll("'", "'\"'\"'") + "'";

export default defineConfig({
  testDir: './e2e',
  outputDir: join(testRoot, 'results'),
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: { baseURL: 'http://localhost:5173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `../backend/.venv/bin/python ../scripts/local_demo.py --fake-model --data-dir ${dataDirectory}`,
      url: 'http://127.0.0.1:8000/health/ready',
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      url: 'http://localhost:5173',
      reuseExistingServer: false,
    },
  ],
});
