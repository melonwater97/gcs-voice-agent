import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, AudioLines, BookOpen, Check, ChevronDown, ChevronRight, CircleHelp, Clock3, Copy, Download, FileText, History, Layers3, LoaderCircle, Mic, Minus, MoreHorizontal, Plus, Radio, Search, Settings2, ShieldCheck, Sparkles, Square, SquareArrowOutUpRight, Workflow, Wrench, X } from 'lucide-react';
import { request, subscribe, download } from './api';
import Settings from './Settings.jsx';
import FlightPanel from './FlightPanel.jsx';
import Trajectory, { RecordInspector } from './Trajectory.jsx';
import { projectTrajectory } from './lib/trajectory';

export const statusLabel = { completed: '已完成', running: '执行中', streaming: '生成中', failed: '失败', interrupted: '已中止', unknown: '结果待确认' };
export const pretty = value => value === undefined ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const duration = ms => ms === undefined ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
const toolName = name => ({ create_task: '创建任务', upload_task: '上传任务', execute_task: '执行任务', knowledge_search: '检索知识库', load_skill: '加载 Skill', flight_sync: '查询仿真与同步航线', flight_get_state: '查看仿真流程', flight_request_action: '请求界面批准', flight_edit_waypoints: '修改航线草稿', flight_cancel_request: '取消待批准请求', flight_generate_report: '生成飞行报告' }[name?.split('__').at(-1)] || name);

