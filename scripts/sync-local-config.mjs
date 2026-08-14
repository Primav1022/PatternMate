import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'config', 'model.local.json');
if (!fs.existsSync(source)) {
  console.error('Missing config/model.local.json. Copy model.local.example.json first.');
  process.exit(1);
}
const config = JSON.parse(fs.readFileSync(source, 'utf8'));
const workerVars = [
  `MODEL_ENABLED=${config.model?.enabled ? 'true' : 'false'}`,
  `MODEL_PROVIDER=${config.model?.provider || ''}`,
  `MODEL_BASE_URL=${config.model?.baseUrl || ''}`,
  `MODEL_NAME=${config.model?.name || ''}`,
  `MODEL_API_KEY=${config.model?.apiKey || ''}`,
  `GEOMETRY_SERVICE_URL=${config.geometryBaseUrl || 'http://127.0.0.1:8788'}`
].join('\n') + '\n';
fs.writeFileSync(path.join(root, 'apps', 'worker', '.dev.vars'), workerVars, 'utf8');
fs.writeFileSync(path.join(root, 'apps', 'web', '.env.local'), `VITE_API_BASE_URL=${config.apiBaseUrl || 'http://127.0.0.1:8787'}\nVITE_GEOMETRY_BASE_URL=${config.geometryBaseUrl || 'http://127.0.0.1:8788'}\n`, 'utf8');
console.log('Synced local model/API config to apps/worker/.dev.vars and apps/web/.env.local');
