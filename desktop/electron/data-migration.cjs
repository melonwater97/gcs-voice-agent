const fs = require('node:fs');
const path = require('node:path');
const { createCipheriv, createDecipheriv, randomBytes } = require('node:crypto');

function encrypt(value, key) {
  const iv = randomBytes(12), cipher = createCipheriv('aes-256-gcm', key, iv);
  const data = Buffer.concat([cipher.update(JSON.stringify(value), 'utf8'), cipher.final()]);
  return { iv: iv.toString('base64'), data: data.toString('base64'), tag: cipher.getAuthTag().toString('base64') };
}
function decrypt(value, key) {
  const cipher = createDecipheriv('aes-256-gcm', key, Buffer.from(value.iv, 'base64'));
  cipher.setAuthTag(Buffer.from(value.tag, 'base64'));
  return JSON.parse(Buffer.concat([cipher.update(Buffer.from(value.data, 'base64')), cipher.final()]).toString('utf8'));
}
function migrateWebData(sourceDir, targetDir, destinationKey) {
  if (!sourceDir || path.resolve(sourceDir) === path.resolve(targetDir)) return false;
  const sourceConfig = path.join(sourceDir, 'settings.enc.json'), targetConfig = path.join(targetDir, 'settings.enc.json');
  if (fs.existsSync(targetConfig) || !fs.existsSync(sourceConfig) || !fs.existsSync(path.join(sourceDir, 'development.key'))) return false;
  const config = decrypt(JSON.parse(fs.readFileSync(sourceConfig, 'utf8')), fs.readFileSync(path.join(sourceDir, 'development.key')));
  fs.mkdirSync(targetDir, { recursive: true });
  for (const name of ['sessions', 'knowledge', 'requests', 'pi', 'python-tasks']) {
    const source = path.join(sourceDir, name), destination = path.join(targetDir, name);
    if (fs.existsSync(source)) fs.cpSync(source, destination, { recursive: true, force: false, errorOnExist: false, filter: entry => !fs.lstatSync(entry).isSymbolicLink() });
  }
  const sessions = path.join(targetDir, 'sessions');
  if (fs.existsSync(sessions)) for (const name of fs.readdirSync(sessions).filter(name => name.endsWith('.json'))) {
    const file = path.join(sessions, name), session = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (session.piFile) {
      const relative = path.relative(path.join(sourceDir, 'pi'), session.piFile);
      if (!relative.startsWith('..') && !path.isAbsolute(relative)) session.piFile = path.join(targetDir, 'pi', relative);
    }
    fs.writeFileSync(file, JSON.stringify(session, null, 2), { mode: 0o600 });
  }
  // Write the encrypted settings last so an interrupted import can be retried.
  const temp = targetConfig + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(encrypt(config, Buffer.from(destinationKey, 'base64'))), { mode: 0o600 });
  fs.renameSync(temp, targetConfig);
  fs.writeFileSync(path.join(targetDir, 'web-import.json'), JSON.stringify({ importedAt: new Date().toISOString(), version: 1 }));
  return true;
}
module.exports = { migrateWebData };
