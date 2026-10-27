import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Store, MASK } from '../server/store.mjs';

test('MCP paths remain editable while credentials stay masked across settings round trips', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-config-'));
  try {
    const store = new Store(dir);
    const config = structuredClone(store.config);
    config.voice.python = 'D:\\runtime\\python.exe';
    config.voice.modelDir = 'D:\\project\\models';
    config.mcp = [{ id: 'python', name: 'python_tasks', transport: 'stdio', command: 'node', args: [], enabled: true,
      env: { LH_LEGACY_PYTHON: config.voice.python, LH_PROJECT_ROOT: 'D:\\project', GITHUB_TOKEN: 'private-token-value' },
      headers: { Authorization: 'Bearer private-auth-value' } }];
    const visible = store.safe(store.saveConfig(config));
    assert.equal(visible.voice.python, config.voice.python);
    assert.equal(visible.voice.modelDir, config.voice.modelDir);
    assert.equal(visible.mcp[0].env.LH_PROJECT_ROOT, 'D:\\project');
    assert.equal(visible.mcp[0].env.GITHUB_TOKEN, MASK);
    assert.equal(visible.mcp[0].headers.Authorization, MASK);
    store.saveConfig(visible);
    assert.equal(store.config.voice.modelDir, config.voice.modelDir);
    assert.equal(store.config.mcp[0].env.GITHUB_TOKEN, 'private-token-value');
    assert.equal(store.safe({ totalTokens: 80 }).totalTokens, 80);
  } finally {
    assert.equal(path.dirname(path.resolve(dir)), path.resolve(os.tmpdir()));
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
