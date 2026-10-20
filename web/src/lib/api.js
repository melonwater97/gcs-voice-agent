import { nextMsgId } from './msgId.js';

const FRONT = { type: 'FRONT', id: 'GS-01' };
const AGENT = { type: 'BACKGROUND', id: 'AGENT-01' };
const RESPONSE_TYPES = {
  VOICE_INPUT: 'AGENT_OUTPUT', AGENT_INPUT: 'AGENT_OUTPUT',
  RECORD_START: 'RECORD_START_OUTPUT', RECORD_STATUS: 'RECORD_STATUS_OUTPUT',
  STATE_QUERY: 'STATE_OUTPUT', HEALTH_QUERY: 'HEALTH_OUTPUT',
};
const MSG_ID = /^MSG-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function makeMessage(msgType, payload, msgId = nextMsgId()) {
  return { header: { msg_id: msgId, msg_type: msgType, version: '1.0.0',
    src: FRONT, dst: AGENT }, payload };
}

function validateMessage(body, message, allowedTypes) {
  const header = body?.header;
  if (!header || !MSG_ID.test(header.msg_id) || header.version !== '1.0.0'
      || !allowedTypes.includes(header.msg_type)
      || header.src?.id !== AGENT.id || header.src?.type !== AGENT.type
      || header.dst?.id !== FRONT.id || header.dst?.type !== FRONT.type
      || !body.payload || typeof body.payload !== 'object' || Array.isArray(body.payload)) {
    throw Object.assign(new Error('Agent 返回的消息结构无效。'), { retryable: true });
  }
  return body;
}

export async function readVoiceStream(response, message, onTranscript = () => {}) {
  if (!response.headers.get('content-type')?.startsWith('application/x-ndjson') || !response.body) {
    throw Object.assign(new Error('Agent 未返回语音分阶段响应。'), { retryable: true });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', transcribed = false, completed = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        let frame;
        try { frame = JSON.parse(line); }
        catch { throw Object.assign(new Error('Agent 语音响应格式无效。'), { retryable: true }); }
        validateMessage(frame, message, ['ASR_OUTPUT', 'AGENT_OUTPUT', 'ERROR']);
        if (frame.header.msg_type === 'ERROR') {
          throw Object.assign(new Error(frame.payload.error || 'Agent 语音处理失败。'), { retryable: true });
        }
        if (frame.header.msg_type === 'ASR_OUTPUT') {
          if (transcribed || typeof frame.payload.text !== 'string' || !frame.payload.text.trim()) {
            throw Object.assign(new Error('Agent 转写响应无效。'), { retryable: true });
          }
          transcribed = true;
          onTranscript(frame);
        } else {
          if (!transcribed) throw Object.assign(new Error('Agent 缺少语音转写响应。'), { retryable: true });
          completed = true;
          return frame;
        }
      }
      if (done) throw Object.assign(new Error('语音响应中断，尚未收到处理结果；请查看任务状态或重试。'), { retryable: true });
    }
  } finally {
    if (!completed) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

async function request(path, message, timeout = 190000, onTranscript) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(message), signal: controller.signal, cache: 'no-store' });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const error = new Error(body?.payload?.error || `服务请求失败（${response.status}），请检查 Agent 是否已启动。`);
      error.retryable = [502, 503, 504].includes(response.status);
      throw error;
    }
    if (message.header.msg_type === 'VOICE_INPUT' || message.header.msg_type === 'RECORD_STOP') {
      return await readVoiceStream(response, message, onTranscript);
    }
    return validateMessage(await response.json().catch(() => null), message,
      [RESPONSE_TYPES[message.header.msg_type]]);
  } catch (error) {
    if (error.name === 'AbortError') throw Object.assign(new Error('请求超时。任务可能仍在处理中，请查看任务面板，避免重复发送。'), { retryable: true });
    if (error instanceof TypeError) throw Object.assign(new Error('无法连接 Agent，请检查 Agent 服务是否已启动。'), { retryable: true });
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export const startRecord = msgId => request('/api/record/start', makeMessage('RECORD_START', {}, msgId), 10000);
export const stopRecord = (msgId, onTranscript) => request('/api/record/stop', makeMessage(
  'RECORD_STOP', {}, msgId), 190000, onTranscript);
export const getRecordStatus = msgId => request('/api/record/status', makeMessage('RECORD_STATUS', {}, msgId), 5000);
export const sendText = (text, msgId) => request('/api/text', makeMessage('AGENT_INPUT', { text }, msgId));
export const getState = async () => (await request('/api/state', makeMessage('STATE_QUERY', {}), 5000)).payload;
export const getHealth = async () => (await request('/health', makeMessage('HEALTH_QUERY', {}), 5000)).payload;
