import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const webRoot = path.join(root, 'apps', 'web', 'public');
const errors = [];
const missing = [];
const exists = (file) => fs.existsSync(path.join(root, file));
const report = (kind, file) => (exists(file) ? null : missing.push(`${kind}: ${file}`));

const patterns = JSON.parse(fs.readFileSync(path.join(root, 'packages/catalogs/src/pattern-options.v1.json'), 'utf8'));
for (const option of patterns.options) report('pattern thumbnail', option.thumbnail.replace(/^\//, 'apps/web/public/').replace(/\.svg$/, `.${patterns.asset_extension || 'svg'}`));

const fabrics = JSON.parse(fs.readFileSync(path.join(root, 'packages/catalogs/src/fabrics.v1.json'), 'utf8'));
for (const [family, groups] of Object.entries(fabrics.groups)) {
  for (const [group, values] of Object.entries(groups)) {
    for (const [slug] of values) report('fabric swatch', `apps/web/public/ui-assets/v1/fabric-options/${family}/${group}/${slug}/swatch.png`);
  }
}

const processes = JSON.parse(fs.readFileSync(path.join(root, 'packages/catalogs/src/processes.v1.json'), 'utf8'));
for (const item of processes.processes) report('process thumbnail', item.thumbnail.replace(/^\//, 'apps/web/public/'));

const ruleReady = fs.existsSync(path.join(root, 'data', 'ir', 'v1_rule_ready'))
  ? path.join(root, 'data', 'ir', 'v1_rule_ready')
  : path.join(root, '_handoff_pack', 'v1_rule_ready');
const imageRoot = path.join(root, 'data', 'seed', 'r2', 'chi27-catalog', 'v1', 'references');
if (fs.existsSync(ruleReady)) {
  const cases = fs.readdirSync(ruleReady).filter((name) => name.endsWith('.rule-ready.json')).map((name) => name.replace('.rule-ready.json', ''));
  for (const caseId of cases) {
    const dir = path.join(imageRoot, caseId);
    const candidates = ['cover.png', 'cover.jpg', 'cover.jpeg', 'cover.webp'];
    if (!candidates.some((name) => fs.existsSync(path.join(dir, name)))) {
      missing.push(`reference image: ${path.relative(root, path.join(dir, 'cover.{png|jpg|jpeg|webp}'))}`);
    }
  }
}

console.log(`Catalog version: ${patterns.version}`);
console.log(`Pattern options: ${patterns.options.length}`);
console.log(`Fabric options: ${Object.values(fabrics.groups).flatMap((groups) => Object.values(groups).flat()).length}`);
console.log(`Process options: ${processes.processes.length}`);
if (missing.length) {
  console.log(`\nMissing user-supplied assets (${missing.length}):`);
  for (const item of missing) console.log(`- ${item}`);
  console.log('\nPlace the files according to docs/ASSET_INTAKE.md.');
  if (process.argv.includes('--strict')) process.exitCode = 1;
} else {
  console.log('All catalog assets are present.');
}
