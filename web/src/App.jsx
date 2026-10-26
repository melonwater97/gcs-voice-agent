import { useCallback, useEffect, useRef, useState } from 'react';
import { AudioLines, CircleHelp, RadioTower, ShieldCheck, Terminal } from 'lucide-react';
import ChatPanel from './components/ChatPanel.jsx';
import TaskPanel from './components/TaskPanel.jsx';
import { getRecordStatus, getState, sendText, startRecord, stopRecord } from './lib/api.js';
import { nextMsgId } from './lib/msgId.js';

const EMPTY = { tasks: [], changes: [], pending: null, revision: -1 };
const REST_PHASES = new Set(['idle', 'recording']);
const STATUS_POLL_MS = 200;

export default function App() {
  const [state, setState] = useState(EMPTY);
  const [messages, setMessages] = useState([]);
  const [phase, setPhase] = useState('idle');
  const [selectedId, setSelectedId] = useState(null);
  const [level, setLevel] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [maxSeconds, setMaxSeconds] = useState(90);
  const [retry, setRetry] = useState(null);
  const [showHelp, setShowHelp] = useState(false);
  const mounted = useRef(true);
  const busy = useRef(false);
  const polling = useRef(null);
  const stopping = useRef(false);
  const currentState = useRef(EMPTY);
  const stopRef = useRef(null);
  const shownTranscripts = useRef(new Set());

  const addMessage = useCallback(message => {
    if (mounted.current) setMessages(previous => [...previous, { id: crypto.randomUUID(), at: new Date().toISOString(), ...message }]);
  }, []);
  const applyState = useCallback(next => {
    if (!mounted.current || !Array.isArray(next.tasks)) return;
    const old = currentState.current;
    if (old.session_id === next.session_id && old.revision > next.revision) return;
    if (old.session_id && old.session_id !== next.session_id) {
      setMessages([]); setRetry(null); setSelectedId(null); shownTranscripts.current.clear();
      addMessage({ type: 'notice', text: 'Agent 会话已重新启动，已同步新的任务状态。' });
    }
    currentState.current = next;
    setState(next);
  }, [addMessage]);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    // 不再轮询任务状态与健康状态：仅在挂载时同步一次，用于恢复已有任务列表。
    getState().then(snapshot => { if (active) applyState(snapshot); }).catch(() => {});
    return () => {
      active = false; mounted.current = false;
      if (polling.current) clearInterval(polling.current);
    };
  }, [applyState]);

  const processRequest = async request => {
    let response;
    try {
      if (request.kind === 'record-stop') {
        setPhase('stopping');
        response = await stopRecord(request.msgId, frame => {
          if (!mounted.current) return;
          if (!shownTranscripts.current.has(request.msgId)) {
            shownTranscripts.current.add(request.msgId);
            addMessage({ type: 'user', source: 'voice', text: frame.payload.text,
              msgId: frame.header.msg_id });
          }
          setPhase('thinking');
        });
      } else {
        setPhase('thinking');
        response = await sendText(request.text, request.msgId);
      }
      if (!mounted.current) return;
      const result = response.payload;
      if (typeof result.code === 'number' && result.code >= 400) {
        addMessage({ type: 'error', text: result.message || '操作失败。' });
        setRetry(null);
        return;
      }
      addMessage(result);
      if (result.type === 'task_result') setSelectedId(result.task_id);
      setRetry(null);
      // 查询失败不把已成功的操作标记为可重试；任务面板可在下次操作后再同步。
      try { applyState(await getState()); }
      catch { addMessage({ type: 'notice', text: '操作结果已收到，任务面板等待下一次同步。' }); }
    } catch (error) {
      addMessage({ type: 'error', text: error.message });
      if (mounted.current) setRetry(error.retryable ? request : null);
    } finally {
      busy.current = false;
      if (mounted.current) setPhase('idle');
    }
  };

  const stopPolling = () => {
    if (polling.current) { clearInterval(polling.current); polling.current = null; }
  };
  const startPolling = () => {
    stopPolling();
    polling.current = setInterval(async () => {
      try {
        const frame = await getRecordStatus(await nextMsgId());
        if (!mounted.current) return;
        const payload = frame.payload;
        setLevel(payload.level ?? 0);
        setSeconds(payload.seconds ?? 0);
        if (payload.max_seconds) setMaxSeconds(payload.max_seconds);
        // 服务端到录音上限会自动停录，以状态接口的 listening 为准收尾。
        if (!payload.listening) { stopPolling(); stopRef.current(); }
      } catch { /* 录音期间的轮询失败不打断本次录音，下一轮重试 */ }
    }, STATUS_POLL_MS);
  };

  const stopRecording = async () => {
    if (stopping.current) return;
    stopping.current = true;
    stopPolling(); setLevel(0);
    try { await processRequest({ kind: 'record-stop', msgId: await nextMsgId() }); }
    finally { stopping.current = false; }
  };
  stopRef.current = stopRecording;

  const toggleRecording = async () => {
    if (phase === 'recording') { await stopRecording(); return; }
    if (busy.current) return;
    busy.current = true; setRetry(null); setPhase('requesting');
    try {
      const frame = await startRecord(await nextMsgId());
      if (!mounted.current) return;
      if (frame.payload.code !== 200) {
        throw new Error(frame.payload.message || '麦克风监听未开启，请重试。');
      }
      setLevel(0); setSeconds(0);
      setPhase('recording');
      startPolling();
    } catch (error) {
      busy.current = false;
      addMessage({ type: 'error', text: error.message });
      if (mounted.current) setPhase('idle');
    }
  };

  const submitText = async text => {
    if (busy.current) return;
    busy.current = true; setRetry(null); setPhase('thinking');
    addMessage({ type: 'user', source: 'text', text });
    try { await processRequest({ kind: 'text', text, msgId: await nextMsgId() }); }
    catch (error) { busy.current = false; setPhase('idle'); addMessage({ type: 'error', text: error.message }); }
  };
  const retryRequest = () => {
    if (busy.current || !retry) return;
    busy.current = true; processRequest(retry);
  };

  return <div className="app-shell">
    <aside className="rail"><div className="rail-logo"><RadioTower size={23} /></div><div className="rail-divider" />
      <div className="rail-active" title="任务控制台"><Terminal size={20} /></div>
      <span className="rail-label">GCS</span><div className="rail-bottom"><button title="使用说明" aria-label="使用说明" onClick={() => setShowHelp(true)}><CircleHelp size={19} /></button><span className="station-avatar">01</span></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><div className="brand"><strong>地面站<span> / </span>任务控制台</strong><span className="environment-tag">SIMULATION</span></div></header>
      <main>
        <div className="page-intro"><div><div className="eyebrow">GROUND CONTROL STATION</div><h1>任务工作台<span>Mission workspace</span></h1><p>通过自然语言编排任务，实时查看执行反馈。</p></div>
          <div className="simulation-notice"><ShieldCheck size={16} /><span>本地模拟环境<br /><small>任务操作仅作用于模拟服务</small></span></div>
        </div>
        <div className="panels"><ChatPanel messages={messages} phase={phase} level={level} seconds={seconds} maxSeconds={maxSeconds} pending={state.pending} onRecord={toggleRecording} onText={submitText} retry={retry} onRetry={retryRequest} />
          <TaskPanel tasks={state.tasks} changes={state.changes || []} selectedId={selectedId} onSelect={setSelectedId} syncing={!REST_PHASES.has(phase)} />
        </div>
      </main>
      <footer><span><AudioLines size={12} />ASR → Agent → MCP</span><span>GS-01<span className="footer-divider">/</span>模拟地面站</span></footer>
    </div>
    {showHelp && <div className="modal-backdrop" onClick={() => setShowHelp(false)}><section className="help-modal" role="dialog" aria-modal="true" aria-label="使用说明" onClick={event => event.stopPropagation()}>
      <h2>使用说明</h2><p>1. 点击「点击说话」，服务端开始监听麦克风。</p><p>2. 说出任务口令，再点击「结束并发送」。单次最多录音 {Math.round(maxSeconds / 60)} 分钟，到时自动结束。</p><p>3. 录音只发送一次，识别文字先显示，Agent 继续处理后再显示澄清或任务结果。</p><p>4. 在右侧选择任务，查看状态、完整参数和变化记录。</p><p>当前支持创建、上传和模拟执行任务。参数编辑工具尚未接入。任务和变化记录在 Agent 本次运行期间保留；重启 Agent 将清空会话。</p><button autoFocus onClick={() => setShowHelp(false)}>知道了</button>
    </section></div>}
  </div>;
}