function IconButton({ title, children, onClick, disabled, className = '' }) { return <button className={`icon-button ${className}`} aria-label={title} title={title} onClick={onClick} disabled={disabled}>{children}</button>; }
export function Dialog({ title, children, onClose, wide = false }) {
  useEffect(() => { const handler = e => { if (e.key === 'Escape') onClose(); }; document.addEventListener('keydown', handler); return () => document.removeEventListener('keydown', handler); }, [onClose]);
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><section role="dialog" aria-modal="true" aria-label={title} className={`dialog ${wide ? 'wide' : ''}`}><header className="dialog-header"><h2>{title}</h2><IconButton title="关闭" onClick={onClose}><X size={18}/></IconButton></header>{children}</section></div>;
}
function Record({ row, mode, inspect }) {
  if (['run', 'context'].includes(row.kind)) return null;
  if (row.kind === 'assistant' && !row.text) return null;
  if (row.kind === 'user') return <article className="message user"><div className="user-bubble">{row.text}</div></article>;
  if (row.kind === 'assistant') return <article className="message assistant"><div className="assistant-avatar"><Sparkles size={15}/></div><div className="assistant-content"><div className="message-label">领航<span>{new Date(row.at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span></div><div className="message-text">{row.text}</div>{mode === 'trace' && row.usage?.totalTokens > 0 && <div className="usage">{row.usage.totalTokens.toLocaleString()} tokens</div>}</div></article>;
  if (['tool', 'workflow'].includes(row.kind)) return <button className={`tool-row ${row.status}`} onClick={() => inspect(row)}><span className="tool-icon">{row.status === 'running' ? <LoaderCircle className="spin" size={16}/> : row.kind === 'workflow' ? <Workflow size={16}/> : <Wrench size={16}/>}</span><span className="tool-info"><strong>{toolName(row.name)}</strong><span>{row.kind === 'workflow' ? '工作流步骤' : row.name?.startsWith('mcp__') ? row.name.split('__')[1] : '助手工具'}</span></span><span className={`status ${row.status}`}>{statusLabel[row.status]}</span><span className="tool-duration">{duration(row.durationMs)}</span><ChevronRight size={14}/></button>;
  return <div className={`notice ${row.status}`}><span className="notice-dot"/>{row.text}</div>;
}

export default function App() {
  const [state, setState] = useState(null), [session, setSession] = useState(null), [error, setError] = useState('');
  const [settings, setSettings] = useState(false), [history, setHistory] = useState(false), [query, setQuery] = useState('');
  const [text, setText] = useState(''), [mode, setMode] = useState(() => new URLSearchParams(window.location.search).get('view') === 'trace' || localStorage.getItem('linghang.view') === 'trace' ? 'trace' : 'chat'), [selected, setSelected] = useState(null), [loading, setLoading] = useState(false);
  const [phase, setPhase] = useState('idle'), [recording, setRecording] = useState(null), [flowMenu, setFlowMenu] = useState(false), [flow, setFlow] = useState(null), [flowInput, setFlowInput] = useState('');
  const sessionRef = useRef(null), bottom = useRef(null), follows = useRef(true), stopRecording = useRef(false);
  const busy = loading || Boolean(state?.active) || phase !== 'idle';
  async function openSession(id) {
    const value = await request(`/api/sessions/${id}`); sessionRef.current = value.id; setSession(value); setSelected(null); setHistory(false); follows.current = true;
  }
  async function refresh() {
    const value = await request('/api/state'); setState(value); return value;
  }
  async function bootstrap() {
    try {
      const value = await refresh();
      setPhase(value.voice === 'idle' ? 'idle' : value.voice);
      const id = sessionRef.current || value.active?.sessionId || value.sessions[0]?.id;
      if (id) await openSession(id);
      else { const s = await request('/api/sessions', 'POST', {}); sessionRef.current = s.id; setSession(s); }
      setError('');
    } catch (err) { setError(`无法连接助手服务：${err.message}`); }
  }
  useEffect(() => {
    void bootstrap();
    const unsub = subscribe(event => {
      if (event.type === 'record' && event.sessionId === sessionRef.current) setSession(old => {
        if (!old) return old;
        const i = old.records.findIndex(r => r.id === event.record.id), records = [...old.records];
        if (i < 0) records.push(event.record); else records[i] = event.record;
        return { ...old, records };
      });
      if (event.type === 'status' || event.type === 'settings' || event.type === 'mcp_runtime') void refresh().catch(err => setError(err.message));
      if (event.type === 'reconnected') void bootstrap();
    });
    return unsub;
  }, []);
  useEffect(() => { if (follows.current) bottom.current?.scrollIntoView({ behavior: 'auto' }); }, [session?.records]);
  useEffect(() => {
    localStorage.setItem('linghang.view', mode);
    const url = new URL(window.location.href);
    if (mode === 'trace') url.searchParams.set('view', 'trace'); else url.searchParams.delete('view');
    window.history.replaceState(null, '', url);
  }, [mode]);
  useEffect(() => {
    if (phase !== 'recording') return;
    const timer = setInterval(async () => {
      try {
        const snap = await request('/api/voice/status'); setRecording(snap);
        if (!snap.listening && snap.seconds > 0 && !stopRecording.current) { stopRecording.current = true; void finishVoice(); }
      } catch (err) { setError(err.message); setPhase('idle'); }
    }, 300);
    return () => clearInterval(timer);
  }, [phase]);
  async function newSession() {
    try { const s = await request('/api/sessions', 'POST', {}); sessionRef.current = s.id; setSession(s); setSelected(null); setHistory(false); await refresh(); } catch (err) { setError(err.message); }
  }
  async function send(message = text, options = {}) {
    if (!session || !message.trim() || busy) return;
    setError(''); setLoading(true); setText(''); follows.current = true;
    try { await request('/api/run', 'POST', { sessionId: session.id, text: message, ...options }); await refresh(); }
    catch (err) { setError(err.message); setText(message); }
    finally { setLoading(false); }
  }
  async function startVoice() {
    if (busy) return; setError(''); setPhase('starting'); stopRecording.current = false;
    try { const snap = await request('/api/voice/start', 'POST', {}); setRecording(snap); setPhase('recording'); }
    catch (err) { setError(err.message); setPhase('idle'); }
  }
  async function finishVoice() {
    setPhase('transcribing'); setError('');
    try { await request('/api/voice/stop', 'POST', { sessionId: sessionRef.current }); await refresh(); }
    catch (err) { setError(err.message); }
    finally { setPhase('idle'); setRecording(null); }
  }
  async function stop() { try { await request('/api/stop', 'POST', {}); setPhase('idle'); setRecording(null); await refresh(); } catch (err) { setError(err.message); } }
  const records = session?.records || [], flows = state?.config.workflows.filter(f => f.enabled) || [];
  const projection = useMemo(() => projectTrajectory(session?.records), [session?.records]);
  return <div className={`app ${selected ? 'with-inspector' : ''}`}>
    <div className="workspace">
      <header className="app-header"><div className="brand"><span className="brand-logo"><AudioLines size={21}/></span><strong>领航</strong><span className="brand-divider"/><span className="brand-caption">语音助手</span><span className="version-tag">PREVIEW</span></div><div className="header-actions"><select className="scenario-picker" aria-label="当前场景" value={session?.scenarioId || state?.config.activeScenarioId || 'groundstation'} disabled={busy || !state} onChange={async event => { try { const value = await request('/api/scenarios/switch', 'POST', { id: event.target.value }); sessionRef.current = value.id; setSession(value); setSelected(null); await refresh(); } catch (err) { setError(err.message); } }}>{state?.config.scenarios?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><span className="engine-pill"><i/>Pi Engine</span><IconButton title="设置" onClick={() => setSettings(true)}><Settings2 size={18}/></IconButton>{window.linghang && <div className="window-controls"><IconButton title="最小化" onClick={() => window.linghang.window('minimize')}><Minus size={14}/></IconButton><IconButton title="最大化" onClick={() => window.linghang.window('maximize')}><Square size={11}/></IconButton><IconButton title="关闭窗口" onClick={() => window.linghang.window('close')}><X size={16}/></IconButton></div>}</div></header>
      <div className="conversation-toolbar"><div className="conversation-title"><IconButton title="历史会话" onClick={() => setHistory(!history)}><History size={17}/></IconButton><span>{session?.title || '新对话'}</span><IconButton title="新对话" onClick={newSession} disabled={busy}><Plus size={17}/></IconButton></div><div className="view-switch" aria-label="显示模式"><button className={mode === 'chat' ? 'active' : ''} onClick={() => setMode('chat')}>对话</button><button className={mode === 'trace' ? 'active' : ''} onClick={() => setMode('trace')}>轨迹</button></div><IconButton title="导出会话" onClick={() => session && download(`领航-${session.title}.json`, JSON.stringify(session, null, 2))}><Download size={16}/></IconButton></div>
      {history && <div className="history-popover"><label className="search-box"><Search size={15}/><input aria-label="搜索会话名称" placeholder="搜索会话名称" value={query} onChange={e => setQuery(e.target.value)}/></label><div className="history-list">{state?.sessions.filter(s => s.title.includes(query)).map(s => <button key={s.id} disabled={busy} onClick={() => openSession(s.id).catch(err => setError(err.message))}><span>{s.title}</span><small>{new Date(s.updatedAt).toLocaleDateString('zh-CN')} · {s.count} 条记录</small></button>)}{!state?.sessions.length && <p className="muted">暂无历史会话</p>}</div></div>}
      {session?.scenarioId === 'px4-simulation' && <FlightPanel key={session.id} session={session} busy={busy} error={setError}/>}
      {mode === 'trace' ? <Trajectory session={session} active={state?.active?.sessionId === session?.id} selectedId={selected?.id} onSelect={setSelected} onClose={() => setSelected(null)}/> : <main className={`conversation-scroll ${!records.length ? 'empty' : ''}`} onScroll={e => { const el = e.currentTarget; follows.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }}>
        {!records.length ? <div className="welcome"><div className="welcome-orbit"><span/><div className="welcome-logo"><AudioLines size={35}/></div></div><div className="eyebrow">YOUR WORK, IN GOOD HANDS</div><h1>让想法，成为行动。</h1><p>用一句话开始，领航帮你连接知识与工具。<br/>每一步执行，都有迹可循。</p><div className="suggestions">{[{ icon: Radio, title: '创建业务任务', text: '创建一个放飞前出任务', hint: '通过对话完成参数配置' }, { icon: BookOpen, title: '查阅知识资料', text: '请根据知识库说明上传任务前的检查事项', hint: '从你的资料中寻找答案' }, { icon: Workflow, title: '运行工作流程', action: () => { setFlowMenu(true); }, hint: '让重复工作按步骤完成' }].map(item => <button key={item.title} disabled={busy} onClick={() => item.action ? item.action() : setText(item.text)}><item.icon size={20}/><strong>{item.title}</strong><span>{item.hint}</span><ChevronRight size={14}/></button>)}</div><button className="welcome-settings" onClick={() => setSettings(true)}><Settings2 size={14}/>首次使用？配置模型和服务<ChevronRight size={14}/></button></div> : <div className={`records ${mode === 'trace' ? 'trace-mode' : ''}`}>{records.slice(-500).map(row => <Record key={row.id} row={row} mode={mode} inspect={setSelected}/>)}{records.length > 500 && <div className="notice">当前展示最近 500 条记录，完整记录可通过导出查看。</div>}{state?.active?.sessionId === session?.id && <div className="working"><span className="thinking-dots"><i/><i/><i/></span>领航正在处理</div>}<div ref={bottom}/></div>}
      </main>}
      <footer className="composer-wrap">{error && <div className="error-banner" role="alert"><CircleHelp size={16}/><span>{error}</span><IconButton title="关闭提示" onClick={() => setError('')}><X size={14}/></IconButton></div>}{phase === 'recording' && <div className="recording-bar"><i/><span>正在聆听</span><div className="audio-meter" style={{ '--level': recording?.level || 0 }}/><span>{recording?.seconds || 0} / {recording?.max_seconds || 90}s</span><button onClick={() => { stopRecording.current = true; void finishVoice(); }}>结束并发送</button></div>}<div className="composer"><textarea aria-label="消息输入" placeholder="输入消息，或点击麦克风开始说话…" value={text} onChange={e => setText(e.target.value)} disabled={phase !== 'idle'} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}/><div className="composer-bottom"><div className="composer-tools"><button className="capability-button" onClick={() => setFlowMenu(!flowMenu)} disabled={busy}><Workflow size={16}/><span>工作流</span><ChevronDown size={12}/></button><span className="composer-hint">Enter 发送 · Shift + Enter 换行</span></div><div className="send-actions"><IconButton title={phase === 'recording' ? '结束录音' : '开始录音'} onClick={phase === 'recording' ? finishVoice : startVoice} disabled={busy && phase !== 'recording'} className={phase === 'recording' ? 'recording' : ''}><Mic size={19}/></IconButton>{busy && phase !== 'recording' ? <button className="send-button stopping" aria-label="停止执行" onClick={stop}><Square size={14} fill="currentColor"/></button> : <button className="send-button" aria-label="发送消息" onClick={() => send()} disabled={!text.trim() || busy}><ArrowUp size={20}/></button>}</div></div></div><div className="footer-meta"><span><span className={`connection-dot ${state ? 'connected' : ''}`}/>{phase === 'transcribing' ? '正在识别语音' : busy ? '执行中' : state ? '就绪' : '连接中'}</span><span>领航 0.1 <span>·</span> {state?.config.model.model || 'Pi 1.4.0'}</span></div>{flowMenu && <div className="flow-popover"><strong>选择工作流</strong>{flows.length ? flows.map(f => <button key={f.id} disabled={busy} onClick={() => { setFlow(f); setFlowInput(JSON.stringify(f.inputExample || {}, null, 2)); setFlowMenu(false); }}><Workflow size={16}/><span>{f.name}</span><small>v{f.version || 1}</small><ChevronRight size={14}/></button>) : <p>还没有工作流，请在设置中添加模板。</p>}<button className="text-button" onClick={() => { setSettings(true); setFlowMenu(false); }}>管理工作流<Settings2 size={14}/></button></div>}</footer>
    </div>
    {selected && mode === 'chat' && <RecordInspector key={selected.id} row={projection.byId.get(selected.id) || selected} projection={projection} navigate={setSelected} close={() => setSelected(null)}/>}
    {settings && state && <Settings state={state} refresh={refresh} close={() => setSettings(false)} busy={busy}/>}
    {flow && <Dialog title={`运行：${flow.name}`} onClose={() => setFlow(null)}><div className="dialog-content"><p className="muted">将按模板依次执行：{flow.steps.map(s => s.id).join(' → ')}。请输入本次参数。</p><label className="field">输入参数 JSON<textarea className="json-editor" aria-label="工作流输入参数" value={flowInput} onChange={e => setFlowInput(e.target.value)}/></label><button className="primary" onClick={() => { try { const input = JSON.parse(flowInput); void send(`运行工作流：${flow.name}`, { workflowId: flow.id, input }); setFlow(null); } catch { setError('工作流输入必须为合法 JSON'); } }}>开始运行<ArrowUp size={16}/></button></div></Dialog>}
  </div>;
}
