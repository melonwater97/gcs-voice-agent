import fs from 'node:fs';
import path from 'node:path';
import { createHmac, randomBytes } from 'node:crypto';
import { atomicJSON, readJSON, uid } from './store.mjs';
import { scenarioFor } from './scenarios.mjs';
import { flightAuthority } from './flight-policy.mjs';
import { digest, inspectPlan, validPosition, distanceM } from './flight-plan.mjs';

const inFlight = new Set(['starting', 'monitoring', 'intervention_required', 'unknown']);
const awaiting = new Set(['pending']);
const endedPhases = new Set(['landed', 'completed', 'collecting', 'reporting']);
export class FlightCoordinator {
  constructor(store, mcp, notify = () => {}, { backend, now = Date.now } = {}) {
    this.store = store; this.mcp = mcp; this.notify = notify; this.backend = backend; this.now = now; this.locks = new Set();
    this.dir = path.join(store.dir, 'flights'); fs.mkdirSync(this.dir, { recursive: true });
    const keyFile = path.join(this.dir, 'authority.key');
    if (!fs.existsSync(keyFile)) fs.writeFileSync(keyFile, randomBytes(32), { mode: 0o600 });
    this.key = fs.readFileSync(keyFile);
    for (const flight of this.list()) {
      let changed = false;
      for (const approval of flight.approvals) if (approval.status === 'executing') { approval.status = 'unknown'; changed = true; }
      if (['uploading', 'starting', 'adjusting_origin'].includes(flight.phase)) { flight.phase = 'unknown'; changed = true; }
      if (['collecting', 'reporting'].includes(flight.phase)) { flight.phase = 'landed'; flight.notices.push('报告生成曾中断，可单独重试。'); changed = true; }
      if (changed) { flight.notices.push('应用重启，未完成的写操作需要核对实际状态，不会自动重放。'); this.save(flight); }
    }
  }
  file(id) { if (!/^[a-f0-9-]{36}$/.test(id)) throw new Error('飞行编号无效'); return path.join(this.dir, `${id}.json`); }
  list() { return fs.readdirSync(this.dir).filter(name => /^[a-f0-9-]{36}\.json$/.test(name)).map(name => readJSON(path.join(this.dir, name), null)); }
  get(id) { const flight = readJSON(this.file(id), null); if (!flight) throw new Error('飞行记录不存在'); return flight; }
  forSession(sessionId) { return this.list().filter(flight => flight.sessionId === sessionId).sort((a, b) => b.createdAt - a.createdAt)[0] || null; }
  active() { return this.list().filter(flight => ['uploading', 'adjusting_origin', 'starting', 'monitoring', 'intervention_required', 'unknown'].includes(flight.phase)); }
  create(session, { newRun = false } = {}) {
    const scenario = scenarioFor(this.store.config, session.scenarioId);
    if (!scenario.flight) throw new Error('当前会话不是仿真飞行场景');
    const existing = this.forSession(session.id);
    if (existing && !newRun) return existing;
    if (existing && !['landed', 'completed'].includes(existing.phase)) throw new Error('本轮尚未结束，请先处理或取消当前流程');
    if (this.active().length) throw new Error('已有未结束或待核对的仿真，请先处理该流程');
    const flight = { id: uid(), sessionId: session.id, scenarioId: scenario.id, phase: 'new', createdAt: Math.max(this.now(), (existing?.createdAt || 0) + 1),
      revision: 0, approvals: [], events: [], notices: [], status: null, draft: null, review: null };
    this.save(flight); return flight;
  }
  save(flight) { flight.updatedAt = this.now(); atomicJSON(this.file(flight.id), flight); this.notify({ type: 'flight', sessionId: flight.sessionId, flight: this.store.safe(flight) }); }
  event(flight, type, detail) { flight.events.push({ at: this.now(), type, detail: this.store.safe(detail) }); }
  profile(flight) { return scenarioFor(this.store.config, flight.scenarioId); }
  async call(flight, name, args = {}, options = {}) {
    if (this.backend) return this.backend(name, args, options);
    const id = this.profile(flight).flight.serviceId;
    const result = await this.mcp.call(id, name, args, options);
    if (result.isError) throw new Error(result.content?.map(item => item.text || '').join('\n') || '仿真工具失败');
    return result.structuredContent || JSON.parse(result.content.find(item => item.type === 'text').text);
  }
  invalidate(flight, reason) {
    for (const approval of flight.approvals) if (awaiting.has(approval.status)) { approval.status = 'invalidated'; approval.reason = reason; }
  }
  validateStatus(status, { ground = false } = {}) {
    if (!status || status.simulationVerified !== true || !status.vehicleId || !Number.isFinite(status.observedAt) || Math.abs(this.now() - status.observedAt) > 5000) throw new Error('仿真身份或遥测未核验、已过期，禁止执行');
    if (!validPosition(status.position)) throw new Error('缺少有效的飞机实际位置');
    if (ground && (status.armed !== false || status.landed !== true)) throw new Error('操作要求飞机已落地且未解锁');
  }
  async exclusive(id, operation) {
    if (this.locks.has(id)) throw new Error('本次飞行已有操作正在执行');
    this.locks.add(id);
    try { return await operation(); } finally { this.locks.delete(id); }
  }
  async sync(id) { return this.exclusive(id, () => this.synchronize(id)); }
  async synchronize(id) {
    const flight = this.get(id);
    if (endedPhases.has(flight.phase)) return flight;
    const status = await this.call(flight, 'query_status');
    if (flight.status?.vehicleId && flight.status.vehicleId !== status.vehicleId) this.invalidate(flight, '目标飞行器发生变化');
    flight.status = status;
    let snapshot;
    try {
      snapshot = await this.call(flight, 'qgc_snapshot');
      if (snapshot.connected !== true || !snapshot.plan || typeof snapshot.digest !== 'string' || !Number.isInteger(snapshot.revision)) throw new Error('QGC 草稿数据无效');
      flight.bridgeConnected = true;
    } catch (error) {
      flight.bridgeConnected = false; flight.review = null; this.invalidate(flight, 'QGC 数据桥断开');
      flight.phase = inFlight.has(flight.phase) ? 'intervention_required' : 'planning';
      const notice = `已读取仿真状态，但 QGC 草稿数据桥未就绪：${error.message}`;
      if (flight.notices.at(-1) !== notice) flight.notices.push(notice);
      this.save(flight); return flight;
    }
    if (!flight.draft || flight.draft.digest !== snapshot.digest || flight.draft.revision !== snapshot.revision) {
      this.invalidate(flight, 'QGC 航线已修改，需要重新审查和批准');
      flight.draft = snapshot; flight.revision++; flight.review = null;
      if (!inFlight.has(flight.phase)) { flight.phase = 'planning'; flight.uploaded = null; }
      else { flight.phase = 'intervention_required'; flight.notices.push('飞行期间 QGC 草稿发生变化；未发送新的控制指令。'); }
    }
    if (!inFlight.has(flight.phase)) {
      flight.review = inspectPlan(snapshot.plan, this.profile(flight).flight.limits, status.position);
      if (!flight.phase.startsWith('awaiting_') && flight.phase !== 'uploaded') flight.phase = flight.review.passed ? 'reviewed' : 'planning';
    }
    this.event(flight, 'synchronized', { vehicleId: status.vehicleId, digest: snapshot.digest, revision: snapshot.revision }); this.save(flight); return flight;
  }
  async preview(id, plan, expectedRevision) {
    return this.exclusive(id, async () => {
    const flight = this.get(id); if (endedPhases.has(flight.phase)) throw new Error('历史飞行已结束，请先创建新一轮');
    this.validateStatus(await this.call(flight, 'query_status'), { ground: true });
    if (inFlight.has(flight.phase)) throw new Error('飞行状态下不允许助手修改草稿');
    this.invalidate(flight, '用户请求修改航线'); flight.review = null; flight.uploaded = null; flight.phase = 'planning'; this.save(flight);
    await this.call(flight, 'preview_mission', { plan, expected_revision: expectedRevision });
    return this.synchronize(id);
    });
  }
  async edit(id, expectedRevision, edits, speed) {
    const flight = this.get(id), snapshot = await this.call(flight, 'qgc_snapshot');
    if (snapshot.revision !== expectedRevision) throw new Error('QGC 草稿已更新，请重新读取后修改');
    const plan = structuredClone(snapshot.plan);
    if (!Array.isArray(edits) || edits.length > 100) throw new Error('航点修改列表无效');
    for (const edit of edits) {
      const matches = plan.mission.items.filter(item => item.doJumpId === edit.itemId && item.type === 'SimpleItem');
      if (matches.length !== 1) throw new Error('航点编号不存在或不唯一，请使用 QGC 实际 doJumpId');
      const item = matches[0];
      if (edit.altitudeM !== undefined) { if (!Number.isFinite(edit.altitudeM) || edit.altitudeM <= 0) throw new Error('高度无效'); item.params[6] = edit.altitudeM; item.Altitude = edit.altitudeM; }
      if (edit.latitude !== undefined || edit.longitude !== undefined) {
        if (!validPosition(edit)) throw new Error('修改位置必须同时提供有效经纬度');
        item.params[4] = edit.latitude; item.params[5] = edit.longitude;
      }
    }
    if (speed !== undefined) { if (!Number.isFinite(speed) || speed <= 0) throw new Error('速度无效'); plan.mission.hoverSpeed = speed; }
    return this.preview(id, plan, expectedRevision);
  }
  async cancelPending(id) {
    return this.exclusive(id, async () => {
      const flight = this.get(id), pending = flight.approvals.filter(approval => approval.status === 'pending');
      if (!pending.length) return { cancelled: false, phase: flight.phase, message: '没有待批准请求；本操作不会停止飞行，飞行中需要在 QGC 人工接管。' };
      for (const approval of pending) approval.status = 'rejected';
      flight.phase = flight.uploaded ? 'uploaded' : 'planning'; this.event(flight, 'pending_cancelled', { count: pending.length }); this.save(flight); return flight;
    });
  }
  async propose(id, action, payload = {}) {
    if (!['origin', 'upload', 'start'].includes(action)) throw new Error('不支持的审批动作');
    if (endedPhases.has(this.get(id).phase)) throw new Error('本轮飞行已结束，请创建新一轮，不复用旧飞行编号');
    if (inFlight.has(this.get(id).phase)) throw new Error('本轮正在执行或等待核对，不能追加新的执行请求');
    return this.exclusive(id, async () => {
    let flight = await this.synchronize(id); this.validateStatus(flight.status, { ground: true });
    if (!flight.bridgeConnected || !flight.draft) throw new Error('QGC 选点与草稿尚未同步，不能创建执行审批');
    if (action === 'origin' && !validPosition(payload.position)) throw new Error('必须指定 QGC 所选的实际起飞位置');
    if (action === 'origin' && digest(payload.position) !== digest(flight.draft.selectedPosition)) throw new Error('位置与 QGC 当前选点不一致，请重新同步');
    if (action !== 'origin' && !flight.review?.passed) throw new Error('基础审查尚未通过，请处理检查结果并配置限值');
    if (action === 'start' && (!flight.uploaded || flight.uploaded.digest !== flight.draft.digest || flight.uploaded.verified !== true)) throw new Error('当前航线尚未上传并读回核对');
    this.invalidate(flight, '已创建新的审批请求');
    const approval = { id: uid(), action, status: 'pending', createdAt: this.now(), expiresAt: this.now() + this.profile(flight).flight.approvalSeconds * 1000,
      revision: flight.revision, qgcRevision: flight.draft.revision, planDigest: flight.draft.digest, planHash: digest(flight.draft.plan),
      configHash: digest(this.profile(flight).flight), vehicleId: flight.status.vehicleId, positionBefore: flight.status.position,
      payload: action === 'origin' ? { position: payload.position } : {}, summary: flight.review?.summary || null };
    flight.approvals.push(approval); flight.phase = `awaiting_${action}`;
    this.event(flight, 'approval_requested', { id: approval.id, action }); this.save(flight); return flight;
    });
  }
  async decide(id, approvalId, decision, { human = false } = {}) {
    if (!human) throw new Error('只有界面按钮可以批准或拒绝');
    if (!['approve', 'reject'].includes(decision)) throw new Error('无效的审批决定');
    if (this.locks.has(id)) throw new Error('当前审批正在执行，请勿重复提交');
    this.locks.add(id);
    try {
      let flight = this.get(id), approval = flight.approvals.find(item => item.id === approvalId);
      if (!approval || approval.status !== 'pending') throw new Error('审批不存在、已处理或已失效');
      if (decision === 'reject') { approval.status = 'rejected'; flight.phase = flight.uploaded ? 'uploaded' : 'planning'; this.event(flight, 'rejected', approvalId); this.save(flight); return flight; }
      if (approval.expiresAt <= this.now()) { approval.status = 'expired'; this.save(flight); throw new Error('审批已过期，请重新请求'); }
      const status = await this.call(flight, 'query_status'), snapshot = await this.call(flight, 'qgc_snapshot');
      this.validateStatus(status, { ground: true });
      if (status.vehicleId !== approval.vehicleId || snapshot.connected !== true || snapshot.digest !== approval.planDigest || snapshot.revision !== approval.qgcRevision
          || digest(snapshot.plan) !== approval.planHash || digest(this.profile(flight).flight) !== approval.configHash || approval.expiresAt <= this.now()
          || distanceM(status.position, approval.positionBefore) > 2) {
        approval.status = 'invalidated'; approval.reason = '目标、航线、配置或有效期变化'; this.save(flight); throw new Error('审批内容已变化，请重新同步和确认');
      }
      if (approval.action === 'origin' && digest(snapshot.selectedPosition) !== digest(approval.payload.position)) throw new Error('QGC 选点已变化，需要重新确认');
      const review = inspectPlan(snapshot.plan, this.profile(flight).flight.limits, status.position);
      if (approval.action !== 'origin' && !review.passed) throw new Error('执行前基础审查未通过');
      if (approval.action === 'start' && (!flight.uploaded?.verified || flight.uploaded.digest !== approval.planDigest)) throw new Error('缺少一致的上传回执');
      approval.status = 'executing'; approval.approvedAt = this.now(); flight.phase = { origin: 'adjusting_origin', upload: 'uploading', start: 'starting' }[approval.action];
      this.event(flight, 'approved', { id: approval.id, action: approval.action }); this.save(flight);
      const grant = { nonce: approval.id, flightId: id, action: approval.action, vehicleId: approval.vehicleId,
        planDigest: approval.planDigest, qgcRevision: approval.qgcRevision, payload: approval.payload, expiresAt: approval.expiresAt,
        completion: 'return_and_land', limits: this.profile(flight).flight.limits, origin: approval.positionBefore };
      const body = Buffer.from(JSON.stringify(grant)).toString('base64url'), signature = createHmac('sha256', this.key).update(body).digest('hex');
      try {
        const result = await this.call(flight, 'execute_authorized_action', { grant: `${body}.${signature}` }, { authority: flightAuthority, timeout: 120000 });
        if (result.status !== 'completed' || result.action !== approval.action) throw new Error('未收到匹配的完整执行回执');
        if (approval.action === 'upload' && result.verified !== true) throw new Error('上传后读回核对未通过');
        approval.status = 'completed'; approval.result = result;
        flight.status = result.vehicle || status;
        if (approval.action === 'origin') { flight.phase = 'planning'; flight.review = null; flight.uploaded = null; flight.draft = null; this.invalidate(flight, '实际起飞位置已改变'); }
        if (approval.action === 'upload') { flight.uploaded = { digest: approval.planDigest, verified: true, at: this.now(), vehicleId: approval.vehicleId }; flight.phase = 'uploaded'; }
        if (approval.action === 'start') { flight.phase = 'monitoring'; flight.startedAt = this.now(); flight.executionId = result.executionId; }
        this.event(flight, 'executed', { action: approval.action, result }); this.save(flight); return flight;
      } catch (error) {
        approval.status = 'unknown'; flight.phase = 'unknown'; flight.notices.push(`结果待确认：${error.message}。先核对真实状态，不自动重放。`); this.save(flight); throw error;
      }
    } finally { this.locks.delete(id); }
  }
  async poll(id) {
    const flight = this.get(id); if (!['monitoring', 'intervention_required', 'unknown'].includes(flight.phase)) return flight;
    if (this.locks.has(id)) return flight;
    this.locks.add(id);
    try {
      const status = await this.call(flight, 'query_status'); this.validateStatus(status);
      if (status.vehicleId !== flight.status?.vehicleId) throw new Error('目标飞行器身份发生变化');
      flight.status = status;
      const execution = await this.call(flight, 'execution_status', { flight_id: flight.id });
      if (execution.alerts?.length || execution.status === 'failed') { flight.phase = 'intervention_required'; flight.notices.push(...(execution.alerts || ['仿真执行出现异常，请人工接管'])); }
      if (execution.status === 'completed' && status.landed === true && status.armed === false) { flight.phase = 'landed'; flight.endedAt = this.now(); this.event(flight, 'landed', execution); }
      if (execution.status === 'failed' && flight.startedAt && status.landed === true && status.armed === false) {
        flight.phase = 'landed'; flight.outcome = 'interrupted'; flight.endedAt = this.now(); this.event(flight, 'landed_after_intervention', execution);
      }
    } catch (error) { flight.phase = 'intervention_required'; if (flight.notices.at(-1) !== error.message) flight.notices.push(error.message); }
    finally { this.locks.delete(id); }
    this.save(flight); return flight;
  }
  async reconcile(id) {
    return this.exclusive(id, async () => {
      const flight = this.get(id), status = await this.call(flight, 'query_status');
      if (!['unknown', 'intervention_required'].includes(flight.phase)) throw new Error('只有异常或未知状态需要人工核对恢复');
      this.validateStatus(status, { ground: true });
      const execution = await this.call(flight, 'execution_status', { flight_id: id });
      flight.status = status; this.invalidate(flight, '界面核对后重新规划');
      if (execution.startedAt || flight.startedAt) { flight.startedAt ||= execution.startedAt; flight.endedAt = this.now(); flight.phase = 'landed'; flight.outcome = 'interrupted'; }
      else { flight.phase = 'planning'; flight.review = null; flight.uploaded = null; }
      this.event(flight, 'manually_reconciled', { landed: true, armed: false, outcome: flight.outcome }); this.save(flight); return flight;
    });
  }
  tools(session) {
    if (!scenarioFor(this.store.config, session.scenarioId || 'groundstation').flight) return [];
    const text = data => ({ content: [{ type: 'text', text: JSON.stringify(data) }], details: { data } });
    const empty = { type: 'object', properties: {}, additionalProperties: false };
    const get = () => this.create(session);
    return [
      { name: 'flight_get_state', label: '查看仿真流程', description: '读取本次仿真流程和待处理审批，不批准任何操作。', parameters: empty, execute: async () => text(get()) },
      { name: 'flight_new_run', label: '创建新一轮仿真', description: '上一轮已落地结束后创建新的飞行编号，保留旧日志和报告；不会发送飞行命令。', parameters: empty, execute: async () => text(this.create(session, { newRun: true })) },
      { name: 'flight_sync', label: '同步仿真与航线', description: '查询真实仿真状态、QGC选点及未上传草稿，执行基础航线审查。', parameters: empty, execute: async () => text(await this.sync(get().id)) },
      { name: 'flight_request_action', label: '请求界面批准', description: '请求调整实际起飞位置、上传、或解锁并启动任务。仅创建待批准记录；用户必须点击界面按钮才能执行。',
        parameters: { type: 'object', required: ['action'], properties: { action: { type: 'string', enum: ['origin', 'upload', 'start'] } }, additionalProperties: false },
        execute: async (_id, args) => { const flight = get(); const synced = await this.sync(flight.id); return text(await this.propose(flight.id, args.action, args.action === 'origin' ? { position: synced.draft.selectedPosition } : {})); } },
      { name: 'flight_generate_report', label: '生成飞行报告', description: '已核对落地后收集本次日志并生成 Markdown 和 DOCX；失败可单独重试，不重新飞行。', parameters: empty,
        execute: async () => text(await this.generateReport(get().id)) },
      { name: 'flight_cancel_request', label: '取消待批准请求', description: '拒绝尚未批准的请求。不能撤销已发送操作，不会暂停、返航或降落飞行器。', parameters: empty,
        execute: async () => text(await this.cancelPending(get().id)) },
      { name: 'flight_edit_waypoints', label: '修改 QGC 航线草稿', description: '按当前 QGC doJumpId 修改明确的航点坐标/相对高度或计划速度，使用真实草稿 revision 防止覆盖人工编辑；修改后重新审查和批准，不上传。',
        parameters: { type: 'object', required: ['expectedRevision', 'edits'], additionalProperties: false, properties: {
          expectedRevision: { type: 'integer' }, speedMS: { type: 'number', exclusiveMinimum: 0 }, edits: { type: 'array', maxItems: 100,
            items: { type: 'object', required: ['itemId'], additionalProperties: false, properties: { itemId: { type: 'integer' }, altitudeM: { type: 'number', exclusiveMinimum: 0 }, latitude: { type: 'number', minimum: -90, maximum: 90 }, longitude: { type: 'number', minimum: -180, maximum: 180 } } } } } },
        execute: async (_id, args) => text(await this.edit(get().id, args.expectedRevision, args.edits, args.speedMS)) },
    ];
  }
}
