import { test, expect } from '@playwright/test';
import { makeMessage } from '../src/lib/api.js';

test('录音 → 转写 → 真实 MCP 创建、上传、执行 → 刷新恢复任务', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('button', { name: '开始说话' })).toBeVisible();
  await page.screenshot({ path: 'test-results/ground-station-empty.png', fullPage: true });
  const sequence = [];
  page.on('request', request => {
    if (request.method() !== 'POST') return;
    const match = /\/(start|stop|text)$/.exec(request.url());
    if (match) sequence.push(match[1]);
  });
  await page.getByRole('button', { name: '开始说话' }).click();
  await expect(page.getByText('正在聆听', { exact: true })).toBeVisible();
  await page.waitForTimeout(600);
  await page.getByRole('button', { name: '结束录音并发送' }).click();
  await expect(page.getByText('语音转写', { exact: true })).toBeVisible();
  await expect(page.getByText('创建任务成功', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Agent 正在处理', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('创建任务成功', { exact: true })).toBeVisible();
  await expect(page.getByText('语音转写', { exact: true })).toBeVisible();
  await expect(page.getByText('create_task', { exact: true })).toBeVisible();
  await expect(page.getByRole('definition').filter({ hasText: '50' })).toBeVisible();
  expect(sequence).toEqual(['start', 'stop']);

  await page.getByRole('button', { name: '文字输入' }).click();
  const command = page.getByRole('textbox', { name: '文字口令' });
  await command.fill('上传刚才创建的任务');
  await page.getByRole('button', { name: '发送文字口令' }).click();
  await expect(page.getByText('上传任务成功', { exact: true })).toBeVisible();
  await command.fill('执行刚才上传的任务');
  await page.getByRole('button', { name: '发送文字口令' }).click();
  await expect(page.getByText('执行任务成功', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: /变化记录/ }).click();
  await expect(page.locator('.change-entry')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/ground-station-active.png', fullPage: true });

  await page.reload();
  await expect(page.locator('.task-item .badge')).toHaveText('执行中');
  await page.getByRole('tab', { name: /变化记录/ }).click();
  await expect(page.locator('.change-entry')).toHaveCount(3);
  expect(errors).toEqual([]);
});

test('澄清、麦克风开启失败和手机布局', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '开始说话' })).toBeVisible();
  await page.getByRole('button', { name: '文字输入' }).click();
  await page.getByRole('textbox', { name: '文字口令' }).fill('缺参任务');
  await page.getByRole('button', { name: '发送文字口令' }).click();
  await expect(page.locator('.bubble').filter({ hasText: '请补充起飞高度' })).toBeVisible();
  await expect(page.locator('.pending')).toContainText('请补充起飞高度');
  // 服务端开麦失败时前端展示服务端错误，不进入录音态
  await page.route('**/api/record', async route => {
    if (route.request().postDataJSON().payload.type !== 'record_start') return route.continue();
    return route.fulfill({
      status: 503, json: { payload: { code: 503, message: '无法开启麦克风：设备被占用' } } });
  });
  await page.getByRole('button', { name: '开始说话' }).click();
  await expect(page.getByText('无法开启麦克风：设备被占用', { exact: true })).toBeVisible();
  await expect(page.getByText('正在聆听', { exact: true })).toHaveCount(0);
  await page.unroute('**/api/record');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0); });
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/ground-station-mobile.png', fullPage: true });
});

test('响应丢失后沿用原消息编号重试，任务只创建一次', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '开始说话' })).toBeVisible();
  const before = (await (await page.request.post('/api/state', { data: makeMessage('STATE_QUERY', {}) })).json()).payload;
  const ids = [];
  await page.route('**/api/text', async route => {
    ids.push(route.request().postDataJSON().header.msg_id);
    if (ids.length === 1) {
      await route.fetch();  // 后端已完成创建，但浏览器没有收到响应。
      await route.abort('failed');
    } else await route.continue();
  });
  await page.getByRole('button', { name: '文字输入' }).click();
  await page.getByRole('textbox', { name: '文字口令' }).fill('新增放飞前出任务，原地起飞，起飞高度50米');
  await page.getByRole('button', { name: '发送文字口令' }).click();
  await page.getByRole('button', { name: '重试本次请求' }).click();
  await expect(page.getByText('创建任务成功', { exact: true })).toBeVisible();
  const after = (await (await page.request.post('/api/state', { data: makeMessage('STATE_QUERY', {}) })).json()).payload;
  expect(ids).toHaveLength(2);
  expect(ids[1]).toEqual(ids[0]);
  expect(after.tasks.length).toBe(before.tasks.length + 1);
});

