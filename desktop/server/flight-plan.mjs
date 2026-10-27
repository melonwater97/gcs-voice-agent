import { createHash } from 'node:crypto';

export const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
export const digest = value => createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
const radians = value => value * Math.PI / 180;
export function distanceM(a, b) {
  const dlat = radians(b.latitude - a.latitude), dlon = radians(b.longitude - a.longitude);
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(radians(a.latitude)) * Math.cos(radians(b.latitude)) * Math.sin(dlon / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(Math.max(0, 1 - h)));
}
export function validPosition(position) {
  return position && Number.isFinite(position.latitude) && Math.abs(position.latitude) <= 90
    && Number.isFinite(position.longitude) && Math.abs(position.longitude) <= 180;
}
export function inspectPlan(plan, limits, origin) {
  const errors = [], warnings = ['未验证障碍物与地形，不代表已完成全面安全验证。'];
  if (plan?.fileType !== 'Plan' || !Array.isArray(plan.mission?.items)) errors.push('只支持 QGC .plan 结构化航线');
  const items = plan?.mission?.items || [], points = [];
  if (!items.length || items.length > 200) errors.push('航线需要 1–200 个任务项');
  for (const [index, item] of items.entries()) {
    if (item.type !== 'SimpleItem' || ![16, 22].includes(item.command)) { errors.push(`任务项 ${index + 1} 不是首版支持的简单起飞/航点命令`); continue; }
    if (![3, 6].includes(item.frame)) errors.push(`任务项 ${index + 1} 必须明确使用相对起飞点高度`);
    const point = { latitude: item.params?.[4], longitude: item.params?.[5], altitudeM: item.params?.[6], command: item.command };
    if (!validPosition(point) || !Number.isFinite(point.altitudeM) || point.altitudeM <= 0) errors.push(`任务项 ${index + 1} 坐标或高度无效`);
    points.push(point);
    if (index && item.command === 22) errors.push('只允许首个任务项为起飞命令');
    if (item.autoContinue !== true) errors.push(`任务项 ${index + 1} 未设置自动继续`);
    if ((item.params || []).slice(0, 4).some(value => value !== null && value !== 0)) errors.push(`任务项 ${index + 1} 包含尚未审查支持的非默认停留/转弯/航向参数`);
  }
  if (items[0]?.command !== 22) errors.push('航线必须以明确的起飞任务项开始');
  if (!validPosition(origin)) errors.push('缺少实际仿真起飞位置，不能估算距离');
  const speed = plan?.mission?.hoverSpeed;
  if (!Number.isFinite(speed) || speed <= 0) errors.push('航线必须明确提供正数 hoverSpeed');
  for (const key of ['maxAltitudeM', 'maxSpeedMS', 'maxDistanceM', 'maxDurationS']) if (!Number.isFinite(limits?.[key]) || limits[key] <= 0) errors.push(`尚未配置场景限值：${key}`);
  let lengthM = 0, maxDistanceM = 0;
  if (!errors.length) {
    let previous = origin;
    for (const point of points) { lengthM += distanceM(previous, point); maxDistanceM = Math.max(maxDistanceM, distanceM(origin, point)); previous = point; }
    lengthM += distanceM(previous, origin);
    if (points.some(point => point.altitudeM > limits.maxAltitudeM)) errors.push('航点高度超过场景上限');
    if (speed > limits.maxSpeedMS) errors.push('速度超过场景上限');
    if (maxDistanceM > limits.maxDistanceM) errors.push('距起飞点最远距离超过场景上限');
    if (lengthM / speed > limits.maxDurationS) errors.push('水平航程估算时间已超过场景上限');
  }
  warnings.push('预计时长仅为水平航程估算，不含完整爬升、转弯、风和降落时间。');
  return { passed: errors.length === 0, errors, warnings, checkedAt: Date.now(), planHash: digest(plan),
    summary: { waypointCount: points.length, maxAltitudeM: points.length ? Math.max(...points.map(point => point.altitudeM).filter(Number.isFinite)) : null,
      speedMS: Number.isFinite(speed) ? speed : null, routeLengthM: lengthM, maxDistanceM, estimatedHorizontalSeconds: speed > 0 ? lengthM / speed : null,
      altitudeReference: 'relative-to-home', completion: 'return_and_land', obstacleTerrainVerified: false } };
}
