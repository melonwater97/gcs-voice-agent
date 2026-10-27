import fs from 'node:fs';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createHmac } from 'node:crypto';

export function flightFixture(store) {
  store.config.activeScenarioId = 'px4-simulation';
  store.config.scenarios.find(s => s.flight).flight.limits = { maxAltitudeM: 100, maxSpeedMS: 10, maxDistanceM: 1000, maxDurationS: 600 };
  const home = { latitude: 47.397971, longitude: 8.546164, altitudeAmslM: 488 };
  const state = { status: { vehicleId: 'SITL:fixture', simulationVerified: true, armed: false, landed: true, observedAt: Date.now(), position: home },
    snapshot: { connected: true, revision: 1, digest: 'fixture-plan-one', selectedPosition: { ...home }, plan: { fileType: 'Plan', mission: { hoverSpeed: 5, items: [
      { type: 'SimpleItem', command: 22, frame: 3, autoContinue: true, params: [0, 0, 0, null, home.latitude, home.longitude, 12] },
      { type: 'SimpleItem', command: 16, frame: 3, autoContinue: true, params: [0, 0, 0, null, home.latitude + .0003, home.longitude, 12] },
    ] } } }, writes: [], execution: { status: 'running', alerts: [] } };
  const backend = async (name, args) => {
    if (name === 'query_status') return { ...state.status, observedAt: Date.now() };
    if (name === 'qgc_snapshot') return structuredClone(state.snapshot);
    if (name === 'execution_status') return state.execution;
    if (name === 'execute_authorized_action') {
      const [body, signature] = args.grant.split('.');
      assert.equal(signature, createHmac('sha256', fs.readFileSync(path.join(store.dir, 'flights/authority.key'))).update(body).digest('hex'));
      const grant = JSON.parse(Buffer.from(body, 'base64url'));
      state.writes.push(grant);
      return { status: 'completed', action: grant.action, verified: true, executionId: 'fixture-execution', vehicle: { ...state.status, observedAt: Date.now() } };
    }
    throw new Error(`Unexpected fixture call: ${name}`);
  };
  return { state, backend };
}
