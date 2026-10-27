import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { readSkill } from './knowledge.mjs';
import { runWorkflow } from './workflows.mjs';
import { uid } from './store.mjs';
import { scenarioFor, executionRules } from './scenarios.mjs';

const textResult = data => ({ content: [{ type: 'text', text: typeof data === 'string' ? data : JSON.stringify(data) }], details: { data } });
const stringObject = (name, description) => ({ type: 'object', required: [name], additionalProperties: false, properties: { [name]: { type: 'string', description } } });

export class Engine {
  constructor(store, knowledge, notify, mcp = null, flights = null) { this.store = store; this.knowledge = knowledge; this.notify = notify; this.mcp = mcp; this.flights = flights; this.active = null; }
  assertIdle() { if (this.active) throw Object.assign(new Error('当前任务仍在执行，请先停止或等待完成'), { statusCode: 409 }); }
  row(session, data) {
    const row = this.store.safe({ id: uid(), runId: this.active?.runId, turnNumber: this.active?.turnNumber,
      stepNumber: this.active?.stepNumber || undefined, parentRecordId: this.active?.parentRecordId,
      at: new Date().toISOString(), status: 'completed', ...data });
    session.records.push(row); this.persist(session, row); return row;
  }
  persist(session, row, streaming = false) {
    this.notify({ type: 'record', sessionId: session.id, record: this.store.safe(row) });
    if (streaming) {
      if (!this.flushTimer) this.flushTimer = setTimeout(() => { this.flushTimer = null; this.store.saveSession(session); }, 200);
    } else { clearTimeout(this.flushTimer); this.flushTimer = null; this.store.saveSession(session); }
  }
  start(id, text, options = {}) {
    this.assertIdle();
    if (typeof text !== 'string' || !text.trim() || text.length > 32000) throw new Error('请输入 1–32000 个字符');
    const session = this.store.getSession(id), controller = new AbortController();
    const runId = uid();
    const oldRuns = new Set(session.records.filter(r => r.kind === 'user').map(r => r.runId || r.id));
    const turnNumber = Math.max(oldRuns.size, ...session.records.map(r => r.turnNumber || 0)) + 1;
    this.active = { sessionId: id, runId, turnNumber, stepNumber: 0, controller, pi: null, timings: new Map() };
    if (session.records.length === 0) session.title = text.trim().slice(0, 30);
    const runRow = this.row(session, { kind: 'run', text: options.workflowId ? '工作流运行' : '对话运行', startedAt: Date.now(), status: 'running', stepCount: 0 });
    if (options.inputRecordId) {
      const input = session.records.find(r => r.id === options.inputRecordId);
      if (input) { Object.assign(input, { runId, turnNumber }); this.persist(session, input); }
    }
    this.row(session, { kind: 'user', text, source: options.inputRecordId ? 'voice' : 'text', startedAt: Date.now() });
    this.notify({ type: 'status', sessionId: id, status: 'running', runId });
    this.task = this.execute(session, text, options).catch(error => {
      this.row(session, { kind: 'notice', status: controller.signal.aborted ? 'interrupted' : 'failed', text: controller.signal.aborted ? '已停止后续执行；已发送的地面站操作请核对回执。' : this.store.safe(error.message) });
    }).finally(async () => {
      for (const row of session.records) if (row.runId === runId && row.kind !== 'run' && ['running', 'streaming'].includes(row.status)) {
        row.status = row.kind === 'tool' ? 'unknown' : 'interrupted'; this.persist(session, row);
      }
      const failed = session.records.some(r => r.runId === runId && ['failed', 'unknown'].includes(r.status));
      Object.assign(runRow, { status: controller.signal.aborted ? 'interrupted' : failed ? 'failed' : 'completed',
        endedAt: Date.now(), durationMs: Date.now() - runRow.startedAt, stepCount: this.active?.stepNumber || 0 });
      this.persist(session, runRow);
      try {
        // Release per-session extensions; shared MCP connections belong to the host.
        await this.active?.pi?.extensionRunner.emit({ type: 'session_shutdown', reason: 'quit' });
      } finally {
        this.active?.pi?.dispose(); this.active = null;
        this.store.saveSession(session); this.notify({ type: 'status', sessionId: id, status: 'idle' });
      }
    });
    return { runId };
  }
  async stop() {
    if (!this.active) return;
    this.active.controller.abort(); await this.active.pi?.abort();
    await this.task;
  }
  skills() {
    const session = this.active && this.store.getSession(this.active.sessionId), scenario = scenarioFor(this.store.config, session?.scenarioId || 'groundstation');
    const skills = this.store.config.skills.filter(s => s.enabled && (scenario.skillIds === null || scenario.skillIds.includes(s.id))).map(s => {
      try { return { ...readSkill(s.directory), id: s.id }; }
      catch (error) { return { id: s.id, name: s.name, error: error.message }; }
    });
    if (scenario.flight) skills.push({ ...readSkill(fileURLToPath(new URL('../resources/skills/px4-simulation', import.meta.url))), id: 'builtin-px4-guide' });
    return skills;
  }
  loadSkill(name) {
    const skill = this.skills().find(s => s.name === name || s.id === name);
    if (!skill) throw new Error('Skill 不存在或已禁用');
    if (skill.error) throw new Error(skill.error);
    return { name: skill.name, content: skill.content, compatibility: skill.compatibility, note: '此版本提供指导与资源读取，不执行 Skill 目录中的脚本。' };
  }
  async makePi(session) {
    // Restore the task catalogue from recorded results without executing tools.
    for (const row of session.records) if (row.kind === 'tool' && row.status === 'completed') this.rememberTask(session, row.result);
    const config = this.store.config, piDir = path.join(this.store.dir, 'pi'), scenario = scenarioFor(config, session.scenarioId || 'groundstation');
    const runtime = await ModelRuntime.create({ authPath: path.join(piDir, 'auth.json'), modelsPath: null, modelsStorePath: path.join(piDir, 'models-cache.json'), refreshOnCreate: false });
    runtime.registerProvider('linghang', { baseUrl: config.model.baseUrl.replace(/\/chat\/completions\/?$/, '').replace(/\/$/, ''), api: 'openai-completions', apiKey: config.model.apiKey || 'local-no-key',
      models: [{ id: config.model.model, name: config.model.model, reasoning: false, input: ['text'], contextWindow: config.model.contextWindow, maxTokens: config.model.maxTokens,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, compat: { supportsDeveloperRole: false, supportsStore: false, maxTokensField: 'max_tokens', supportsReasoningEffort: false } }] });
    const customTools = [
      { name: 'knowledge_search', label: '检索知识库', description: '检索用户已启用的本地资料。根据片段回答并使用返回的 citation 标注来源；无结果时不能编造依据。', parameters: stringObject('query', '检索问题'), execute: async (_id, args) => textResult(this.knowledge.search(args.query)) },
      { name: 'load_skill', label: '加载 Skill', description: '读取已启用的 Skill 指导内容，按实际依赖和工具能力完成任务。', parameters: stringObject('name', 'Skill 名称'), execute: async (_id, args) => textResult(this.loadSkill(args.name)) },
      ...await this.mcp.piTools(scenario.flight ? [] : scenario.mcpIds),
      ...(scenario.flight ? this.flights.tools(session) : []),
    ];
    const skills = this.skills();
    const flightState = scenario.flight ? this.flights.forSession(session.id) : null;
    const systemPrompt = `${scenario.systemPrompt}\n${executionRules}\n启用的 Skills（通过 load_skill 显式读取）：${JSON.stringify(skills.map(s => ({ name: s.name, description: s.description, error: s.error })))}\n已知任务：${JSON.stringify(session.tasks || {})}${flightState ? '\n当前仿真流程：' + JSON.stringify({ id: flightState.id, phase: flightState.phase, review: flightState.review, pending: flightState.approvals.filter(a => a.status === 'pending') }) : ''}`;
    const settingsManager = SettingsManager.inMemory({ compaction: { enabled: true }, retry: { enabled: true, maxRetries: 1 }, packages: [] });
    const loader = new DefaultResourceLoader({ cwd: piDir, agentDir: piDir, settingsManager, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
      systemPrompt,
    });
    await loader.reload();
    const manager = session.piFile && fs.existsSync(session.piFile) ? SessionManager.open(session.piFile, undefined, piDir) : SessionManager.create(piDir, path.join(piDir, 'sessions'));
    const { session: pi } = await createAgentSession({ cwd: piDir, agentDir: piDir, modelRuntime: runtime, model: runtime.getModel('linghang', config.model.model), thinkingLevel: 'off',
      resourceLoader: loader, settingsManager, sessionManager: manager, noTools: 'builtin', customTools });
    this.active.pi = pi;
    pi.subscribe(event => this.piEvent(session, pi, event));
    await pi.bindExtensions({ uiContext: { notify: (message, level) => this.row(session, { kind: 'notice', text: this.store.safe(message), status: level === 'error' ? 'failed' : 'completed' }) } });
    session.piFile = manager.getSessionFile(); this.store.saveSession(session);
    return pi;
  }
  piEvent(session, pi, event) {
    if (!this.active || this.active.sessionId !== session.id) return;
    const runId = this.active.runId;
    if (event.type === 'turn_start') {
      this.active.stepNumber++;
      this.active.stepStartedAt = Date.now(); this.active.stepStartedMono = performance.now();
      const prompt = pi.systemPrompt;
      if (this.active.lastContext !== prompt) {
        this.active.lastContext = prompt;
        this.row(session, { kind: 'context', text: '系统提示词与运行上下文', startedAt: Date.now(),
          context: { systemPrompt: prompt, model: pi.model?.id, provider: pi.model?.provider, tools: pi.getActiveToolNames() } });
      }
    }
    if (event.type === 'message_start' && event.message.role === 'assistant') {
      this.assistantRow = this.row(session, { kind: 'assistant', text: '', status: 'streaming',
        startedAt: this.active.stepStartedAt || Date.now(), model: pi.model?.id });
      this.active.timings.set(this.assistantRow.id, { start: this.active.stepStartedMono || performance.now() });
    }
    if (event.type === 'message_update') {
      const update = event.assistantMessageEvent;
      if (update && ['text_delta', 'thinking_delta', 'toolcall_delta'].includes(update.type)) {
        if (!this.assistantRow || this.assistantRow.runId !== runId) {
          this.assistantRow = this.row(session, { kind: 'assistant', text: '', status: 'streaming', startedAt: Date.now(), model: pi.model?.id });
          this.active.timings.set(this.assistantRow.id, { start: performance.now() });
        }
        const timing = this.active.timings.get(this.assistantRow.id);
        if (!this.assistantRow.firstTokenAt && update.delta) {
          this.assistantRow.firstTokenAt = Date.now();
          timing.first = performance.now(); this.assistantRow.ttftMs = Math.max(0, timing.first - timing.start);
        }
        if (update.type === 'text_delta') this.assistantRow.text += update.delta;
        if (update.type === 'thinking_delta') this.assistantRow.thinking = (this.assistantRow.thinking || '') + update.delta;
        this.persist(session, this.assistantRow, true);
      }
    }
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const message = event.message, text = message.content.filter(c => c.type === 'text').map(c => c.text).join('\n');
      const row = this.assistantRow?.runId === runId ? this.assistantRow : this.row(session, { kind: 'assistant' });
      const timing = this.active.timings.get(row.id), end = performance.now();
      Object.assign(row, { text: text || message.errorMessage || '', status: message.stopReason === 'aborted' ? 'interrupted' : message.stopReason === 'error' ? 'failed' : 'completed',
        usage: message.usage, model: message.model || row.model, endedAt: Date.now(), stopReason: message.stopReason,
        toolCalls: message.content.filter(c => c.type === 'toolCall').map(c => ({ id: c.id, name: c.name, arguments: c.arguments })),
        raw: this.store.safe(message),
        ...(timing ? { durationMs: Math.max(0, end - timing.start), generationDurationMs: timing.first === undefined ? undefined : Math.max(0, end - timing.first) } : {}) });
      this.active.lastAssistantId = row.id;
      this.persist(session, row); this.assistantRow = null;
    }
    if (event.type === 'tool_execution_start') {
      const definition = pi.getToolDefinition(event.toolName);
      this.row(session, { kind: 'tool', callId: event.toolCallId, parentCallId: event.parentToolCallId,
        parentRecordId: event.parentToolCallId ? undefined : this.active.lastAssistantId || this.active.parentRecordId,
        name: event.toolName, args: this.store.safe(event.args), schema: this.store.safe(definition?.parameters),
        description: definition?.description, status: 'running', startedAt: Date.now() });
    }
    if (event.type === 'tool_execution_end' || event.type === 'tool_execution_update') {
      const row = session.records.findLast(r => r.callId === event.toolCallId && r.runId === runId);
      if (!row) return;
      row.result = this.store.safe(event.result || event.partialResult);
      if (event.type === 'tool_execution_end') {
        row.status = JSON.stringify(row.result).includes('结果待确认') ? 'unknown' : event.isError ? 'failed' : 'completed';
        row.durationMs = event.durationMs ?? Date.now() - row.startedAt;
        row.endedAt = Date.now(); row.timingSource = event.durationMs === undefined ? 'host' : 'pi';
        this.rememberTask(session, row.result);
      }
      this.persist(session, row, event.type === 'tool_execution_update');
    }
    if (event.type === 'auto_retry_start') this.retryRow = this.row(session, { kind: 'notice', text: `模型连接重试：${event.attempt || 1}`, status: 'running', startedAt: Date.now(), details: { attempt: event.attempt, error: event.errorMessage } });
    if (event.type === 'auto_retry_end' && this.retryRow) {
      Object.assign(this.retryRow, { status: event.success ? 'completed' : 'failed', endedAt: Date.now(), durationMs: Date.now() - this.retryRow.startedAt });
      this.persist(session, this.retryRow); this.retryRow = null;
    }
    if (event.type === 'compaction_start') this.compactionRow = this.row(session, { kind: 'context', text: '上下文压缩', status: 'running', startedAt: Date.now(), context: { reason: event.reason } });
    if (event.type === 'compaction_end' && this.compactionRow) {
      Object.assign(this.compactionRow, { status: event.aborted ? 'interrupted' : event.errorMessage ? 'failed' : 'completed', endedAt: Date.now(),
        durationMs: Date.now() - this.compactionRow.startedAt, context: this.store.safe({ ...this.compactionRow.context, result: event.result, error: event.errorMessage }) });
      this.persist(session, this.compactionRow); this.compactionRow = null;
    }
  }
  rememberTask(session, result) {
    let data = result?.details?.data || result?.details?.structuredContent || result?.structuredContent;
    // Pi can wrap the MCP reply inside another structuredContent envelope.
    for (let depth = 0; depth < 5 && data && !data.task_id && data.structuredContent; depth++) data = data.structuredContent;
    if (!data?.task_id) try { data = JSON.parse(result?.content?.find(c => c.type === 'text')?.text || 'null'); } catch { /* A text-only tool has no task object. */ }
    if (data?.task_id && data.status) { session.tasks ||= {}; session.tasks[data.task_id] = { ...session.tasks[data.task_id], ...data }; }
  }
  async execute(session, text, options) {
    const signal = this.active.controller.signal;
    if (options.workflowId) {
      if (scenarioFor(this.store.config, session.scenarioId || 'groundstation').flight) throw new Error('仿真飞行使用持久化流程与界面审批，不运行普通业务工作流');
      const flow = this.store.config.workflows.find(f => f.id === options.workflowId && f.enabled);
      if (!flow) throw new Error('工作流不存在或已禁用');
      const result = await runWorkflow(flow, options.input || {}, { execute: (step, stepSignal) => this.workflowStep(session, step, stepSignal) }, signal);
      signal.throwIfAborted(); this.row(session, { kind: 'assistant', text: typeof result === 'string' ? result : JSON.stringify(result, null, 2) }); return;
    }
    const pi = await this.makePi(session);
    signal.throwIfAborted();
    await pi.prompt(text);
    session.piFile = pi.sessionManager.getSessionFile();
  }
  async workflowStep(session, step, signal) {
    this.active.stepNumber++;
    const row = this.row(session, { kind: 'workflow', name: step.id, text: step.type, status: 'running', args: step, startedAt: Date.now() });
    const previousParent = this.active.parentRecordId; this.active.parentRecordId = row.id;
    try {
      signal.throwIfAborted();
      let result;
      if (step.type === 'knowledge') result = this.knowledge.search(step.query || '');
      if (step.type === 'skill') result = this.loadSkill(step.name);
      if (step.type === 'output') result = step.value;
      if (step.type === 'tool') {
        const match = /^mcp__(\w+)__(.+)$/.exec(step.tool);
        if (!match) throw new Error('工具名应为 mcp__服务名__工具名');
        const config = this.store.config.mcp.find(s => s.name === match[1] && s.enabled);
        if (!config) throw new Error('MCP 服务不存在或未启用');
        if (config.id === 'px4-simulation') throw new Error('仿真服务只通过专用流程和界面审批执行');
        const scenario = scenarioFor(this.store.config, session.scenarioId || 'groundstation');
        if (scenario.mcpIds !== null && !scenario.mcpIds.includes(config.id)) throw new Error('当前场景未授权该 MCP 服务');
        await this.mcp.sync();
        const tool = this.mcp.entry(config.id).tools.find(t => t.name === match[2]);
        if (!tool) throw new Error('MCP 工具不存在');
        const toolRow = this.row(session, { kind: 'tool', callId: uid(), parentCallId: row.id, name: step.tool, args: step.args || {}, schema: tool.inputSchema, status: 'running', startedAt: Date.now() });
        try {
          const response = await this.mcp.call(config.id, match[2], step.args || {}, { signal, timeout: (step.timeoutSeconds || 120) * 1000 });
          toolRow.result = this.store.safe(response); toolRow.durationMs = Date.now() - toolRow.startedAt;
          toolRow.status = JSON.stringify(response).includes('结果待确认') ? 'unknown' : response.isError ? 'failed' : 'completed'; this.persist(session, toolRow);
          if (response.isError) throw new Error(response.content?.map(c => c.text || '').join('\n') || '工具调用失败');
          this.rememberTask(session, response);
          result = response.structuredContent || JSON.parse(response.content.find(c => c.type === 'text').text);
        } catch (error) {
          if (toolRow.status === 'running') { toolRow.status = 'unknown'; toolRow.result = error.message; this.persist(session, toolRow); }
          throw error;
        }
      }
      if (step.type === 'agent') {
        const pi = this.active.pi || await this.makePi(session);
        const tools = pi.getActiveToolNames();
        pi.setActiveToolsByName(['knowledge_search', 'load_skill']);
        const onAbort = () => { void pi.abort(); };
        signal.addEventListener('abort', onAbort, { once: true });
        try { signal.throwIfAborted(); await pi.prompt(step.prompt); result = pi.getLastAssistantText(); }
        finally { signal.removeEventListener('abort', onAbort); pi.setActiveToolsByName(tools); session.piFile = pi.sessionManager.getSessionFile(); }
      }
      signal.throwIfAborted(); Object.assign(row, { status: 'completed', result: this.store.safe(result), durationMs: Date.now() - row.startedAt }); this.persist(session, row); return result;
    } catch (error) { Object.assign(row, { status: signal.aborted ? 'interrupted' : 'failed', result: this.store.safe(error.message), durationMs: Date.now() - row.startedAt }); this.persist(session, row); throw error; }
    finally { this.active.parentRecordId = previousParent; }
  }
}
