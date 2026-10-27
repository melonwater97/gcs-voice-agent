export async function request(path, method = 'GET', body) {
  if (window.linghang) return window.linghang.request(path, method, body);
  const response = await fetch(path, { method, ...(body !== undefined ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}
export function subscribe(callback, onError) {
  if (window.linghang) return window.linghang.subscribe(callback);
  const source = new EventSource('/api/events');
  let opened = false;
  source.onopen = () => { if (opened) callback({ type: 'reconnected' }); opened = true; };
  source.onmessage = message => callback(JSON.parse(message.data));
  source.onerror = () => onError?.();
  return () => source.close();
}
export function download(name, content) {
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
