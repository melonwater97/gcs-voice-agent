import { useEffect, useRef, useState } from 'react';
import { ArrowRight, AudioLines, Bot, Check, ChevronDown, CircleAlert, Code2, Mic, Radio, Send, Square } from 'lucide-react';
import { shortId, STATUS, TOOLS, timeLabel } from '../lib/tasks.js';

const PHASES = {
  idle: '准备接收口令', requesting: '正在开启麦克风', recording: '正在聆听',
  stopping: '正在识别语音', thinking: 'Agent 正在处理',
};
const EXAMPLES = ['新增名为晨曦的放飞前出任务，原地起飞，起飞高度50米', '上传晨曦任务', '执行晨曦任务'];
const clock = value => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(Math.floor(value % 60)).padStart(2, '0')}`;

function Message({ message }) {
  if (message.type === 'task_result') return <div className="result-message">
    <Check size={15} /><code>{message.tool}</code><span>{TOOLS[message.tool] || '任务更新'}成功</span>
    <span className={`badge ${message.status}`}>{STATUS[message.status] || message.status}</span>
    <small title={message.task_id}>{shortId(message.task_id)}</small>
  </div>;
  const user = message.type === 'user';
  return <div className={`message ${user ? 'user' : 'agent'} ${message.type === 'error' ? 'error-message' : ''}`}>
    {!user && <div className="avatar">{message.type === 'error' ? <CircleAlert size={17} /> : <Bot size={17} />}</div>}
    <div className="message-body"><div className="message-label">
      <span>{user ? '你' : message.type === 'error' ? '处理提示' : '地面站助手'}</span>
      {message.source === 'voice' && <span className="asr-label"><AudioLines size={11} />语音转写</span>}
      <time>{timeLabel(message.at)}</time>
    </div><div className="bubble">{message.text}</div></div>
  </div>;
}

export default function ChatPanel({ messages, phase, level, seconds, maxSeconds = 90, pending, online, onRecord, onText, retry, onRetry }) {
  const [text, setText] = useState('');
  const [showText, setShowText] = useState(false);
  const bottom = useRef(null);
  const busy = phase !== 'idle';
  const recording = phase === 'recording';
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, phase]);
  const submit = event => {
    event.preventDefault();
    if (!text.trim() || busy || !online) return;
    onText(text.trim()); setText('');
  };
  return <section className="panel chat-panel" aria-label="语音助手">
    <div className="panel-heading"><div className="heading-icon"><AudioLines size={19} /></div>
      <div><h2>语音助手</h2><p>说出你的任务，助手协同完成</p></div>
      <span className="section-code">VOICE / 01</span>
    </div>
    <div className="chat-scroll" role="log" aria-label="聊天记录" aria-live="polite" aria-relevant="additions">
      <div className="session-divider"><span>工作站会话</span><span>语音 · 理解 · 行动</span></div>
      {messages.length === 0 && <div className="welcome">
        <div className="welcome-orbit"><Radio size={30} strokeWidth={1.25} /><span /></div>
        <span className="eyebrow">VOICE COMMAND, READY.</span>
        <h3>你的下一项任务，<br />从一句话开始。</h3>
        <p>点击下方按钮说话，再次点击结束。<br />识别文字、澄清问题和操作结果都会显示在这里。</p>
        <div className="examples"><span>试试这样说</span>{EXAMPLES.map((example, index) =>
          <button key={example} onClick={() => { setText(example); setShowText(true); }} disabled={busy}>
            <span className="example-number">0{index + 1}</span><span>{example}</span><ArrowRight size={14} />
          </button>)}</div>
      </div>}
      {messages.map(message => <Message key={message.id} message={message} />)}
      {busy && !recording && <div className="processing" role="status"><span className="pulse-dots"><i /><i /><i /></span>{PHASES[phase]}</div>}
      <div ref={bottom} />
    </div>
    {pending && <div className="pending"><CircleAlert size={14} /><span>等待补充：{pending}</span></div>}
    {retry && <div className="retry-line"><span>可使用同一消息编号重试，避免重复操作。</span><button disabled={busy || !online} onClick={onRetry}>重试本次请求</button></div>}
    <div className={`composer ${recording ? 'is-recording' : ''}`}>
      <div className="record-status"><span className={`status-dot ${recording ? 'red' : online ? 'green' : ''}`} />
        <span aria-live="polite">{!online && phase === 'idle' ? '等待 Agent 连接' : PHASES[phase]}</span>
        <span className="record-duration">{recording ? `${clock(seconds)} / ${clock(maxSeconds)}` : '点击开始，再次点击结束'}</span>
      </div>
      <div className="record-controls">
        <div className={`waveform ${recording ? 'active' : ''}`} aria-hidden="true">
          {Array.from({ length: 25 }, (_, index) => <i key={index} style={{ height: `${4 + (recording ? level * 42 : 5) * (0.3 + Math.abs(Math.sin(index * 2.1)) * 0.7)}px` }} />)}
        </div>
        <button className={`record-button ${recording ? 'stop' : ''}`} onClick={onRecord}
          disabled={(!online && !recording) || (busy && !recording)} aria-label={recording ? '结束录音并发送' : '开始说话'}>
          {recording ? <Square size={16} fill="currentColor" /> : <Mic size={19} />}<span>{recording ? '结束并发送' : busy ? '处理中…' : '点击说话'}</span>
        </button>
      </div>
      <div className="composer-bottom"><span>录音结束后自动识别与处理</span><button onClick={() => setShowText(value => !value)} aria-expanded={showText}><Code2 size={13} />文字输入<ChevronDown className={showText ? 'rotated' : ''} size={12} /></button></div>
      {showText && <form className="text-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="command">文字口令</label>
        <textarea id="command" placeholder="输入口令，Enter 发送，Shift + Enter 换行" value={text} disabled={busy}
          maxLength={10000} onChange={event => setText(event.target.value)} onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
          }} rows={2} />
        <button type="submit" aria-label="发送文字口令" disabled={!text.trim() || busy || !online}><Send size={17} /></button>
      </form>}
    </div>
  </section>;
}
