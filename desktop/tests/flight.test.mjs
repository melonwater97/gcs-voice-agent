import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash, createHmac } from 'node:crypto';
import { Store } from '../server/store.mjs';
import { FlightCoordinator } from '../server/flight-coordinator.mjs';
import { inspectPlan } from '../server/flight-plan.mjs';
import { McpRuntime } from '../server/mcp-runtime.mjs';
import { flightFixture } from './helpers/flight-fixture.mjs';
import { generateFlightReport } from '../server/flight-report.mjs';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';


function setup(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-flight-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new Store(dir), fixture = flightFixture(store), coordinator = new FlightCoordinator(store, null, () => {}, { backend: fixture.backend });
  const session = store.newSession(), flight = coordinator.create(session);
  return { store, coordinator, flight, ...fixture };
}

test('voice/model requests never execute; separate upload and start UI approvals bind exact content', async t => {
  const { coordinator, flight, state } = setup(t);
  const requested = await coordinator.propose(flight.id, 'upload'), upload = requested.approvals.at(-1);
  assert.equal(state.writes.length, 0);
  await assert.rejects(coordinator.decide(flight.id, upload.id, 'approve'), /界面按钮/);
  assert.equal(state.writes.length, 0);
  const uploaded = await coordinator.decide(flight.id, upload.id, 'approve', { human: true });
  assert.equal(uploaded.phase, 'uploaded'); assert.equal(state.writes.length, 1);
  await assert.rejects(coordinator.decide(flight.id, upload.id, 'approve', { human: true }), /已处理/);
  const next = await coordinator.propose(flight.id, 'start');
  assert.equal(state.writes.length, 1);
  const started = await coordinator.decide(flight.id, next.approvals.at(-1).id, 'approve', { human: true });
  assert.equal(started.phase, 'monitoring'); assert.equal(state.writes.length, 2);
  assert.equal(state.writes[1].completion, 'return_and_land');
});

test('modified draft, changed limits, expired approval, and an armed vehicle all block execution', async t => {
  for (const mutation of ['draft', 'limits', 'expiry', 'armed']) {
    await t.test(mutation, async sub => {
      const { coordinator, flight, store, state } = setup(sub);
      const requested = await coordinator.propose(flight.id, 'upload'), approval = requested.approvals.at(-1);
      if (mutation === 'draft') state.snapshot.plan.mission.items[0].params[6] = 13; // Even if a buggy bridge kept the digest unchanged.
      if (mutation === 'limits') store.config.scenarios.find(s => s.flight).flight.limits.maxAltitudeM = 80;
      if (mutation === 'expiry') { coordinator.now = () => approval.expiresAt + 1; }
      if (mutation === 'armed') state.status.armed = true;
      await assert.rejects(coordinator.decide(flight.id, approval.id, 'approve', { human: true }));
      assert.equal(state.writes.length, 0);
    });
  }
});

test('rejection, missing limits, unverified simulation, and upload readback mismatch never authorize start', async t => {
  const { coordinator, flight, store, state } = setup(t);
  let requested = await coordinator.propose(flight.id, 'upload');
  await coordinator.decide(flight.id, requested.approvals.at(-1).id, 'reject', { human: true });
  assert.equal(state.writes.length, 0);
  await assert.rejects(coordinator.propose(flight.id, 'start'), /尚未上传/);
  store.config.scenarios.find(s => s.flight).flight.limits.maxSpeedMS = null;
  // A fresh draft must be reviewed under the newly configured limits.
  state.snapshot.revision++;
  await assert.rejects(coordinator.propose(flight.id, 'upload'), /基础审查/);
  state.status.simulationVerified = false;
  await assert.rejects(coordinator.propose(flight.id, 'origin', { position: state.snapshot.selectedPosition }), /身份/);
});

test('application recovery marks in-progress writes unknown and never replays them', async t => {
  const { coordinator, flight, store, state } = setup(t);
  const saved = coordinator.get(flight.id);
  saved.phase = 'starting'; saved.approvals.push({ id: 'fixture-approval', status: 'executing' }); coordinator.save(saved);
  const recovered = new FlightCoordinator(store, null, () => {}, { backend: coordinator.backend });
  assert.equal(recovered.get(flight.id).phase, 'unknown');
  assert.equal(recovered.get(flight.id).approvals[0].status, 'unknown');
  assert.equal(state.writes.length, 0);
});

test('flight anomalies request intervention without issuing a new control action', async t => {
  const { coordinator, flight, state } = setup(t);
  const saved = coordinator.get(flight.id); saved.phase = 'monitoring'; saved.status = state.status; coordinator.save(saved);
  state.execution = { status: 'failed', alerts: ['telemetry stale'] };
  const polled = await coordinator.poll(flight.id);
  assert.equal(polled.phase, 'intervention_required'); assert.equal(state.writes.length, 0);
});

test('flight execution endpoint is neither exposed to Pi nor callable by ordinary MCP/workflows', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-flight-policy-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new Store(dir);
  store.config.mcp = [{ id: 'flight', name: 'flight', enabled: true, transport: 'stdio', command: 'unused', args: [] }];
  const mcp = new McpRuntime(store, () => {}, { connect: async () => ({ tools: [{ name: 'execute_authorized_action', inputSchema: { type: 'object' } }, { name: 'query_status', inputSchema: { type: 'object' } }], close: async () => {} }) });
  t.after(() => mcp.close());
  assert.deepEqual((await mcp.piTools()).map(tool => tool.name), ['mcp__flight__query_status']);
  await assert.rejects(mcp.call('flight', 'execute_authorized_action', {}), /界面审批/);
  assert.deepEqual(await mcp.piTools([]), []);
});

