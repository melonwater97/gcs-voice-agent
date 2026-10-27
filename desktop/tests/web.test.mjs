import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import os from 'node:os';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium, expect } from '@playwright/test';
import { startServer } from '../server/index.mjs';
import { prepareRuntimeConfig, saveMcpYaml } from '../server/runtime-config.mjs';
import { httpSimulator } from './helpers/http-simulator.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));

test('Web UI loads, manages settings and runs Pi tools without replaying on reload', { timeout: 60000 }, async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-web-'));
  const requests = [];
  const model = http.createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw); requests.push(body);
    const hasToolResult = body.messages.some(message => message.role === 'tool');
    const delta = hasToolResult
      ? { role: 'assistant', content: '已检索到资料，上传前需检查起飞高度。' }
      : { role: 'assistant', tool_calls: [{ index: 0, id: 'call_knowledge', type: 'function', function: { name: 'knowledge_search', arguments: '{"query":"上传 起飞高度"}' } }] };
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(`data: ${JSON.stringify({ id: 'chat-test', object: 'chat.completion.chunk', model: 'local-test', choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`);
    res.write(`data: ${JSON.stringify({ id: 'chat-test', object: 'chat.completion.chunk', model: 'local-test', choices: [{ index: 0, delta: {}, finish_reason: hasToolResult ? 'stop' : 'tool_calls' }], usage: { prompt_tokens: 100, completion_tokens: 15, total_tokens: 115 } })}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve => model.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => model.close(resolve)));
  const app = await startServer({ port: 0, dataDir: dir });
  t.after(() => app.close());
  app.store.saveConfig({ ...app.store.config, model: { ...app.store.config.model, baseUrl: `http://127.0.0.1:${model.address().port}/v1`, model: 'local-test' } });
  if (process.env.LH_TEST_PYTHON) {
    const baseUrl = await httpSimulator(t, dir);
    app.store.saveConfig({ ...app.store.config, mcp: [{ id: 'python-task-simulator', name: 'python_tasks', transport: 'stdio', enabled: false, command: process.env.LH_TEST_PYTHON, args: [], env: {} }] });
    prepareRuntimeConfig(app.store);
    await saveMcpYaml(app.store, 'python-task-simulator', `ground_station:\n  base_url: ${baseUrl}\n  simulation: true\n`);
  }
  await app.knowledge.add('检查手册.md', Buffer.from('上传任务前应检查起飞高度、任务名称及编队参数。').toString('base64'));
  const vite = await createServer({ root, configFile: path.join(root, 'vite.config.mjs'), logLevel: 'error', server: { host: '127.0.0.1', port: 5181, strictPort: true, proxy: { '/api': { target: `http://127.0.0.1:${app.port}`, timeout: 0 } } } });
  await vite.listen(); t.after(() => vite.close());
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:5181/');
  await page.getByRole('heading', { name: '让想法，成为行动。' }).waitFor();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  assert.equal(await page.getByRole('navigation').getByRole('button', { name: '地面站', exact: true }).count(), 0);
  for (const name of ['通用', '模型服务', '语音输入', '知识库', 'MCP 服务', 'Skills', '工作流']) {
    await page.getByRole('navigation').getByRole('button', { name, exact: true }).click();
    await page.getByRole('heading', { name, exact: true }).waitFor();
    if (name === 'MCP 服务' && process.env.LH_TEST_PYTHON) {
      assert.equal(await page.getByRole('switch', { name: '启用 MCP python_tasks', exact: true }).count(), 0);
      assert.equal(await page.getByRole('textbox', { name: '程序路径', exact: true }).isVisible(), false);
      await page.getByRole('button', { name: '配置服务 python_tasks', exact: true }).click();
      const editor = page.getByRole('textbox', { name: '服务配置 YAML', exact: true });
      const yaml = await editor.inputValue();
      assert.match(yaml, /simulation: true/);
      await editor.fill('ground_station:\n  timeout_seconds: 0');
      await page.getByRole('button', { name: '保存 YAML 配置', exact: true }).click();
      await page.locator('.settings-footer .error-text').waitFor();
      await editor.fill(yaml);
      await page.getByRole('button', { name: '保存 YAML 配置', exact: true }).click();
      await page.getByText('YAML 配置已保存，运行中的服务已重新加载。', { exact: true }).waitFor();
      await page.getByRole('button', { name: '关闭服务配置', exact: true }).click();
      await page.getByRole('button', { name: '测试连接 python_tasks', exact: true }).click();
      await page.getByText('HTTP 可达 · 模拟服务', { exact: true }).waitFor();
      await page.getByRole('button', { name: '工具 create_task', exact: true }).waitFor();
      assert.equal(app.mcp.snapshot('python-task-simulator').status, 'paused', 'Testing must not start a paused service permanently');
      await page.getByRole('button', { name: '启动服务 python_tasks', exact: true }).click();
      await page.getByRole('button', { name: '暂停服务 python_tasks', exact: true }).waitFor();
      const pid = app.mcp.snapshot('python-task-simulator').pid;
      assert.ok(pid);
      await page.getByRole('button', { name: '工具 create_task', exact: true }).click();
      const toolDetail = page.getByRole('complementary', { name: 'MCP 工具详情' });
      await toolDetail.getByText('当前可调用', { exact: true }).waitFor();
      await toolDetail.getByRole('button', { name: '参数', exact: true }).click();
      await toolDetail.getByText('task', { exact: true }).waitFor();
      await toolDetail.getByText('必填', { exact: true }).waitFor();
      await toolDetail.getByRole('button', { name: 'Schema', exact: true }).click();
      assert.match(await toolDetail.locator('pre').innerText(), /takeoff_altitude/);
      await page.screenshot({ path: path.join(root, '.data', 'mcp-service-detail-preview.png'), fullPage: true });
      await page.getByRole('button', { name: '暂停服务 python_tasks', exact: true }).click();
      await expect(page.getByRole('button', { name: '启动服务 python_tasks', exact: true })).toBeEnabled();
      await toolDetail.getByRole('button', { name: '概述', exact: true }).click();
      await toolDetail.getByText('当前不可调用', { exact: true }).waitFor();
      assert.throws(() => process.kill(pid, 0));
      await page.getByRole('button', { name: '关闭工具详情', exact: true }).click();
      await page.getByRole('button', { name: '收起服务 python_tasks', exact: true }).click();
      assert.equal(await page.getByRole('button', { name: '工具 create_task', exact: true }).count(), 0);
      await page.getByRole('button', { name: '保存设置', exact: true }).click();
      await page.getByText('设置已保存，下一次执行生效', { exact: true }).waitFor();
      assert.equal(app.store.config.mcp[0].enabled, false, 'Saving the settings form must not undo a pause');
      await page.screenshot({ path: path.join(root, '.data', 'mcp-service-list-preview.png'), fullPage: true });
    }
  }
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('textbox', { name: '消息输入' }).fill('请从知识库检索上传前的检查事项');
  await page.getByRole('button', { name: '发送消息' }).click();
  await page.getByText('已检索到资料，上传前需检查起飞高度。', { exact: true }).waitFor({ timeout: 30000 });
  assert.equal(requests.length, 2);
  await page.getByRole('button', { name: /检索知识库.*已完成/ }).click();
  await page.getByRole('button', { name: 'Schema', exact: true }).click();
  assert.match(await page.locator('.inspector pre').innerText(), /query/);
  await page.reload();
  await page.getByText('已检索到资料，上传前需检查起飞高度。', { exact: true }).waitFor();
  assert.equal(requests.length, 2, 'Reading history must not repeat inference or tool calls');
  const recorded = app.store.getSession(app.store.listSessions()[0].id);
  const completedModel = recorded.records.findLast(row => row.kind === 'assistant');
  assert.equal(recorded.records.find(row => row.kind === 'run').stepCount, 2);
  assert.equal(Number.isFinite(completedModel.durationMs), true);
  assert.equal(Number.isFinite(completedModel.ttftMs), true);
  assert.equal(recorded.records.some(row => row.kind === 'context' && row.context.systemPrompt.includes('你是领航')), true);
  await page.getByRole('button', { name: '轨迹', exact: true }).click();
  await page.getByRole('region', { name: '轨迹视图' }).waitFor();
  await page.getByRole('textbox', { name: '搜索轨迹' }).fill('检查手册');
  await page.getByRole('combobox', { name: '记录类型' }).selectOption('tool');
  const ledger = page.getByLabel('轨迹记录列表', { exact: true });
  await ledger.getByRole('button', { name: /工具.*knowledge_search/ }).click();
  const inspector = page.getByRole('complementary', { name: '工具详情' });
  await inspector.getByRole('button', { name: '参数', exact: true }).click();
  assert.match(await inspector.locator('pre').innerText(), /上传 起飞高度/);
  await inspector.getByRole('button', { name: '结果', exact: true }).click();
  assert.match(await inspector.locator('pre').innerText(), /检查手册/);
  await inspector.getByRole('button', { name: 'Schema', exact: true }).click();
  assert.match(await inspector.locator('pre').innerText(), /query/);
  await inspector.getByRole('button', { name: '计时', exact: true }).click();
  assert.match(await inspector.locator('pre').innerText(), /开始时间/);
  await inspector.getByRole('button', { name: '概述', exact: true }).click();
  await page.getByRole('button', { name: '清除搜索', exact: true }).click();
  await page.getByRole('combobox', { name: '记录类型' }).selectOption('all');
  await page.screenshot({ path: path.join(root, '.data', 'trajectory-inspector-preview.png'), fullPage: true });
  await page.getByRole('button', { name: '折叠第1轮', exact: true }).click();
  assert.equal(await ledger.locator('.trace-row').count(), 0);
  await page.getByRole('button', { name: '展开第1轮', exact: true }).click();
  await page.getByRole('combobox', { name: '记录类型' }).selectOption('context');
  await ledger.getByRole('button', { name: /上下文.*系统提示词/ }).click();
  await page.getByRole('complementary', { name: '轨迹详情' }).getByRole('button', { name: '上下文', exact: true }).click();
  assert.match(await page.locator('.trajectory-inspector pre').innerText(), /你是领航/);
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.getByRole('combobox', { name: '记录类型' }).selectOption('all');
  const plot = page.locator('.timeline-plot'), ticks = await page.locator('.timeline-ticks').innerText(), box = await plot.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 60); await page.mouse.wheel(0, -400);
  await page.waitForFunction(before => document.querySelector('.timeline-ticks').innerText !== before, ticks);
  await page.getByRole('button', { name: '重置时间轴', exact: true }).click();
  await page.mouse.move(box.x + box.width * .2, box.y + 63); await page.mouse.down();
  await page.mouse.move(box.x + box.width * .6, box.y + 63, { steps: 5 }); await page.mouse.up();
  await page.locator('.trace-time-filter').waitFor();
  await page.getByRole('button', { name: '清除时间筛选', exact: true }).click();

  // A long fixture checks full-history search, virtual rows and stable reading position.
  const history = app.store.newSession(), now = Date.now() - 1500000;
  history.title = '长会话轨迹回归';
  for (let n = 1; n <= 1500; n++) {
    const runId = `history-run-${n}`, startedAt = now + n * 1000, common = { runId, turnNumber: n, status: 'completed' };
    history.records.push(
      { ...common, id: `run-${n}`, kind: 'run', startedAt, durationMs: 900, stepCount: 1 },
      { ...common, id: `user-${n}`, kind: 'user', text: `任务查询 ${n}`, startedAt },
      { ...common, id: `assistant-${n}`, kind: 'assistant', stepNumber: 1, text: `检查任务参数 ${n}`, startedAt: startedAt + 10, durationMs: 300, ttftMs: 100, usage: { totalTokens: 140 } },
      { ...common, id: `history-tool-${n}`, kind: 'tool', stepNumber: 1, name: 'knowledge_search', callId: `history-call-${n}`, parentRecordId: `assistant-${n}`, args: { query: `archive-target-${n}` }, result: { text: `任务检查结果 ${n}` }, schema: { type: 'object', properties: { query: { type: 'string' } } }, startedAt: startedAt + 320, durationMs: 150 },
    );
  }
  app.store.saveSession(history);
  await page.reload();
  await page.getByText('长会话轨迹回归', { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll('.trace-row').length > 10);
  assert.ok(await page.locator('.trace-row').count() < 100, 'Long history must render a bounded visible window');
  await page.screenshot({ path: path.join(root, '.data', 'trajectory-long-preview.png'), fullPage: true });
  await page.locator('[data-record-id="history-tool-1500"] .trace-row-main').click();
  await page.screenshot({ path: path.join(root, '.data', 'trajectory-long-inspector-preview.png'), fullPage: true });
  await page.getByRole('textbox', { name: '搜索轨迹' }).fill('archive-target-20"');
  await page.locator('[data-record-id="history-tool-20"]').waitFor();
  assert.equal(await page.locator('.trace-row').count(), 1);
  await page.getByRole('button', { name: '清除搜索', exact: true }).click();
  await ledger.evaluate(el => { el.scrollTop = 350; });
  await page.waitForFunction(() => document.querySelector('.trace-ledger').scrollTop === 350);
  app.engine.row(history, { kind: 'notice', text: '新增记录不打断历史阅读', status: 'completed' });
  await page.waitForFunction(() => document.querySelector('.trace-count').textContent.includes('4,501'));
  assert.equal(await ledger.evaluate(el => el.scrollTop), 350);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(root, '.data', 'trajectory-preview.png'), fullPage: true });
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
});
