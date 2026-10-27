import React, { useState } from 'react';
import { request } from './api';

export default function ScenarioSettings({ draft, setDraft, pending, busy, action, refresh }) {
  const [id, setId] = useState(draft.activeScenarioId);
  const scenario = draft.scenarios.find(item => item.id === id) || draft.scenarios[0];
  const update = patch => setDraft(old => ({ ...old, scenarios: old.scenarios.map(item => item.id === scenario.id ? { ...item, ...patch } : item) }));
  const limitNames = { maxAltitudeM: '最大相对高度（米）', maxSpeedMS: '最大速度（米/秒）', maxDistanceM: '距起飞点最远距离（米）', maxDurationS: '最大任务时长（秒）' };
  return <>
    <label className="field"><span>编辑场景</span><select aria-label="编辑场景" value={scenario.id} onChange={event => setId(event.target.value)}>{draft.scenarios.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label className="field"><span>场景名称</span><input value={scenario.name} onChange={event => update({ name: event.target.value })}/></label>
    <label className="field"><span>系统提示词</span><textarea className="json-editor" style={{ minHeight: 240 }} aria-label="场景系统提示词" value={scenario.systemPrompt} onChange={event => update({ systemPrompt: event.target.value })}/></label>
    <p className="muted">提示词负责灵活引导，执行审批与仿真限制由程序检查，提示词不能覆盖。切换场景从对话工具栏操作，自动创建独立会话。</p>
    {scenario.flight ? <>
      <div className="setting-card"><strong>仿真专用工具</strong><p>仅提供仿真流程工具，屏蔽普通 MCP 和任意执行能力。具体 MAVLink 写操作由界面审批授权，模型不能调用授权执行入口。</p>
        <button className="secondary" disabled={pending || busy} onClick={() => action(async () => { await request('/api/flight/setup', 'POST', {}); const value = await refresh(); setDraft(old => ({ ...old, mcp: value.config.mcp })); })}>配置本机 WSL 仿真服务</button>
      </div>
      <div className="field-grid">{Object.entries(limitNames).map(([key, label]) => <label className="field" key={key}><span>{label}</span><input aria-label={label} type="number" min="0" placeholder="必须明确配置" value={scenario.flight.limits[key] ?? ''} onChange={event => update({ flight: { ...scenario.flight, limits: { ...scenario.flight.limits, [key]: event.target.value === '' ? null : Number(event.target.value) } } })}/></label>)}</div>
      <label className="field"><span>审批有效期（秒）</span><input type="number" min="10" max="600" value={scenario.flight.approvalSeconds} onChange={event => update({ flight: { ...scenario.flight, approvalSeconds: Number(event.target.value) } })}/></label>
      <p className="muted">未填限值时禁止通过自动航线审查。首版仅支持简单起飞/航点、相对起飞点高度、返航自动落地，不检查障碍物和地形。</p>
    </> : <div className="setting-card"><strong>可用 MCP 服务</strong><p>选中的服务仍需在 MCP 管理页启动。</p>{draft.mcp.map(service => <label key={service.id} style={{ display: 'block', marginTop: 12 }}><input type="checkbox" checked={scenario.mcpIds === null || scenario.mcpIds.includes(service.id)} onChange={event => { const ids = scenario.mcpIds || draft.mcp.map(item => item.id); update({ mcpIds: event.target.checked ? [...new Set([...ids, service.id])] : ids.filter(item => item !== service.id) }); }}/>{service.name}</label>)}</div>}
  </>;
}
