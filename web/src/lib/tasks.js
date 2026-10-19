export const STATUS = { draft: '草稿', uploaded: '已上传', executing: '执行中', completed: '已完成', aborted: '已终止' };
export const TOOLS = { create_task: '创建任务', upload_task: '上传任务', execute_task: '执行任务' };
const FIELDS = {
  task_name: ['任务名称', ''], task_type: ['任务类型', ''], mode: ['放飞模式', ''], takeoff_altitude: ['起飞高度', 'm'],
  formation_type: ['编队类型', ''], open_angle: ['打开角度', '°'], radius: ['编队半径', 'm'],
  formation_length: ['编队长度', 'm'], formation_width: ['编队宽度', 'm'],
  longitude: ['展开经度', '°'], latitude: ['展开纬度', '°'],
};

export function taskFields(task, prefix = '') {
  return Object.entries(task || {}).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === 'object') return taskFields(value, path);
    const [label, unit] = FIELDS[key] || [key, ''];
    return [{ path, label, unit, value }];
  });
}

export function fieldChanges(before, after) {
  const old = new Map(taskFields(before?.task).map(field => [field.path, field]));
  const current = new Map(taskFields(after?.task).map(field => [field.path, field]));
  return [...new Set([...old.keys(), ...current.keys()])].flatMap(path => {
    const prev = old.get(path), next = current.get(path);
    return prev?.value === next?.value ? [] : [{ ...(next || prev), before: prev?.value, after: next?.value }];
  });
}

export function shortId(id) { return id ? `TASK · ${id.replace('TASK-', '').slice(0, 8).toUpperCase()}` : ''; }
export function timeLabel(value) { return new Date(value).toLocaleTimeString('zh-CN', { hour12: false }); }
