import { useState } from 'react';
import { Activity, ArrowRight, ChevronRight, ClipboardList, Clock3, Crosshair, Layers3 } from 'lucide-react';
import { fieldChanges, shortId, STATUS, taskFields, timeLabel, TOOLS } from '../lib/tasks.js';

function ChangeEntry({ change }) {
  const changes = fieldChanges(change.before, change.after);
  return <div className="change-entry"><div className="change-marker" /><div className="change-content">
    <div className="change-title"><strong>{TOOLS[change.tool] || '任务更新'}</strong><time>{timeLabel(change.at)}</time></div>
    <span className="change-task" title={change.task_id}>{shortId(change.task_id)}</span>
    {change.before?.status !== change.after.status && <div className="change-values"><span>任务状态</span>
      <span>{change.before ? STATUS[change.before.status] || change.before.status : '未创建'}</span><ArrowRight size={12} />
      <b>{STATUS[change.after.status] || change.after.status}</b></div>}
    {changes.map(field => <div className="change-values" key={field.path}><span>{field.label}</span>
      {field.before !== undefined && <><span>{String(field.before)}{field.unit}</span><ArrowRight size={12} /></>}
      <b>{field.after === undefined ? '已移除' : `${field.after}${field.unit}`}</b>
    </div>)}
  </div></div>;
}

export default function TaskPanel({ tasks, changes, selectedId, onSelect, syncing, online }) {
  const [tab, setTab] = useState('parameters');
  const selected = tasks.find(task => task.task_id === selectedId) || tasks.at(-1);
  const fields = taskFields(selected?.task);
  const latest = [...changes].reverse().find(change => change.task_id === selected?.task_id);
  const changedPaths = new Set(latest?.before ? fieldChanges(latest.before, latest.after).map(field => field.path) : []);
  const selectedChanges = [...changes].reverse().filter(change => change.task_id === selected?.task_id);
  return <section className="panel task-panel" aria-label="任务监控">
    <div className="panel-heading"><div className="heading-icon"><Layers3 size={19} /></div>
      <div><h2>任务监控</h2><p>状态与参数，随任务同步</p></div>
      <span className={`live-label ${online ? '' : 'offline'}`}><span className={`status-dot ${online ? 'green' : ''}`} />{syncing ? '同步中' : online ? '已连接' : '未连接'}</span>
    </div>
    <div className="task-list-heading"><span>当前任务</span><span className="count">{tasks.length.toString().padStart(2, '0')}</span><span className="muted">按创建顺序</span></div>
    <div className="task-list">
      {tasks.length === 0 ? <div className="empty-tasks"><div className="empty-grid"><Crosshair size={28} strokeWidth={1.2} /></div>
        <h3>等待第一项任务</h3><p>通过语音创建任务后，<br />任务状态与参数将在这里同步。</p><span>草稿 <ChevronRight size={12} /> 已上传 <ChevronRight size={12} /> 执行中</span></div> :
        tasks.map((task, index) => <button key={task.task_id} className={`task-item ${selected?.task_id === task.task_id ? 'selected' : ''}`} onClick={() => onSelect(task.task_id)}>
          <span className="task-index">{String(index + 1).padStart(2, '0')}</span><div><strong title={task.task?.task_name}>{task.task?.task_name || task.task?.task_type || '任务'}</strong><small title={task.task_id}>{shortId(task.task_id)}</small></div>
          <span className={`badge ${task.status}`}>{STATUS[task.status] || task.status}</span><ChevronRight size={15} />
        </button>)}
    </div>
    <div className="task-detail">
      <div className="detail-tabs" role="tablist" aria-label="任务详情">
        <button id="tab-parameters" role="tab" aria-controls="task-detail-content" aria-selected={tab === 'parameters'} onClick={() => setTab('parameters')} className={tab === 'parameters' ? 'active' : ''}><ClipboardList size={15} />任务参数</button>
        <button id="tab-changes" role="tab" aria-controls="task-detail-content" aria-selected={tab === 'changes'} onClick={() => setTab('changes')} className={tab === 'changes' ? 'active' : ''}><Activity size={15} />变化记录{selectedChanges.length > 0 && <span>{selectedChanges.length}</span>}</button>
      </div>
      <div className="detail-body" id="task-detail-content" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === 'parameters' ? selected ? <>
          <div className="detail-label"><span>参数明细</span><span>{fields.length} 项参数</span></div>
          <dl className="parameter-list">{fields.map(field => <div key={field.path} className={changedPaths.has(field.path) ? 'changed' : ''}>
            <dt>{field.label}{changedPaths.has(field.path) && <i title="最近一次更新改变了此参数" />}</dt>
            <dd>{String(field.value ?? '—')}<small>{field.unit}</small></dd>
          </div>)}</dl>
          <div className="lifecycle">{['draft', 'uploaded', 'executing'].map((status, index) => <div key={status} className={['draft', 'uploaded', 'executing'].indexOf(selected.status) >= index ? 'reached' : ''}>
            <i>{index + 1}</i><span>{STATUS[status]}</span></div>)}</div>
        </> : <div className="empty-detail"><ClipboardList size={23} strokeWidth={1.3} /><span>创建任务后查看完整参数</span></div> :
          selectedChanges.length ? <div className="change-log">{selectedChanges.map(change => <ChangeEntry key={change.revision} change={change} />)}</div> :
            <div className="empty-detail"><Clock3 size={23} strokeWidth={1.3} /><span>任务变化将自动记录在这里</span></div>}
      </div>
    </div>
    <div className="task-footnote"><span className="status-dot" />数据来自 Agent 任务回执<span>模拟环境</span></div>
  </section>;
}
