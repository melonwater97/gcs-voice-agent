import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { startServer } from '../server/index.mjs';
import { Voice } from '../server/voice.mjs';

async function fixture(t, options = {}) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-origin-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(dataDir)), path.resolve(os.tmpdir()));
    fs.rmSync(dataDir, { recursive: true, force: true });
  });
  const app = await startServer({ port: 0, dataDir, token: '', ...options });
  t.after(() => app.close());
  return app;
}

function post(app, route, headers = {}, body = {}) {
  return fetch(`http://127.0.0.1:${app.port}${route}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body),
  });
}

test('localhost and IPv4 loopback origins can submit text and voice on actual and development ports', async t => {
  const app = await fixture(t);
  const runs = t.mock.method(app.engine, 'start', () => ({ runId: 'origin-test' }));
  // Exercise the real voice start/stop routes without accessing a microphone or ASR model.
  t.mock.method(Voice.prototype, 'request', async command => command === 'stop' ? { text: '语音输入' } : { recording: true });
  for (const host of ['127.0.0.1', 'localhost']) {
    for (const port of [app.port, 5180, 5181]) {
      const headers = { Origin: `http://${host}:${port}` };
      const created = await post(app, '/api/sessions', headers);
      assert.equal(created.status, 200, headers.Origin);
      const session = await created.json();
      const text = await post(app, '/api/run', headers, { sessionId: session.id, text: '文字输入' });
      assert.equal(text.status, 200, headers.Origin);
      assert.equal(runs.mock.calls.at(-1).arguments[1], '文字输入');
      const started = await post(app, '/api/voice/start', headers);
      assert.equal(started.status, 200, headers.Origin);
      const stopped = await post(app, '/api/voice/stop', headers, { sessionId: session.id });
      assert.equal(stopped.status, 200, headers.Origin);
      assert.equal((await stopped.json()).text, '语音输入');
      assert.equal(runs.mock.calls.at(-1).arguments[1], '语音输入');
    }
  }
  assert.equal(runs.mock.callCount(), 12);
});

test('untrusted origins and hosts remain blocked before text or voice processing', async t => {
  const app = await fixture(t);
  const run = t.mock.method(app.engine, 'start');
  const voice = t.mock.method(Voice.prototype, 'request');
  const origins = ['https://example.com', 'null', 'http://127.0.0.1:0', 'http://localhost:0',
    'http://127.0.0.1:1', 'http://localhost:1', 'http://127.0.0.1:5182',
    'http://localhost:5182', 'https://localhost:5180', 'http://localhost.evil.example:5180',
    'http://127.0.0.1.evil.example:5180', 'http://[::1]:5180'];
  for (const Origin of origins) {
    for (const route of ['/api/run', '/api/voice/start']) {
      const response = await post(app, route, { Origin });
      assert.equal(response.status, 403, `${Origin} ${route}`);
      assert.equal((await response.json()).error, '不支持的请求来源');
    }
  }
  const response = await new Promise((resolve, reject) => {
    const req = http.request(`http://127.0.0.1:${app.port}/api/sessions`, {
      method: 'POST', headers: { Host: `evil.example:${app.port}`, Origin: 'http://localhost:5180' },
    }, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    req.end();
  });
  assert.equal(response.status, 403);
  assert.equal(JSON.parse(response.body).error, '不支持的主机');
  assert.equal(run.mock.callCount(), 0);
  assert.equal(voice.mock.callCount(), 0);
  assert.equal(app.store.listSessions().length, 0);
});

test('allowed local origins still require the backend token and Electron requests may omit Origin', async t => {
  const app = await fixture(t, { token: 'test-backend-token' });
  const headers = { Origin: `http://localhost:${app.port}` };
  for (const auth of [{}, { Authorization: 'Bearer wrong-token' }]) {
    const response = await post(app, '/api/sessions', { ...headers, ...auth });
    assert.equal(response.status, 401);
    assert.equal((await response.json()).error, '未授权');
  }
  for (const origin of [headers, {}]) {
    const response = await post(app, '/api/sessions', { ...origin, Authorization: 'Bearer test-backend-token' });
    assert.equal(response.status, 200);
    assert.ok((await response.json()).id);
  }
});