test('结束录音响应丢失后按原消息编号重试，不回退文字输入且任务只创建一次', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '开始说话' })).toBeVisible();
  const before = (await (await page.request.post('/api/state', { data: makeMessage('STATE_QUERY', {}) })).json()).payload;
  const ids = [];
  const textRequests = [];
  page.on('request', request => { if (request.url().endsWith('/api/text')) textRequests.push(request.url()); });
  await page.route('**/api/record', async route => {
    if (route.request().postDataJSON().payload.type !== 'record_stop') return route.continue();
    ids.push(route.request().postDataJSON().header.msg_id);
    if (ids.length === 1) {
      await route.fetch();
      await route.abort('failed');
    } else await route.continue();
  });
  await page.getByRole('button', { name: '开始说话' }).click();
  await page.waitForTimeout(400);
  await page.getByRole('button', { name: '结束录音并发送' }).click();
  await page.getByRole('button', { name: '重试本次请求' }).click();
  await expect(page.getByText('创建任务成功', { exact: true })).toBeVisible();
  await expect(page.getByText('语音转写', { exact: true })).toBeVisible();
  expect(ids).toHaveLength(2);
  expect(ids[1]).toEqual(ids[0]);
  expect(textRequests).toEqual([]);
  const after = (await (await page.request.post('/api/state', { data: makeMessage('STATE_QUERY', {}) })).json()).payload;
  expect(after.tasks.length).toBe(before.tasks.length + 1);
});

test('操作成功后状态同步失败，不把成功操作标记成待重试', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '开始说话' })).toBeVisible();
  await page.route('**/api/text', async route => {
    const response = await route.fetch();
    await page.route('**/api/state', stateRoute => stateRoute.fulfill({
      status: 503, json: { payload: { code: 503, message: '暂时无法查询状态' } },
    }));
    await route.fulfill({ response });
  });
  await page.getByRole('button', { name: '文字输入' }).click();
  await page.getByRole('textbox', { name: '文字口令' }).fill('新增放飞前出任务，原地起飞，起飞高度50米');
  await page.getByRole('button', { name: '发送文字口令' }).click();
  await expect(page.getByText('创建任务成功', { exact: true })).toBeVisible();
  await expect(page.getByText('操作结果已收到，任务面板等待下一次同步。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '重试本次请求' })).toHaveCount(0);
});

test('收到转写后连接中断，重试不重复显示转写或创建任务', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '开始说话' })).toBeVisible();
  const before = (await (await page.request.post('/api/state', { data: makeMessage('STATE_QUERY', {}) })).json()).payload;
  await page.evaluate(() => {
    const original = window.fetch.bind(window);
    let interrupted = false;
    window.fetch = async (url, options) => {
      const response = await original(url, options);
      if (url !== '/api/record'
          || JSON.parse(options.body).payload.type !== 'record_stop' || interrupted) return response;
      interrupted = true;
      const reader = response.body.getReader();
      let buffer = '';
      const decoder = new TextDecoder();
      return new Response(new ReadableStream({ async start(controller) {
        while (true) {
          const chunk = await reader.read();
          buffer += decoder.decode(chunk.value, { stream: !chunk.done });
          const newline = buffer.indexOf('\n');
          if (newline !== -1) {
            controller.enqueue(new TextEncoder().encode(buffer.slice(0, newline + 1)));
            controller.close();
            await reader.cancel();
            return;
          }
          if (chunk.done) { controller.close(); return; }
        }
      } }), { status: response.status, headers: response.headers });
    };
  });
  const ids = [];
  page.on('request', request => { if (request.url().endsWith('/api/record')
    && request.postDataJSON().payload.type === 'record_stop') ids.push(request.postDataJSON().header.msg_id); });
  await page.getByRole('button', { name: '开始说话' }).click();
  await page.waitForTimeout(400);
  await page.getByRole('button', { name: '结束录音并发送' }).click();
  await expect(page.getByText('语音转写', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重试本次请求' }).click();
  await expect(page.getByText('创建任务成功', { exact: true })).toBeVisible();
  await expect(page.getByText('语音转写', { exact: true })).toHaveCount(1);
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
  const after = (await (await page.request.post('/api/state', { data: makeMessage('STATE_QUERY', {}) })).json()).payload;
  expect(after.tasks.length).toBe(before.tasks.length + 1);
});
