import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Clock3, Copy, CornerDownRight, Filter, Focus, GitBranch, Layers3, Search, X } from 'lucide-react';
import { KIND_LABELS, STATUS_LABELS, ROW_HEIGHT, pretty, formatDuration, formatTime, toolLabel, resultText,
  projectTrajectory, visibleItems, matchesRow, timelineData, clampViewport } from './lib/trajectory';
import './trajectory.css';

function Highlight({ text = '', query }) {
  if (!query?.trim()) return text;
  const needle = query.trim().toLocaleLowerCase(), lower = text.toLocaleLowerCase(), pieces = [];
  let start = 0, at;
  while ((at = lower.indexOf(needle, start)) >= 0 && pieces.length < 60) {
    pieces.push(text.slice(start, at), <mark key={at}>{text.slice(at, at + needle.length)}</mark>); start = at + needle.length;
  }
  pieces.push(text.slice(start)); return pieces;
}
function coloredCode(text) {
  const expression = /("(?:\\.|[^"\\])*"\s*:|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)/gi;
  return text.split(expression).map((part, i) => {
    const kind = /^"/.test(part) ? /:\s*$/.test(part) ? 'key' : 'string' : /^(true|false|null)$/.test(part) ? 'literal' : /^-?\d/.test(part) ? 'number' : '';
    return kind ? <span className={`json-${kind}`} key={i}>{part}</span> : part;
  });
}
export function CodeBlock({ title, value, collapsible = false }) {
  const [expanded, setExpanded] = useState(!collapsible), [copied, setCopied] = useState(false), [formatted, setFormatted] = useState(true);
  const original = pretty(value);
  let text = original, json = typeof value === 'object' && value !== null;
  if (typeof value === 'string') try { const parsed = JSON.parse(value); json = typeof parsed === 'object'; if (formatted && json) text = JSON.stringify(parsed, null, 2); } catch { /* Plain text is preserved. */ }
  if (json && !formatted && typeof value === 'object') text = JSON.stringify(value);
  const long = text.length > 1200 || text.split('\n').length > 9;
  return <section className="trace-code"><div className="trace-code-heading"><span>{title}</span><div>{json && <button onClick={() => setFormatted(v => !v)} aria-label={`切换${title}格式`}>{formatted ? '{ }' : '文本'}</button>}<button aria-label={`复制${title}`} onClick={async () => { try { await navigator.clipboard.writeText(original); setCopied(true); setTimeout(() => setCopied(false), 1300); } catch { setCopied(false); } }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button></div></div><pre className={long && !expanded ? 'collapsed' : ''}><code>{json && formatted ? coloredCode(text) : text}</code></pre>{long && <button className="trace-expand" onClick={() => setExpanded(v => !v)}>{expanded ? '收起' : '展开全文'}<ChevronDown size={12} style={{ transform: expanded ? 'rotate(180deg)' : undefined }}/></button>}</section>;
}

export function RecordInspector({ row, projection, close, navigate, embedded = false }) {
  const [tab, setTab] = useState('概述');
  const tabs = row.kind === 'tool' ? ['概述', '参数', '结果', 'Schema', '计时'] : row.kind === 'context' ? ['概述', '上下文', '计时'] : row.kind === 'assistant' ? ['概述', '内容', '原始消息', '计时'] : row.kind === 'workflow' ? ['概述', '参数', '结果', '计时'] : ['概述', '内容', '计时'];
  const parent = projection?.byId.get(row.parentId), timing = {
    开始时间: row.startedAt === undefined ? '未记录' : `${new Date(row.startedAt).toLocaleDateString('zh-CN')} ${formatTime(row.startedAt)}`,
    结束时间: row.endedAt ? formatTime(row.endedAt) : '未记录', 持续时间: formatDuration(row.durationMs),
    ...(['running', 'streaming'].includes(row.status) ? { 说明: '尚未结束，当前仅记录开始时间' } : {}),
    ...(row.kind === 'assistant' ? { 首字延迟: formatDuration(row.ttftMs), 生成时间: formatDuration(row.generationDurationMs),
      输出Token: row.usage?.output > 0 ? row.usage.output : '未提供',
      生成吞吐: row.generationDurationMs > 0 && row.usage?.output > 0 ? `${(row.usage.output / (row.generationDurationMs / 1000)).toFixed(1)} token/s` : '未记录' } : {}),
    ...(row.kind === 'assistant' ? { 计时说明: '首字延迟从模型步骤开始到首个输出片段（含工具参数）；生成时间为其后的流式输出时间。' } : {}),
    ...(row.callId ? { 调用编号: row.callId } : {}), ...(row.runId ? { 运行编号: row.runId } : {}),
  };
  let value;
  if (tab === '参数') value = row.args;
  if (tab === '结果') value = row.result;
  if (tab === 'Schema') value = row.schema;
  if (tab === '计时') value = timing;
  if (tab === '上下文') value = row.context?.systemPrompt || row.context || row.text;
  if (tab === '内容') value = row.text || row.thinking || row.result;
  if (tab === '原始消息') value = row.raw || { text: row.text, thinking: row.thinking, toolCalls: row.toolCalls, usage: row.usage };
  const title = row.kind === 'tool' ? toolLabel(row.name) : row.kind === 'assistant' ? '助手消息' : row.kind === 'context' ? row.text || '运行上下文' : row.kind === 'workflow' ? row.name : KIND_LABELS[row.kind] || '记录';
  return <aside className={`inspector trajectory-inspector ${embedded ? 'embedded' : ''}`} aria-label={row.kind === 'tool' ? '工具详情' : '轨迹详情'}><header className="trace-inspector-header"><span className={`trace-kind ${row.kind}`}>{KIND_LABELS[row.kind] || '系统'}</span><span>第 {row.turnNumber || '—'} 轮{row.stepNumber ? ` · 第 ${row.stepNumber} 步` : ''}</span><button className="icon-button" aria-label="关闭详情" onClick={close}><X size={17}/></button></header><h2 title={row.name}>{title}</h2><div className="inspector-tabs">{tabs.map(name => <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}>{name}</button>)}</div><div className="inspector-body">{tab === '概述' ? <><dl><dt>层级</dt><dd>{parent ? <button className="trace-parent-link" onClick={() => navigate?.(parent)}>{KIND_LABELS[parent.kind]}{parent.name ? ` · ${toolLabel(parent.name)}` : '消息'}<ChevronRight size={13}/></button> : row.kind === 'user' ? '用户输入' : '会话记录'}</dd><dt>状态</dt><dd><span className={`status ${row.status}`}>{STATUS_LABELS[row.status] || row.status}</span></dd>{row.name && <><dt>名称</dt><dd className="mono">{row.name}</dd></>}{row.model && <><dt>模型</dt><dd className="mono">{row.model}</dd></>}<dt>耗时</dt><dd>{formatDuration(row.durationMs)}</dd>{row.historical && <><dt>数据范围</dt><dd>历史记录；未采集的计时不补算</dd></>}</dl>
      {row.kind === 'tool' || row.kind === 'workflow' ? <><CodeBlock title="参数" value={row.args} collapsible/><CodeBlock title="结果" value={resultText(row.result)} collapsible/>{row.kind === 'tool' && <><h3>Schema</h3>{row.description && <p className="trace-description">{row.description}</p>}<CodeBlock title="参数定义" value={row.schema} collapsible/></>}</> : row.kind === 'context' ? <CodeBlock title="上下文" value={row.context?.systemPrompt || row.context || row.text} collapsible/> : <><CodeBlock title="内容" value={row.text || (row.toolCalls?.length ? row.toolCalls : '未提供文本')} collapsible/>{row.thinking && <CodeBlock title="模型提供的推理内容" value={row.thinking} collapsible/>}</>}
      {row.usage?.totalTokens > 0 && <CodeBlock title="用量" value={row.usage} collapsible/>}<CodeBlock title="计时" value={timing} collapsible/>
    </> : <>{tab === 'Schema' && row.description && <p className="trace-description">{row.description}</p>}<CodeBlock key={tab} title={tab} value={value}/></>}</div></aside>;
}

