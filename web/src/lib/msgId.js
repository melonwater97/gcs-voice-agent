export function nextMsgId() {
  return `MSG-${crypto.randomUUID()}`;
}