test('basic review rejects implicit altitude and unsupported mission items and reports its coverage', async t => {
  const { store, state } = setup(t), limits = store.config.scenarios.find(s => s.flight).flight.limits;
  const review = inspectPlan(state.snapshot.plan, limits, state.status.position);
  assert.equal(review.passed, true); assert.equal(review.summary.obstacleTerrainVerified, false);
  const modified = structuredClone(state.snapshot.plan); modified.mission.items[0].frame = 0;
  assert.equal(inspectPlan(modified, limits, state.status.position).passed, false);
});

test('upload without matching readback becomes unknown and cannot authorize start', async t => {
  const { coordinator, flight, state } = setup(t), original = coordinator.backend;
  coordinator.backend = async (name, args) => name === 'execute_authorized_action' ? { status: 'completed', action: 'upload', verified: false } : original(name, args);
  const pending = await coordinator.propose(flight.id, 'upload');
  await assert.rejects(coordinator.decide(flight.id, pending.approvals.at(-1).id, 'approve', { human: true }), /读回/);
  assert.equal(coordinator.get(flight.id).phase, 'unknown');
  await assert.rejects(coordinator.propose(flight.id, 'start'));
});

test('voice cancellation rejects only pending approvals without stopping or commanding the vehicle', async t => {
  const { coordinator, flight, state } = setup(t);
  await coordinator.propose(flight.id, 'upload');
  const cancelled = await coordinator.cancelPending(flight.id);
  assert.equal(cancelled.approvals.at(-1).status, 'rejected'); assert.equal(state.writes.length, 0);
  const saved = coordinator.get(flight.id); saved.phase = 'monitoring'; coordinator.save(saved);
  const result = await coordinator.cancelPending(flight.id);
  assert.equal(result.cancelled, false); assert.equal(coordinator.get(flight.id).phase, 'monitoring'); assert.equal(state.writes.length, 0);
});

test('QGC disconnected still returns real simulation status but cannot create an approval', async t => {
  const { coordinator, flight, state } = setup(t), original = coordinator.backend;
  coordinator.backend = async (name, args) => { if (name === 'qgc_snapshot') throw new Error('bridge unavailable'); return original(name, args); };
  const synced = await coordinator.sync(flight.id);
  assert.equal(synced.status.vehicleId, state.status.vehicleId); assert.equal(synced.bridgeConnected, false);
  await assert.rejects(coordinator.propose(flight.id, 'upload'), /尚未同步/); assert.equal(state.writes.length, 0);
});

test('completed flight records are immutable to planning actions; the next run has a new identity', async t => {
  const { coordinator, flight, store, state } = setup(t), saved = coordinator.get(flight.id);
  saved.phase = 'completed'; coordinator.save(saved);
  assert.equal((await coordinator.sync(flight.id)).phase, 'completed');
  await assert.rejects(coordinator.propose(flight.id, 'upload'), /已结束/);
  await assert.rejects(coordinator.preview(flight.id, state.snapshot.plan, 1), /已结束/);
  const next = coordinator.create(store.getSession(flight.sessionId), { newRun: true });
  assert.notEqual(next.id, flight.id); assert.equal(coordinator.forSession(flight.sessionId).id, next.id);
  assert.equal(coordinator.get(flight.id).phase, 'completed');
});

const pandoc = fileURLToPath(new URL('../.data/tools/pandoc/pandoc.exe', import.meta.url));
test('real Pandoc produces DOCX from verified per-flight data; report retries never replay flight', { skip: !fs.existsSync(pandoc), timeout: 30000 }, async t => {
  const { coordinator, flight, state, store } = setup(t), original = coordinator.backend;
  const log = Buffer.from(JSON.stringify({ label: 'isolated-test-fixture', samples: [] }));
  const artifact = { id: 'telemetry', name: 'telemetry.json', size: log.length, sha256: createHash('sha256').update(log).digest('hex') };
  coordinator.backend = async (name, args) => {
    if (name === 'collect_flight_logs') return { flightId: flight.id, vehicleId: state.status.vehicleId, metrics: { source: 'isolated-test-fixture', durationSeconds: 10, sampleCount: 0 }, missing: ['fixture 没有实际飞行数据'], artifacts: [artifact] };
    if (name === 'read_log_chunk') return { offset: args.offset, data: log.toString('base64'), length: log.length };
    return original(name, args);
  };
  const saved = coordinator.get(flight.id); saved.phase = 'landed'; saved.status = state.status; saved.review = inspectPlan(state.snapshot.plan, store.config.scenarios.find(s => s.flight).flight.limits, state.status.position); saved.draft = state.snapshot; coordinator.save(saved);
  store.config.general.pandocPath = pandoc;
  const result = await generateFlightReport(coordinator, flight.id);
  assert.equal(result.phase, 'completed'); assert.equal(state.writes.length, 0);
  assert.equal(fs.readFileSync(result.report.docx).subarray(0, 2).toString(), 'PK');
  const xml = execFileSync(process.env.LH_TEST_PYTHON || 'python', ['-c', "import sys,zipfile,xml.etree.ElementTree as E; root=E.fromstring(zipfile.ZipFile(sys.argv[1]).read('word/document.xml')); print(''.join(n.text or '' for n in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t')))", result.report.docx], { encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } });
  assert.match(xml, /10.00/); assert.match(xml, /未验证障碍物与地形/); assert.match(xml, /fixture 没有实际飞行数据/);
  store.config.general.pandocPath = path.join(store.dir, 'missing-pandoc');
  await assert.rejects(generateFlightReport(coordinator, flight.id));
  assert.equal(state.writes.length, 0); assert.equal(coordinator.get(flight.id).phase, 'landed');
});
