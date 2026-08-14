import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const source = path.join(root, '_handoff_pack', 'v1_rule_ready');
const target = path.join(root, 'data', 'ir', 'v1_rule_ready');
fs.mkdirSync(target, { recursive: true });
for (const file of fs.readdirSync(source).filter((name) => name.endsWith('.rule-ready.json'))) {
  fs.copyFileSync(path.join(source, file), path.join(target, file));
}
console.log(`Synced ${fs.readdirSync(target).length} rule-ready IR files to ${path.relative(root, target)}`);
