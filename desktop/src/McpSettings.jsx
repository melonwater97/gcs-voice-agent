import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Code2, Copy, Pause, Play, Plus, Radio, Settings2, Trash2, Wrench, X } from 'lucide-react';
import { request } from './api';
import './mcp-settings.css';

const managedId = 'python-task-simulator';
const statusText = { running: '运行中', paused: '已暂停', starting: '启动中', pausing: '等待调用结束', failed: '连接异常' };
function Field({ label, children }) { return <label className="field"><span>{label}</span>{children}</label>; }
function JsonField({ label, value, onChange, invalid, id }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  return <Field label={label}><textarea className="json-editor" aria-label={label} spellCheck={false} value={text} onChange={event => {
    setText(event.target.value); try { onChange(JSON.parse(event.target.value)); invalid(id, false); } catch { invalid(id, true); }
  }}/></Field>;
}
function resolve(schema, root) {
  if (!schema?.$ref?.startsWith('#/')) return schema || {};
  return schema.$ref.slice(2).split('/').reduce((value, key) => value?.[key.replace(/~1/g, '/').replace(/~0/g, '~')], root) || schema;
}
function typeName(raw, root) {
  const schema = resolve(raw, root);
  return Array.isArray(schema.type) ? schema.type.join(' / ') : schema.type || (schema.enum ? '枚举' : schema.oneOf || schema.anyOf ? '联合类型' : '—');
}
function ToolDetails({ service, runtime, tool, close }) {
  const [tab, setTab] = useState('overview'), [copied, setCopied] = useState(false);
  const schema = tool.inputSchema || {}, parameters = resolve(schema, schema), properties = Object.entries(parameters.properties || {});
  return <aside className="mcp-tool-detail" aria-label="MCP 工具详情">
    <header><span className="mcp-detail-icon"><Code2 size={18}/></span><div><small>{service.name}</small><h3>{tool.name}</h3></div><button className="icon-button" aria-label="关闭工具详情" onClick={close}><X size={17}/></button></header>
    <nav aria-label="工具详情标签">{[['overview', '概述'], ['parameters', '参数'], ['schema', 'Schema']].map(([key, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}</nav>
    <div className="mcp-detail-content">
      {tab === 'overview' && <><span className={`mcp-state ${runtime.status === 'running' ? 'running' : 'paused'}`}>{runtime.status === 'running' ? '当前可调用' : '当前不可调用'}</span><p className="mcp-tool-description">{tool.description || '服务未提供工具说明。'}</p><dl><dt>服务</dt><dd>{service.name}</dd><dt>来源</dt><dd>{service.id === managedId ? '自建 Python MCP' : '自定义 MCP'}</dd><dt>传输</dt><dd>{service.transport === 'stdio' ? '本地 stdio' : 'Streamable HTTP'}</dd><dt>参数</dt><dd>{properties.length} 项 · {(parameters.required || []).length} 项必填</dd></dl>{tool.annotations && <div className="mcp-hints">{Object.entries(tool.annotations).filter(([, value]) => typeof value === 'boolean').map(([name, value]) => <span key={name}>{name}: {String(value)}</span>)}</div>}</>}
      {tab === 'parameters' && <>{properties.length ? properties.map(([name, raw]) => { const field = resolve(raw, schema); return <div className="mcp-parameter" key={name}><div><code>{name}</code><span>{typeName(raw, schema)}</span>{parameters.required?.includes(name) && <b>必填</b>}</div><p>{field.description || field.title || '详见 Schema。'}</p>{field.enum && <small>可选值：{field.enum.join('、')}</small>}{field.properties && <small>包含：{Object.keys(field.properties).join('、')}</small>}</div>; }) : <p className="muted">此工具没有顶层参数字段，请查看 Schema。</p>}</>}
      {tab === 'schema' && <><button className="text-button" onClick={async () => { await navigator.clipboard.writeText(JSON.stringify(schema, null, 2)); setCopied(true); }}><Copy size={13}/>{copied ? '已复制' : '复制 Schema'}</button><pre>{JSON.stringify(schema, null, 2)}</pre></>}
    </div>
  </aside>;
}

export default function McpSettings({ draft, setDraft, state, refresh, pending, busy, action, invalid, invalids, updateItem }) {
  const [expanded, setExpanded] = useState(new Set()), [selected, setSelected] = useState(null);
  const [configId, setConfigId] = useState(null), [editor, setEditor] = useState(null), [testing, setTesting] = useState(null);
  const runtimes = state.mcpRuntime || [];
  useEffect(() => { setDraft(old => ({ ...old, mcp: old.mcp.map(item => { const saved = state.config.mcp.find(service => service.id === item.id); return saved ? { ...item, enabled: saved.enabled } : item; }) })); }, [state.config.mcp]);
  const update = (item, key, value) => updateItem('mcp', item.id, key, value);
  const toggle = id => setExpanded(old => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const runtimeFor = id => runtimes.find(runtime => runtime.id === id) || { status: 'paused', tools: [], activeCalls: 0 };
  const operate = (id, operation) => action(async () => { try { if (operation === 'test') setTesting(id); await request(`/api/mcp/${id}/${operation}`, 'POST', {}); if (operation === 'start' || operation === 'test') setExpanded(old => new Set([...old, id])); } finally { setTesting(null); await refresh(); } });
  const openConfig = item => action(async () => { if (configId === item.id) { setConfigId(null); return; } setConfigId(item.id); if (item.id === managedId) setEditor({ id: item.id, ...await request(`/api/mcp/${item.id}/config`) }); });
  const selectedService = draft.mcp.find(item => item.id === selected?.id), selectedRuntime = selectedService && runtimeFor(selectedService.id), selectedTool = selectedRuntime?.tools.find(tool => tool.name === selected?.name);
  return <>
    <div className="mcp-section-bar"><span className="mcp-total">{draft.mcp.length} 个服务</span><div>
      {!draft.mcp.some(item => item.id === managedId) && <button className="secondary" disabled={busy || pending} onClick={() => action(async () => { setDraft(await request('/api/mcp/groundstation', 'POST', {})); await refresh(); })}><Plus size={14}/>添加地面站服务</button>}
      <button className="primary" disabled={busy || pending} onClick={() => { const id = crypto.randomUUID(); setDraft(old => ({ ...old, mcp: [...old.mcp, { id, name: `tools_${old.mcp.length + 1}`, transport: 'stdio', command: '', args: [], env: {}, headers: {}, enabled: false }] })); setConfigId(id); }}><Plus size={14}/>添加服务</button>
    </div></div>
    {!draft.mcp.length && <div className="settings-empty"><Wrench size={30}/><h3>连接更多工具</h3><p>添加自建地面站服务，或接入其他 MCP 服务。</p></div>}
    <div className={`mcp-workspace ${selectedTool ? 'has-detail' : ''}`}><div className="mcp-service-list">
      {draft.mcp.map(item => {
        const managed = item.id === managedId, runtime = runtimeFor(item.id), open = expanded.has(item.id), saved = state.config.mcp.some(service => service.id === item.id), running = runtime.status === 'running', changing = ['starting', 'pausing'].includes(runtime.status), remote = item.transport === 'http';
        return <section className="mcp-service" key={item.id} aria-label={`MCP 服务 ${item.name}`}>
          <div className="mcp-service-header"><button className="mcp-service-summary" aria-label={`${open ? '收起' : '展开'}服务 ${item.name}`} aria-expanded={open} onClick={() => toggle(item.id)}>{open ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}<span className={`mcp-service-icon ${managed ? 'builtin' : ''}`}><Wrench size={18}/></span><span className="mcp-service-title"><strong>{managed ? '地面站工具' : item.name}</strong><small>{managed ? `${item.name} · 自建` : remote ? '远程 MCP' : '本地 MCP'}</small></span></button>
            <span className={`mcp-state ${runtime.status}`} title={runtime.error}><i/>{saved ? statusText[runtime.status] : '待保存'}{runtime.activeCalls ? ` · ${runtime.activeCalls} 调用` : ''}</span><span className="mcp-count">{runtime.tools.length} 工具</span>
            <div className="mcp-service-actions"><button className={running ? 'secondary' : 'primary'} disabled={pending || !saved || changing} aria-label={`${running ? remote ? '断开连接' : '暂停服务' : remote ? '连接服务' : '启动服务'} ${item.name}`} onClick={() => operate(item.id, running ? 'pause' : 'start')}>{running ? <Pause size={13}/> : <Play size={13}/>}<span>{running ? remote ? '断开' : '暂停' : remote ? '连接' : '启动'}</span></button>
              <button className="icon-button" disabled={pending || !saved || changing} aria-label={`测试连接 ${item.name}`} title="测试连接" onClick={() => operate(item.id, 'test')}><Radio size={16} className={testing === item.id ? 'spin' : ''}/></button>
              <button className="icon-button" disabled={pending || busy} aria-label={`配置服务 ${item.name}`} title="配置" onClick={() => openConfig(item)}><Settings2 size={16}/></button>
              <button className="icon-button danger" disabled={pending || busy || running || changing} aria-label={`删除 MCP ${item.name}`} title={running ? '先暂停服务再移除' : '移除服务后保存设置'} onClick={() => { setDraft(old => ({ ...old, mcp: old.mcp.filter(service => service.id !== item.id) })); for (const key of ['args', 'env', 'headers']) invalid(`${item.id}-${key}`, false); }}><Trash2 size={15}/></button>
            </div>
          </div>
          {(runtime.error || runtime.testError) && <p className="mcp-service-error" role="status">{runtime.error || runtime.testError}</p>}
          {runtime.lastTestAt && <div className="mcp-test-result"><span>连接测试 · {new Date(runtime.lastTestAt).toLocaleTimeString()}</span><span>{runtime.testError ? 'MCP 连接失败' : 'MCP 可达'}</span>{runtime.http && <span className={runtime.http.reachable ? '' : 'error-text'}>{runtime.http.reachable ? 'HTTP 可达' : 'HTTP 未连接'}{runtime.http.simulation ? ' · 模拟服务' : ''}</span>}</div>}
          {open && <div className="mcp-tools" aria-label={`${item.name} 工具列表`}>{!running && runtime.tools.length > 0 && <p className="mcp-list-note">最近发现的工具 · 启动服务后可调用</p>}{runtime.tools.length ? runtime.tools.map(tool => <button key={tool.name} className={`mcp-tool-row ${selected?.id === item.id && selected.name === tool.name ? 'selected' : ''}`} aria-label={`工具 ${tool.name}`} onClick={() => setSelected({ id: item.id, name: tool.name })}><strong>{tool.name}</strong><span>{tool.description || '服务未提供说明'}</span><ChevronRight size={13}/></button>) : <p className="mcp-list-note">{changing ? '正在读取工具…' : '启动服务或测试连接，读取支持的工具。'}</p>}</div>}
          {configId === item.id && <div className="mcp-config-panel"><div className="mcp-config-heading"><strong>服务配置</strong><button className="icon-button" aria-label="关闭服务配置" onClick={() => setConfigId(null)}><X size={16}/></button></div>
            {managed && editor?.id === item.id && <><Field label="服务配置 YAML"><textarea className="json-editor mcp-yaml" aria-label="服务配置 YAML" spellCheck={false} value={editor.yaml} onChange={event => setEditor(old => ({ ...old, yaml: event.target.value, saved: false }))}/></Field><small className="muted mcp-config-path">{editor.file}</small><p className="muted">base_url 为 HTTP 基础地址，不含 /api/tasks。模拟器使用 simulation: true；真实地面站改为 false。令牌从环境变量读取。</p>
              <button className="primary" disabled={pending || busy} onClick={() => action(async () => { try { const value = await request(`/api/mcp/${item.id}/config`, 'PUT', { yaml: editor.yaml }); setEditor({ id: item.id, ...value, saved: true }); } finally { await refresh(); } })}>保存 YAML 配置</button><p className={editor.saved ? 'success-text' : 'muted'} role="status">{editor.saved ? 'YAML 配置已保存，运行中的服务已重新加载。' : '保存时由 Python 校验；运行中的服务会重启以加载新配置。'}</p></>}
            <details open={!managed} className="mcp-advanced"><summary>高级连接配置</summary><div className="field-grid"><Field label="服务名称"><input value={item.name} onChange={event => update(item, 'name', event.target.value)}/></Field>{!managed && <Field label="传输方式"><select value={item.transport} onChange={event => update(item, 'transport', event.target.value)}><option value="stdio">本地程序 · stdio</option><option value="http">Streamable HTTP</option></select></Field>}</div>
              {item.transport === 'stdio' ? <><Field label="程序路径"><input value={item.command || ''} onChange={event => update(item, 'command', event.target.value)}/></Field>{!managed && <JsonField id={`${item.id}-args`} label="启动参数 JSON 数组" value={item.args || []} onChange={value => update(item, 'args', value)} invalid={invalid}/>}<JsonField id={`${item.id}-env`} label="环境变量 JSON" value={item.env || {}} onChange={value => update(item, 'env', value)} invalid={invalid}/></> : <><Field label="服务地址"><input value={item.url || ''} placeholder="https://example.com/mcp" onChange={event => update(item, 'url', event.target.value)}/></Field><JsonField id={`${item.id}-headers`} label="HTTP 请求头 JSON" value={item.headers || {}} onChange={value => update(item, 'headers', value)} invalid={invalid}/></>}
              <p className="muted">修改连接信息后，点击底部「保存设置」。运行中的服务会重新连接。</p>
            </details>
          </div>}
        </section>;
      })}
    </div>{selectedTool && <ToolDetails key={`${selectedService.id}:${selectedTool.name}`} service={selectedService} runtime={selectedRuntime} tool={selectedTool} close={() => setSelected(null)}/>}</div>
  </>;
}
