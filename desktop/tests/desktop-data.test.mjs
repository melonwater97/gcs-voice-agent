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
    prepareRuntimeConfig(store, { speechRoot: path.join(dir, 'resources', 'speech') });
    assert.equal(store.config.mcp[0].command, 'python');
    assert.deepEqual(store.config.mcp[0].args, ['-m', 'integrations.mcp_server', '--config', path.join(dir, 'mcp', 'groundstation.yaml'), '--request-dir', path.join(dir, 'requests', 'groundstation')]);
    assert.equal(store.config.mcp[0].env.PYTHONUTF8, '1');
    assert.equal(store.config.mcp[0].env.LH_LEGACY_PYTHON, undefined);
    assert.equal(store.config.mcp[0].env.LH_PROJECT_ROOT, undefined);
    assert.equal(store.config.mcp[0].env.ELECTRON_RUN_AS_NODE, undefined);
    assert.equal(store.config.mcp[0].env.TASK_STATE_PATH, undefined);
    assert.match(fs.readFileSync(path.join(dir, 'mcp', 'groundstation.yaml'), 'utf8'), /18003/);
    assert.equal(store.config.mcp[0].cwd, path.join(dir, 'resources', 'speech'));
    store.config.mcp[0].command = 'user-selected-python';
    prepareRuntimeConfig(store, { speechRoot: path.join(dir, 'new-resources', 'speech') });
    assert.equal(store.config.mcp[0].command, 'user-selected-python');
    assert.equal(store.config.mcp[0].cwd, path.join(dir, 'new-resources', 'speech'));
  } finally { assert.equal(path.dirname(dir), path.resolve(os.tmpdir())); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('old ground-station settings migrate to MCP YAML and encrypted environment without losing workflows', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-gcs-migration-'));
  try {
    const store = new Store(dir);
    // Simulate a previously saved configuration loaded before runtime preparation.
    store.config.gcs = { baseUrl: 'http://station.example:18001', timeoutSeconds: 22, token: 'private-station-token' };
    store.config.workflows = [{ id: 'old', steps: [{ id: 'create', type: 'tool', tool: 'mcp__groundstation__create_task' }] }];
    prepareRuntimeConfig(store);
    const text = fs.readFileSync(path.join(dir, 'mcp', 'groundstation.yaml'), 'utf8');
    assert.match(text, /station.example:18001/);
    assert.match(text, /simulation: false/);
    assert.equal(text.includes('private-station-token'), false);
    assert.equal(store.config.mcp[0].env.LH_GCS_TOKEN, 'private-station-token');
    assert.equal(store.publicConfig().mcp[0].env.LH_GCS_TOKEN, '••••••••');
    assert.equal(store.config.gcs, undefined);
    fs.writeFileSync(path.join(dir, 'mcp', 'groundstation.yaml'), 'user-edited-file');
    prepareRuntimeConfig(store);
    assert.equal(fs.readFileSync(path.join(dir, 'mcp', 'groundstation.yaml'), 'utf8'), 'user-edited-file');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
