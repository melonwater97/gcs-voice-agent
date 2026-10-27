export const KIND_LABELS = { user: '用户', context: '上下文', assistant: '助手', tool: '工具', workflow: '工作流', input: '输入', notice: '系统' };
export const STATUS_LABELS = { completed: '已完成', running: '执行中', streaming: '生成中', failed: '失败', interrupted: '已中止', unknown: '结果待确认' };
export const ROW_HEIGHT = 34;
export function pretty(value) { return value === undefined ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
export function formatDuration(ms) {
  if (!Number.isFinite(ms)) return '未记录';
  if (ms < 1000) return `${Math.round(ms).toLocaleString()} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`;
}
export function formatTime(time) {
  if (!Number.isFinite(time)) return '未记录';
  return new Intl.DateTimeFormat('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 }).format(time);
}
export function toolLabel(name) { return ({ create_task: '创建任务', upload_task: '上传任务', execute_task: '执行任务', knowledge_search: '检索知识库', load_skill: '加载 Skill' })[name?.split('__').at(-1)] || name || ''; }
export function recordPreview(row) {
  if (row.kind === 'tool') return `${row.name || ''} ${pretty(row.args)} → ${resultText(row.result)}`;
  if (row.kind === 'assistant' && !row.text) return row.toolCalls?.length ? `调用 ${row.toolCalls.map(c => c.name).join('、')}` : row.thinking || '模型响应';
  if (row.kind === 'context') return row.context?.systemPrompt || row.text || pretty(row.context);
  return row.text || row.name || pretty(row.result);
}
export function resultText(value) {
  if (value?.content?.length) return value.content.filter(c => c.type === 'text').map(c => c.text).join('\n') || pretty(value);
  return pretty(value);
}
function timeOf(raw) {
  const number = typeof raw.startedAt === 'number' ? raw.startedAt : Date.parse(raw.at);
  return Number.isFinite(number) ? number : undefined;
}

// Project legacy and current records without modifying stored history.
export function projectTrajectory(rawRecords = []) {
  const groups = [], groupsByKey = new Map(), rows = [], byId = new Map(), byCall = new Map();
  let currentKey = 'ungrouped', nextTurn = 0;
  function groupFor(key, raw) {
    if (!groupsByKey.has(key)) {
      const number = raw.turnNumber || (key === 'ungrouped' ? 0 : ++nextTurn);
      nextTurn = Math.max(nextTurn, number);
      const group = { key, number, rows: [], steps: 0, calls: 0, tokens: 0, startedAt: undefined, endedAt: undefined, durationMs: undefined, status: 'completed' };
      groupsByKey.set(key, group); groups.push(group);
    }
    return groupsByKey.get(key);
  }
  for (const raw of rawRecords) {
    if (raw.kind === 'user') currentKey = raw.runId || raw.id;
    const key = raw.runId || currentKey;
    if (raw.kind === 'run') currentKey = key;
    const group = groupFor(key, raw);
    if (raw.kind === 'run') {
      Object.assign(group, { number: raw.turnNumber || group.number, durationMs: raw.durationMs, status: raw.status,
        startedAt: timeOf(raw), endedAt: raw.endedAt, steps: raw.stepCount || 0, runRecord: raw });
      continue;
    }
    const row = { ...raw, turnNumber: raw.turnNumber || group.number, groupKey: key, startedAt: timeOf(raw),
      stepNumber: raw.stepNumber || undefined, historical: !raw.turnNumber };
    if (!row.stepNumber) {
      if (row.kind === 'assistant') group.legacyStep = (group.legacyStep || 0) + 1;
      row.stepNumber = group.legacyStep || undefined;
    }
    group.steps = Math.max(group.steps, row.stepNumber || 0);
    if (row.kind === 'tool') group.calls++;
    if (row.usage?.totalTokens > 0) group.tokens += row.usage.totalTokens;
    if (row.startedAt !== undefined) {
      row.endTime = Number.isFinite(row.durationMs) ? row.startedAt + row.durationMs : row.startedAt;
      group.startedAt = group.startedAt === undefined ? row.startedAt : Math.min(group.startedAt, row.startedAt);
      group.endedAt = group.endedAt === undefined ? row.endTime : Math.max(group.endedAt, row.endTime);
    }
    if (['running', 'streaming'].includes(row.status)) group.status = 'running';
    else if (['failed', 'unknown'].includes(row.status) && group.status !== 'running') group.status = 'failed';
    row.preview = recordPreview(row);
    row.searchText = `${KIND_LABELS[row.kind] || row.kind} ${row.name || ''} ${row.preview} ${pretty(row.args)} ${pretty(row.result)} ${pretty(row.context)} ${pretty(row.schema)} ${row.thinking || ''}`.toLocaleLowerCase();
    group.rows.push(row); rows.push(row); byId.set(row.id, row); if (row.callId) byCall.set(row.callId, row);
  }
  // Resolve explicit nested calls first, then associate tools with the model step.
  for (const group of groups) {
    let assistant;
    for (const row of group.rows) {
      if (row.kind === 'assistant') assistant = row;
      let parent = byId.get(row.parentRecordId) || byCall.get(row.parentCallId) || byId.get(row.parentCallId);
      if (!parent && row.kind === 'tool' && assistant && (!row.stepNumber || row.stepNumber === assistant.stepNumber)) parent = assistant;
      if (parent && parent.id !== row.id) row.parentId = parent.id;
    }
  }
  for (const row of rows) {
    const chain = [], seen = new Set([row.id]); let parent = byId.get(row.parentId);
    while (parent && !seen.has(parent.id) && chain.length < 12) { chain.unshift(parent.id); seen.add(parent.id); parent = byId.get(parent.parentId); }
    row.ancestors = chain; row.depth = chain.length;
    row.childCount = 0;
  }
  for (const row of rows) if (row.parentId && byId.has(row.parentId)) byId.get(row.parentId).childCount++;
  return { groups: groups.filter(g => g.rows.length > 0), rows, byId };
}
export function matchesRow(row, { query = '', kind = 'all', status = 'all', timeRange = null } = {}) {
  if (kind !== 'all' && row.kind !== kind) return false;
  if (status !== 'all' && (status === 'errors' ? !['failed', 'unknown', 'interrupted'].includes(row.status) : status === 'running' ? !['running', 'streaming'].includes(row.status) : row.status !== status)) return false;
  if (query.trim() && !row.searchText.includes(query.trim().toLocaleLowerCase())) return false;
  if (timeRange && (row.startedAt === undefined || row.endTime < timeRange[0] || row.startedAt > timeRange[1])) return false;
  return true;
}
export function visibleItems(projection, options, collapsedGroups = new Set(), collapsedRows = new Set()) {
  const items = [];
  const searching = Boolean(options.query?.trim());
  for (const group of projection.groups) {
    const matching = group.rows.filter(row => matchesRow(row, options));
    if (!matching.length) continue;
    if (options.showTurns) items.push({ type: 'group', id: `group:${group.key}`, group, matchingCount: matching.length });
    if (collapsedGroups.has(group.key) && options.showTurns && !searching) continue;
    for (const row of matching) {
      if (options.showCalls && !searching && row.ancestors.some(id => collapsedRows.has(id))) continue;
      items.push({ type: 'record', id: row.id, row });
    }
  }
  return items;
}
export function timelineData(rows) {
  const records = rows.filter(r => r.startedAt !== undefined);
  if (!records.length) return { domain: [0, 1000], records: [] };
  const start = Math.min(...records.map(r => r.startedAt)), end = Math.max(...records.map(r => r.endTime));
  return { domain: [start, Math.max(start + 1000, end)], records };
}
export function clampViewport(viewport, domain) {
  const length = Math.min(domain[1] - domain[0], Math.max(100, viewport[1] - viewport[0]));
  const start = Math.max(domain[0], Math.min(domain[1] - length, viewport[0]));
  return [start, start + length];
}
