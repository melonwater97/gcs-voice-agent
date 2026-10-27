import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYAML } from 'yaml';
import { atomicJSON, readJSON, uid } from './store.mjs';

export function terms(text) { return (text.toLowerCase().match(/[a-z0-9_]+|[\u3400-\u9fff]/g) || []); }
export function chunks(text, source, page = null) {
  const result = [];
  for (let start = 0; start < text.length; start += 650) {
    const content = text.slice(start, start + 850).trim();
    if (content) result.push({ source, page, offset: start, text: content });
  }
  return result;
}
export class Knowledge {
  constructor(store) { this.store = store; this.file = path.join(store.dir, 'knowledge', 'index.json'); this.docs = readJSON(this.file, []); }
  list() { return this.docs.map(({ chunks: _, ...metadata }) => metadata); }
  async add(name, base64) {
    const bytes = Buffer.from(base64, 'base64');
    if (!bytes.length || bytes.length > 20 * 1024 * 1024) throw new Error('资料应为 1 字节至 20 MiB');
    const ext = path.extname(name).toLowerCase();
    const doc = { id: uid(), name: path.basename(name), enabled: true, status: 'ready', addedAt: new Date().toISOString(), size: bytes.length, chunks: [] };
    if (['.txt', '.md', '.markdown'].includes(ext)) doc.chunks = chunks(bytes.toString('utf8'), doc.name);
    else if (ext === '.pdf') {
      const { getDocument } = await import('pdfjs-dist/legacy/build/pdf.mjs');
      const pdf = await getDocument({ data: new Uint8Array(bytes), useSystemFonts: true, isEvalSupported: false }).promise;
      try {
        if (pdf.numPages > 1000) throw new Error('首版支持不超过 1000 页的 PDF');
        for (let page = 1; page <= pdf.numPages; page++) {
          const content = await (await pdf.getPage(page)).getTextContent();
          doc.chunks.push(...chunks(content.items.map(x => x.str + (x.hasEOL ? '\n' : ' ')).join(''), doc.name, page));
        }
      } finally { await pdf.destroy(); }
    } else throw new Error('当前支持 Markdown、TXT、文本 PDF');
    if (!doc.chunks.length) throw new Error('未能提取文本；扫描资料需要先做 OCR');
    doc.chunkCount = doc.chunks.length;
    this.docs.push(doc); this.persist(); return this.list().find(d => d.id === doc.id);
  }
  persist() { atomicJSON(this.file, this.docs); }
  toggle(id, enabled) { const doc = this.docs.find(d => d.id === id); if (!doc) throw new Error('资料不存在'); doc.enabled = Boolean(enabled); this.persist(); }
  remove(id) { this.docs = this.docs.filter(d => d.id !== id); this.persist(); }
  search(query, limit = 5) {
    const q = [...new Set(terms(query))];
    if (!q.length) return [];
    const all = this.docs.filter(d => d.enabled).flatMap(d => d.chunks.map((c, i) => ({ ...c, citation: `[${d.name}${c.page ? ` · 第${c.page}页` : ` · 片段${i + 1}`}]`, documentId: d.id })));
    return all.map(c => {
      const words = terms(c.text), counts = new Map();
      words.forEach(t => counts.set(t, (counts.get(t) || 0) + 1));
      const score = q.reduce((s, term) => s + (counts.has(term) ? 1 + Math.log(counts.get(term)) : 0), 0) / Math.sqrt(Math.max(1, words.length / 150));
      return { ...c, score };
    }).filter(c => c.score > 0).sort((a, b) => b.score - a.score).slice(0, Math.min(10, limit));
  }
}
export function readSkill(directory) {
  const real = fs.realpathSync(directory), file = path.join(real, 'SKILL.md');
  const stat = fs.statSync(file);
  if (stat.size > 256 * 1024) throw new Error('SKILL.md 超过 256 KiB');
  const content = fs.readFileSync(file, 'utf8');
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(content);
  if (!match) throw new Error('Skill 需要包含 name、description 的 YAML 元数据');
  const metadata = parseYAML(match[1]);
  if (typeof metadata?.name !== 'string' || typeof metadata.description !== 'string' || !metadata.name.trim() || !metadata.description.trim()) throw new Error('Skill 缺少名称或描述');
  return { directory: real, name: metadata.name, description: metadata.description, compatibility: metadata.compatibility || '', content, file };
}
