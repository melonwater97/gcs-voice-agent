import React, { useEffect, useState } from 'react';
import { request, subscribe } from './api';
import './flight.css';

const labels = { new: '准备仿真', planning: '航线设计', reviewed: '基础审查通过', awaiting_origin: '等待位置调整批准', adjusting_origin: '调整仿真位置中', awaiting_upload: '等待上传批准', uploading: '上传并读回核对', uploaded: '航线已核对', awaiting_start: '等待启动批准', starting: '解锁并启动中', monitoring: '执行与监测', landed: '已落地并解除解锁', collecting: '收集日志', reporting: '生成报告', completed: '报告已生成', intervention_required: '请求人工接管', unknown: '结果待确认' };
const actions = { origin: '调整实际起飞位置', upload: '上传当前航线', start: '解锁并启动任务（结束后返航落地）' };
export default function FlightPanel({ session, busy, error }) {
  const [flight, setFlight] = useState(null), [pending, setPending] = useState(false), [expanded, setExpanded] = useState(true);
  useEffect(() => {
    setFlight(null); let alive = true;
    request(`/api/flights/session/${session.id}`).then(value => { if (alive) setFlight(value); }).catch(err => error(err.message));
    const off = subscribe(event => { if (event.type === 'flight' && event.sessionId === session.id) setFlight(current => !current || event.flight.createdAt >= current.createdAt ? event.flight : current); });
    return () => { alive = false; off(); };
  }, [session.id]);
  useEffect(() => {
    if (!flight || !['monitoring', 'intervention_required'].includes(flight.phase)) return;
    let polling = false;
    const timer = setInterval(async () => {
      if (polling) return; polling = true;
      try { await request(`/api/flights/${flight.id}/poll`, 'POST', {}); } catch (err) { error(err.message); } finally { polling = false; }
    }, 3000);
    return () => clearInterval(timer);
  }, [flight?.id, flight?.phase]);
  const perform = async fn => { setPending(true); try { const value = await fn(); if (value) setFlight(value); } catch (err) { error(err.message); } finally { setPending(false); } };
  const propose = action => perform(() => request(`/api/flights/${flight.id}/request`, 'POST', { action, payload: action === 'origin' ? { position: flight.draft?.selectedPosition } : {} }));
  const approval = flight?.approvals.findLast(item => item.status === 'pending');
  const blocked = busy || pending, ended = flight && ['landed', 'completed', 'collecting', 'reporting'].includes(flight.phase);
  return <section className="flight-panel" aria-label="仿真飞行流程"><header><button onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>仿真飞行 · {labels[flight?.phase] || '尚未开始'}</button><small>仅仿真 · 界面审批</small></header>{expanded && <div className="flight-panel-body">
    {!flight ? <button className="secondary" disabled={blocked} onClick={() => perform(() => request('/api/flights', 'POST', { sessionId: session.id }))}>开始本次仿真准备</button> : <>
      <div className="flight-meta"><span>目标：{flight.status?.vehicleId || '未核验'}</span><span>航线版本：{flight.revision}</span><span>{flight.status ? `${flight.status.armed ? '已解锁' : '未解锁'} · ${flight.status.landed ? '地面' : '离地或未知'}` : '遥测未连接'}</span></div>
      <div className="flight-buttons"><button className="secondary" disabled={blocked} onClick={() => perform(() => request(`/api/flights/${flight.id}/sync`, 'POST', {}))}>查询状态并同步航线</button>
        <label className="secondary flight-file">导入 .plan 到 QGC<input type="file" accept=".plan,.json" hidden disabled={blocked || ended || !flight.draft} onChange={event => { const file = event.target.files[0]; if (file) void perform(async () => request(`/api/flights/${flight.id}/preview`, 'POST', { plan: JSON.parse(await file.text()), expectedRevision: flight.draft.revision })); event.target.value = ''; }}/></label>
        <button className="secondary" disabled={blocked || ended || !flight.draft?.selectedPosition} onClick={() => propose('origin')}>请求调整起飞位置</button>
        <button className="secondary" disabled={blocked || ended || !flight.review?.passed} onClick={() => propose('upload')}>请求上传航线</button>
        <button className="secondary" disabled={blocked || ended || !flight.uploaded?.verified} onClick={() => propose('start')}>请求启动任务</button>
      </div>
      {flight.draft?.selectedPosition && <p>QGC 选点：{flight.draft.selectedPosition.latitude}, {flight.draft.selectedPosition.longitude}；海拔 {flight.draft.selectedPosition.altitudeAmslM ?? '未指定'} 米</p>}
      {flight.review && <details><summary>基础审查：{flight.review.passed ? '通过' : '未通过'} · {flight.review.summary.waypointCount} 个任务项</summary><ul>{[...flight.review.errors, ...flight.review.warnings].map((text, index) => <li key={index}>{text}</li>)}</ul></details>}
      {approval && <div className="flight-approval" aria-label="待批准飞行操作"><strong>需要你批准：{actions[approval.action]}</strong><p>目标 {approval.vehicleId} · 航线版本 {approval.revision} · 有效至 {new Date(approval.expiresAt).toLocaleTimeString()}</p>{approval.summary && <p>{approval.summary.waypointCount} 个任务项，最高相对高度 {approval.summary.maxAltitudeM} 米，速度 {approval.summary.speedMS} 米/秒，结束后返航自动落地。</p>}{approval.action === 'origin' && <p>从 {JSON.stringify(approval.positionBefore)} 调整为 {JSON.stringify(approval.payload.position)}；将重建仿真并使旧批准失效。</p>}<p>未验证障碍物与地形。批准只针对本次内容，修改后需重新确认。</p><div className="flight-buttons"><button className="secondary" disabled={blocked} onClick={() => perform(() => request(`/api/flights/${flight.id}/approvals/${approval.id}`, 'POST', { decision: 'reject' }))}>拒绝，继续修改</button><button className="primary" disabled={blocked || Date.now() >= approval.expiresAt} onClick={() => perform(() => request(`/api/flights/${flight.id}/approvals/${approval.id}`, 'POST', { decision: 'approve' }))}>批准：{actions[approval.action]}</button></div></div>}
      {flight.notices.slice(-3).map((notice, index) => <p className="flight-warning" key={index}>{notice}</p>)}
      {['unknown', 'intervention_required'].includes(flight.phase) && <button className="secondary" disabled={blocked} onClick={() => perform(() => request(`/api/flights/${flight.id}/reconcile`, 'POST', {}))}>已人工处理，核对地面状态并恢复</button>}
      {['landed', 'completed'].includes(flight.phase) && <button className="secondary" disabled={blocked} onClick={() => perform(() => request(`/api/flights/${flight.id}/report`, 'POST', {}))}>生成或重试飞行报告</button>}
      {['landed', 'completed'].includes(flight.phase) && <button className="secondary" disabled={blocked} onClick={() => perform(() => request('/api/flights', 'POST', { sessionId: session.id, newRun: true }))}>开始新一轮仿真，保留本次记录</button>}
      {flight.report && <div className="flight-buttons">{['md', 'docx', 'log'].filter(type => flight.report[type]).map(type => <a key={type} href={`/api/flights/${flight.id}/artifacts/${type}`} download onClick={event => { if (window.linghang) { event.preventDefault(); window.linghang.downloadArtifact(flight.id, type).catch(err => error(err.message)); } }}>下载 {type.toUpperCase()}</a>)}</div>}
    </>}
    <p className="flight-disclaimer">基础审查未验证障碍物与地形。暂停助手不等于暂停飞行；异常时告警并请求人工接管，保留 PX4 既有失效保护。</p>
  </div>}</section>;
}
