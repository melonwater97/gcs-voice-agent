import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { createRequire } from 'node:module';
import { Store } from '../server/store.mjs';
import { prepareRuntimeConfig } from '../server/runtime-config.mjs';

const { migrateWebData } = createRequire(import.meta.url)('../electron/data-migration.cjs');
test('desktop import re-encrypts Web settings, relocates session files and preserves existing desktop settings', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-desktop-data-'));
  try {
    const source = new Store(path.join(dir, 'web')), destination = path.join(dir, 'desktop'), key = randomBytes(32).toString('base64');
    source.saveConfig({ ...source.config, model: { ...source.config.model, model: 'deepseek-flash', apiKey: 'test-private-api-key' } });
    const session = source.newSession(); session.piFile = path.join(source.dir, 'pi', 'sessions', 'test.jsonl'); source.saveSession(session);
    fs.mkdirSync(path.dirname(session.piFile), { recursive: true }); fs.writeFileSync(session.piFile, 'test');
    assert.equal(migrateWebData(source.dir, destination, key), true);
    const imported = new Store(destination, key);
    assert.equal(imported.config.model.apiKey, 'test-private-api-key');
    assert.equal(imported.getSession(session.id).piFile, path.join(destination, 'pi', 'sessions', 'test.jsonl'));
    assert.equal(fs.readFileSync(path.join(destination, 'settings.enc.json'), 'utf8').includes('test-private-api-key'), false);
    imported.saveConfig({ ...imported.config, model: { ...imported.config.model, model: 'user-selected-model' } });
    assert.equal(migrateWebData(source.dir, destination, key), false);
    assert.equal(new Store(destination, key).config.model.model, 'user-selected-model');
  } finally { assert.equal(path.dirname(dir), path.resolve(os.tmpdir())); fs.rmSync(dir, { recursive: true, force: true }); }
});
test('bundled Python MCP resolves to runtime resources and desktop task storage', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-runtime-'));
  try {
    const store = new Store(dir);
    store.saveConfig({ ...store.config, mcp: [{ id: 'python-task-simulator', name: 'python_tasks', transport: 'stdio', enabled: true,
      command: 'old-node', args: ['D:\\old-project\\python-mcp.mjs'], env: { LH_LEGACY_PYTHON: 'python' }, headers: {} }] });
    prepareRuntimeConfig(store, { speechRoot: path.join(dir, 'resources', 'speech'), node: path.join(dir, '领航.exe') });
    assert.equal(store.config.mcp[0].command, path.join(dir, '领航.exe'));
    assert.equal(store.config.mcp[0].env.ELECTRON_RUN_AS_NODE, '1');
    assert.equal(store.config.mcp[0].env.TASK_STATE_PATH, path.join(dir, 'python-tasks', 'state.json'));
    assert.equal(store.config.mcp[0].cwd, path.join(dir, 'resources', 'speech'));
  } finally { assert.equal(path.dirname(dir), path.resolve(os.tmpdir())); fs.rmSync(dir, { recursive: true, force: true }); }
});
