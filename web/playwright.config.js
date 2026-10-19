import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:5174',
    channel: 'chrome',
    viewport: { width: 1440, height: 1000 },
    permissions: ['microphone'],
    launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  },
  webServer: [
    { command: 'python -m tests.web_test_server', cwd: '..', port: 18012, reuseExistingServer: false },
    { command: process.env.GCS_TEST_PREVIEW ? 'npm run preview -- --port 5174' : 'npm run dev -- --port 5174', env: { AGENT_URL: 'http://127.0.0.1:18012' }, url: 'http://127.0.0.1:5174', reuseExistingServer: false },
  ],
});