function Timeline({ data, selectedId, onSelect, timeRange, setTimeRange }) {
  const plot = useRef(null), drag = useRef(null), [viewport, setViewport] = useState(null), [brush, setBrush] = useState(null);
  const domain = data.domain, view = viewport ? clampViewport(viewport, domain) : domain, span = view[1] - view[0];
  const toTime = x => view[0] + Math.max(0, Math.min(1, x)) * span;
  useEffect(() => { setViewport(null); setTimeRange(null); }, [domain[0]]);
  useEffect(() => {
    const element = plot.current;
    const wheel = event => {
      event.preventDefault(); if (!data.records.length) return;
      const rect = element.getBoundingClientRect(), pivot = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const nextSpan = Math.max(100, Math.min(domain[1] - domain[0], span * Math.exp(event.deltaY * .002)));
      const center = view[0] + span * pivot;
      setViewport(clampViewport([center - nextSpan * pivot, center + nextSpan * (1 - pivot)], domain));
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [view[0], view[1], domain[0], domain[1], data.records.length]);
  const visible = data.records.filter(row => row.endTime >= view[0] && row.startedAt <= view[1]);
  const stride = Math.max(1, Math.ceil(visible.length / 1400));
  const bars = visible.filter((row, index) => index % stride === 0 || row.id === selectedId);
  const pointer = event => { const rect = plot.current.getBoundingClientRect(); return (event.clientX - rect.left) / rect.width; };
  const lanes = { user: 0, input: 0, context: 0, assistant: 1, tool: 2, workflow: 2, notice: 0 };
  const color = { user: '#7198eb', context: '#6bb78f', input: '#5ab5a6', assistant: '#9274b5', tool: '#dc8d27', workflow: '#4caaa6', notice: '#a5acb7' };
  const activeBrush = brush || timeRange;
  return <div className="trace-timeline" aria-label="执行时间轴"><div className="timeline-labels"><span>输入</span><span>模型</span><span>工具</span></div><div className="timeline-plot" ref={plot} onContextMenu={event => event.preventDefault()}
    onPointerDown={event => { if (![0, 2].includes(event.button)) return; const x = pointer(event); drag.current = { x, start: toTime(x), view: [...view], button: event.button, moved: false }; plot.current.setPointerCapture(event.pointerId); }}
    onPointerMove={event => { if (!drag.current) return; const x = pointer(event), current = drag.current; if (Math.abs(x - current.x) > .004) current.moved = true; if (current.button === 2) { const delta = (current.x - x) * (current.view[1] - current.view[0]); setViewport(clampViewport(current.view.map(v => v + delta), domain)); } else setBrush([Math.min(current.start, toTime(x)), Math.max(current.start, toTime(x))]); }}
    onPointerUp={() => { if (!drag.current) return; if (drag.current.button === 2) { if (!drag.current.moved) setTimeRange(null); } else if (drag.current.moved && brush) setTimeRange(brush); drag.current = null; setBrush(null); }}
    onPointerCancel={() => { drag.current = null; setBrush(null); }}>
    <div className="timeline-ticks">{Array.from({ length: 5 }, (_, i) => <span key={i} style={{ left: `${i * 25}%` }}>{data.records.length ? formatTime(view[0] + span * i / 4) : '—'}</span>)}</div>
    <svg viewBox="0 0 1000 65" preserveAspectRatio="none" className="timeline-svg" aria-label="输入模型工具时间分布"><path d="M0 21H1000M0 43H1000" stroke="#eef0f4" strokeWidth="1"/>{bars.map(row => {
      const x = Math.max(0, (row.startedAt - view[0]) / span * 1000), right = Math.min(1000, (row.endTime - view[0]) / span * 1000), width = Math.max(2, right - x), y = 4 + (lanes[row.kind] || 0) * 22;
      const ttft = row.kind === 'assistant' && row.durationMs > 0 && Number.isFinite(row.ttftMs) ? Math.min(width, width * row.ttftMs / row.durationMs) : 0;
      const label = `${KIND_LABELS[row.kind]} · 第${row.turnNumber || '—'}轮${row.stepNumber ? ` 第${row.stepNumber}步` : ''} · ${row.name || row.text?.slice(0, 35) || '模型响应'}`;
      const tip = `${label}\n${formatTime(row.startedAt)}\n${['running', 'streaming'].includes(row.status) ? '进行中，仅显示开始位置' : formatDuration(row.durationMs)}${Number.isFinite(row.ttftMs) ? `\n首字延迟 ${formatDuration(row.ttftMs)}` : ''}`;
      return <g key={row.id} className="timeline-bar" role="button" tabIndex={0} aria-label={`时间轴：${label}`} onPointerDown={event => event.stopPropagation()} onClick={event => { event.stopPropagation(); onSelect(row); }} onKeyDown={event => { if (event.key === 'Enter') onSelect(row); }}><title>{tip}</title><rect x={x} y={y} width={width} height="11" rx="1.3" fill={color[row.kind] || color.notice} opacity={ttft ? .4 : .9}/>{ttft > 0 && <rect x={x + ttft} y={y} width={Math.max(0, width - ttft)} height="11" rx="1.3" fill={color.assistant}/>} {row.id === selectedId && <rect x={x - 1} y={y - 2} width={width + 2} height="15" rx="2" fill="none" stroke="#6696ed" strokeWidth="1.6"/>}</g>;
    })}{activeBrush && <rect className="timeline-brush" x={Math.max(0, (activeBrush[0] - view[0]) / span * 1000)} width={Math.max(0, (activeBrush[1] - activeBrush[0]) / span * 1000)} y="0" height="65" fill="#7496e51f" stroke="#7198eb" strokeWidth="1" pointerEvents="none"/>}</svg>
  </div><button className="timeline-fit icon-button" aria-label="重置时间轴" title="重置缩放与时间筛选" onClick={() => { setViewport(null); setTimeRange(null); }}><Focus size={15}/></button><span className="timeline-help">滚轮缩放 · 拖动筛选 · 右键清除 / 平移</span></div>;
}

export default function Trajectory({ session, active, selectedId, onSelect, onClose }) {
  const projection = useMemo(() => projectTrajectory(session?.records), [session?.records]);
  const data = useMemo(() => timelineData(projection.rows), [projection]);
  const [query, setQuery] = useState(''), [kind, setKind] = useState('all'), [status, setStatus] = useState('all'), [showDuration, setShowDuration] = useState(true), [showTurns, setShowTurns] = useState(true), [showCalls, setShowCalls] = useState(true);
  const [timeRange, setTimeRange] = useState(null), [collapsedGroups, setCollapsedGroups] = useState(new Set()), [collapsedRows, setCollapsedRows] = useState(new Set());
  const [scrollTop, setScrollTop] = useState(0), [height, setHeight] = useState(400), [reveal, setReveal] = useState(null);
  const ledger = useRef(null), follows = useRef(true), search = useRef(null);
  const options = useMemo(() => ({ query, kind, status, showTurns, showCalls, timeRange }), [query, kind, status, showTurns, showCalls, timeRange]);
  const items = useMemo(() => visibleItems(projection, options, collapsedGroups, collapsedRows), [projection, options, collapsedGroups, collapsedRows]);
  const matches = useMemo(() => projection.rows.filter(row => matchesRow(row, options)).length, [projection, options]);
  const selected = projection.byId.get(selectedId);
  useEffect(() => { const el = ledger.current, observer = new ResizeObserver(entries => setHeight(entries[0].contentRect.height)); observer.observe(el); return () => observer.disconnect(); }, []);
  useLayoutEffect(() => { if (follows.current && ledger.current) { ledger.current.scrollTop = Math.max(0, items.length * ROW_HEIGHT - ledger.current.clientHeight); setScrollTop(ledger.current.scrollTop); } }, [items.length]);
  useEffect(() => {
    if (!reveal || !ledger.current) return;
    const index = items.findIndex(item => item.id === reveal);
    if (index >= 0) { ledger.current.scrollTop = Math.max(0, index * ROW_HEIGHT - height / 2); setScrollTop(ledger.current.scrollTop); setReveal(null); follows.current = false; }
  }, [items, reveal, height]);
  useEffect(() => { setQuery(''); setKind('all'); setStatus('all'); setCollapsedGroups(new Set()); setCollapsedRows(new Set()); follows.current = true; }, [session?.id]);
  const selectAndReveal = row => {
    onSelect(row); setReveal(row.id); setCollapsedGroups(old => { const next = new Set(old); next.delete(row.groupKey); return next; });
    setCollapsedRows(old => { const next = new Set(old); row.ancestors.forEach(id => next.delete(id)); return next; });
    if (!matchesRow(row, options)) { setQuery(''); setKind('all'); setStatus('all'); setTimeRange(null); }
  };
  const toggle = (setter, key) => setter(old => { const next = new Set(old); next.has(key) ? next.delete(key) : next.add(key); return next; });
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 12), end = Math.min(items.length, Math.ceil((scrollTop + height) / ROW_HEIGHT) + 12);
  const keyboard = event => {
    if (event.key === 'Escape') { onClose(); return; }
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault(); const records = items.filter(i => i.type === 'record');
    const at = records.findIndex(i => i.id === selectedId), next = records[Math.max(0, Math.min(records.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)))];
    if (next) selectAndReveal(next.row);
  };
  return <section className="trajectory" aria-label="轨迹视图"><div className="trace-toolbar"><div className="trace-display-controls"><button aria-pressed={showDuration} className={showDuration ? 'on' : ''} onClick={() => setShowDuration(v => !v)}><Clock3 size={13}/>时长</button><button aria-pressed={showTurns} className={showTurns ? 'on' : ''} onClick={() => setShowTurns(v => !v)}><Layers3 size={13}/>轮次</button><button aria-pressed={showCalls} className={showCalls ? 'on' : ''} onClick={() => setShowCalls(v => !v)}><GitBranch size={13}/>调用</button></div><span className="trace-count">{matches.toLocaleString()} / {projection.rows.length.toLocaleString()} 条</span><div className="trace-filters"><select aria-label="记录类型" value={kind} onChange={e => { follows.current = false; setKind(e.target.value); if (ledger.current) ledger.current.scrollTop = 0; }}><option value="all">全部类型</option>{Object.entries(KIND_LABELS).map(([value, name]) => <option value={value} key={value}>{name}</option>)}</select><select aria-label="记录状态" value={status} onChange={e => { follows.current = false; setStatus(e.target.value); if (ledger.current) ledger.current.scrollTop = 0; }}><option value="all">全部状态</option><option value="completed">已完成</option><option value="running">进行中</option><option value="errors">异常 / 中止</option></select></div><label className="trace-search"><Search size={14}/><input ref={search} aria-label="搜索轨迹" placeholder="搜索内容、参数、结果…" value={query} onChange={e => { follows.current = !e.target.value; setQuery(e.target.value); if (ledger.current) ledger.current.scrollTop = 0; }}/>{query && <button aria-label="清除搜索" onClick={() => { setQuery(''); follows.current = true; }}><X size={12}/></button>}</label></div>
    <Timeline data={data} selectedId={selectedId} onSelect={selectAndReveal} timeRange={timeRange} setTimeRange={range => { follows.current = !range; setTimeRange(range); if (range && ledger.current) ledger.current.scrollTop = 0; }}/>
    {timeRange && <div className="trace-time-filter"><Filter size={12}/>{formatTime(timeRange[0])} — {formatTime(timeRange[1])}<button onClick={() => setTimeRange(null)}>清除时间筛选<X size={12}/></button></div>}
    <div className="trace-body"><div ref={ledger} className="trace-ledger" aria-label="轨迹记录列表" tabIndex={0} onKeyDown={keyboard} onScroll={event => { const el = event.currentTarget; setScrollTop(el.scrollTop); follows.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60; }}>
      {!items.length ? <div className="trace-empty"><GitBranch size={26}/><strong>{projection.rows.length ? '没有匹配的记录' : '等待新的对话'}</strong><span>{projection.rows.length ? '尝试更改搜索、类型或时间筛选。' : '输入、模型响应和工具调用将按发生顺序显示在这里。'}</span></div> : <div className="trace-virtual-space" role="list" style={{ height: items.length * ROW_HEIGHT }}>{items.slice(start, end).map((item, offset) => {
        const index = start + offset, style = { top: index * ROW_HEIGHT, height: ROW_HEIGHT };
        if (item.type === 'group') {
          const { group } = item, closed = collapsedGroups.has(group.key);
          return <button className="trace-turn" key={item.id} style={style} aria-label={`${closed ? '展开' : '折叠'}第${group.number}轮`} onClick={() => toggle(setCollapsedGroups, group.key)}>{closed ? <ChevronRight size={12}/> : <ChevronDown size={12}/>}<strong>{group.number ? `第 ${group.number} 轮` : '轮次外记录'}</strong><span>{group.steps} 步 · {group.calls} 次调用</span>{group.tokens > 0 && <span>{group.tokens.toLocaleString()} tokens</span>}{showDuration && <span className="trace-turn-time">{formatDuration(group.durationMs)}</span>}<span className={`status ${group.status}`}>{STATUS_LABELS[group.status]}</span></button>;
        }
        const { row } = item, depth = showCalls ? Math.min(8, row.depth) : 0, label = KIND_LABELS[row.kind] || '系统';
        return <div role="listitem" data-record-id={row.id} key={row.id} className={`trace-row ${row.kind} ${row.id === selectedId ? 'selected' : ''}`} style={{ ...style, '--depth': depth }}><div className="trace-tree-gutter">{showCalls && row.childCount ? <button aria-label={`${collapsedRows.has(row.id) ? '展开' : '折叠'}调用 ${row.name || label}`} onClick={() => toggle(setCollapsedRows, row.id)}>{collapsedRows.has(row.id) ? <ChevronRight size={11}/> : <ChevronDown size={11}/>}</button> : row.parentId && showCalls ? <CornerDownRight size={10}/> : <i/>}</div><button className="trace-row-main" aria-label={`${label} · 第${row.turnNumber || 0}轮 ${row.name || row.text?.slice(0, 45) || '模型响应'} ${STATUS_LABELS[row.status] || row.status}`} onClick={() => onSelect(row)}><span className={`trace-kind ${row.kind}`}>{label}</span><span className="trace-preview">{row.kind === 'tool' ? <><strong>{row.name}</strong><span className="trace-arguments"><Highlight text={pretty(row.args).replace(/\s+/g, ' ')} query={query}/></span><span className="trace-result-arrow">→</span><Highlight text={resultText(row.result).replace(/\s+/g, ' ')} query={query}/></> : <Highlight text={row.preview.replace(/\s+/g, ' ')} query={query}/>}</span><span className={`trace-status-dot ${row.status}`} title={STATUS_LABELS[row.status]}/>{showDuration && <span className="trace-duration">{Number.isFinite(row.durationMs) ? formatDuration(row.durationMs) : ['running', 'streaming'].includes(row.status) ? '进行中' : '—'}</span>}</button></div>;
      })}</div>}
    </div>{selected && <RecordInspector key={selected.id} row={selected} projection={projection} close={onClose} navigate={selectAndReveal} embedded/>}</div>
    <div className="trace-bottom-bar"><span>{active ? '记录实时更新中' : '已保存的执行记录'}{data.records.length > 1400 ? ' · 时间轴使用概览采样，列表保留全部记录' : ''}</span><button onClick={() => { follows.current = true; if (ledger.current) { ledger.current.scrollTop = ledger.current.scrollHeight; setScrollTop(ledger.current.scrollTop); } }}>跟随最新记录<ChevronDown size={12}/></button></div>
  </section>;
}
