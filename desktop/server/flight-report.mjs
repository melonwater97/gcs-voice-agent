import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const run = promisify(execFile);
const md = value => String(value ?? '缺失').replace(/[<>|`\[\]]/g, char => `\\${char}`).replace(/[\r\n]+/g, ' ');
const number = value => Number.isFinite(value) ? value.toFixed(2) : '缺失';
export function findPandoc(store) {
  const explicit = process.env.LH_PANDOC_PATH || store.config.general.pandocPath;
  if (explicit) return explicit;
  const bundled = path.join(store.dir, 'tools', 'pandoc', process.platform === 'win32' ? 'pandoc.exe' : 'pandoc');
  return fs.existsSync(bundled) ? bundled : 'pandoc';
}
export function reportMarkdown(flight, manifest) {
  const metrics = manifest.metrics || {}, summary = flight.review?.summary || {};
  const eventRows = flight.events.map(event => {
    const detail = typeof event.detail === 'string' ? event.detail : [event.detail?.action, event.detail?.result?.status, event.detail?.vehicleId, event.detail?.revision ? `revision ${event.detail.revision}` : ''].filter(Boolean).join(' · ');
    return `| ${new Date(event.at).toISOString()} | ${md(event.type)} | ${md(detail).slice(0, 140)} |`;
  }).join('\n');
  const values = {
    flightId: md(flight.id), vehicleId: md(manifest.vehicleId), eventRows,
    planSummary: `航线版本：${flight.revision}。草稿摘要：${md(flight.draft?.digest)}。任务项 ${summary.waypointCount ?? '缺失'} 个，最高计划相对高度 ${number(summary.maxAltitudeM)} 米，计划速度 ${number(summary.speedMS)} 米/秒。正常完成行为为返航并自动落地。`,
    outcome: `任务结果：${flight.outcome === 'interrupted' ? '异常中断或人工接管后结束' : '按已记录的任务执行结果结束'}。已核对落地和解除解锁遥测，不以命令发送成功代替飞行完成。`,
    metricSource: md(metrics.source),
    metricRows: `| 任务时长（秒） | ${number(metrics.durationSeconds)} |\n| 遥测样本数 | ${metrics.sampleCount ?? '缺失'} |\n| 最大实际相对高度（米） | ${number(metrics.maxRelativeAltitudeM)} |\n| 最大实际水平速度（米/秒） | ${number(metrics.maxHorizontalSpeedMS)} |`,
    missing: [...(flight.notices || []), ...(manifest.missing || [])].map(text => '- ' + md(text)).join('\n') || '本次记录没有报告异常；此结论只覆盖已有观测。',
    artifacts: manifest.artifacts.map(item => `- ${md(item.name)}，${item.size} 字节，SHA-256：${md(item.sha256)}`).join('\n'),
  };
  const template = fs.readFileSync(fileURLToPath(new URL('../resources/reports/flight-report-v1.md', import.meta.url)), 'utf8');
  return template.replace(/\{\{(\w+)\}\}/g, (_match, key) => values[key] ?? '缺失');
}
export async function generateFlightReport(coordinator, id) {
  if (coordinator.locks.has(id)) throw new Error('本次飞行已有操作正在执行');
  coordinator.locks.add(id);
  const flight = coordinator.get(id);
  try {
    if (!['landed', 'completed'].includes(flight.phase)) throw new Error('必须先核对实际落地及解除解锁，不能生成成功飞行报告');
    flight.phase = 'collecting'; coordinator.save(flight);
    const manifest = await coordinator.call(flight, 'collect_flight_logs', { flight_id: id });
    if (manifest.flightId !== id || manifest.vehicleId !== flight.status.vehicleId || !Array.isArray(manifest.artifacts)) throw new Error('日志清单与本次飞行不匹配');
    const dir = path.join(coordinator.dir, id, 'artifacts'); fs.mkdirSync(dir, { recursive: true });
    for (const item of manifest.artifacts) {
      if (!Number.isInteger(item.size) || item.size < 0 || item.size > 256 * 1024 * 1024 || !/^[a-f0-9]{64}$/.test(item.sha256)) throw new Error('日志清单大小或校验值无效');
      const file = path.join(dir, item.id === 'telemetry' ? 'telemetry.json' : `${item.sha256}.ulg`), temporary = `${file}.part`;
      const hash = createHash('sha256'); let offset = 0;
      const descriptor = fs.openSync(temporary, 'w');
      try {
        while (offset < item.size) {
          const part = await coordinator.call(flight, 'read_log_chunk', { flight_id: id, log_id: item.id, offset, length: Math.min(262144, item.size - offset) });
          const buffer = Buffer.from(part.data || '', 'base64');
          if (part.offset !== offset || !buffer.length || buffer.length !== part.length || offset + buffer.length > item.size) throw new Error('日志片段不完整或顺序不匹配');
          fs.writeSync(descriptor, buffer); hash.update(buffer); offset += buffer.length;
        }
      } finally { fs.closeSync(descriptor); }
      if (hash.digest('hex') !== item.sha256) throw new Error('日志 SHA-256 校验失败');
      fs.renameSync(temporary, file); item.localFile = file;
    }
    const markdown = path.join(dir, 'flight-report.md'), docx = path.join(dir, 'flight-report.docx');
    flight.phase = 'reporting'; coordinator.save(flight);
    fs.writeFileSync(markdown, reportMarkdown(flight, manifest), 'utf8');
    flight.report = { md: markdown, log: manifest.artifacts.find(item => item.id !== 'telemetry')?.localFile || manifest.artifacts[0]?.localFile,
      artifacts: manifest.artifacts, generatedAt: coordinator.now(), templateVersion: 'px4-flight-report-v1' };
    coordinator.save(flight);
    const bundledReference = fileURLToPath(new URL('../resources/reports/reference.docx', import.meta.url)), reference = path.join(dir, 'reference.docx');
    if (fs.existsSync(bundledReference)) fs.copyFileSync(bundledReference, reference);
    await run(findPandoc(coordinator.store), [markdown, '-o', docx, '--standalone', '--resource-path', dir,
      ...(fs.existsSync(reference) ? ['--reference-doc', reference] : [])], { timeout: 60000, windowsHide: true });
    if (!fs.existsSync(docx)) throw new Error('Pandoc 未生成 DOCX');
    flight.report.docx = docx; flight.phase = 'completed'; flight.report.error = null;
    coordinator.event(flight, 'report_generated', { templateVersion: flight.report.templateVersion }); coordinator.save(flight);
    return flight;
  } catch (error) {
    if (['collecting', 'reporting'].includes(flight.phase)) flight.phase = 'landed';
    if (flight.report) flight.report.error = error.message;
    flight.notices.push(`报告阶段未完成：${error.message}。可单独重试，不重新飞行。`); coordinator.save(flight); throw error;
  } finally { coordinator.locks.delete(id); }
}
