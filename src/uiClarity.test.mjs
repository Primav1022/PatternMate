import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const main = readFileSync(new URL('./main.tsx', import.meta.url), 'utf8');
const preview = readFileSync(new URL('./PatternPreview.tsx', import.meta.url), 'utf8');
const review = readFileSync(new URL('./CompositionReviewPanel.tsx', import.meta.url), 'utf8');

for (const redundantElement of [
  'side-heading"><span className="dot"',
  '点击收起/展开',
  '当前预览已是最新',
  'pattern-context',
]) {
  assert.ok(!main.includes(redundantElement), `remove redundant UI: ${redundantElement}`);
}

assert.ok(main.includes('home-gallery'), 'landing keeps inspiration gallery');
assert.ok(main.includes('home-logo'), 'landing lockup includes system logo');
assert.ok(!main.includes('home-pixel-art'), 'landing background pixel art removed');
assert.ok(preview.includes('pattern-download'), 'DXF download stays on pattern stage');
assert.ok(!preview.includes('pattern-inspector'), 'pattern inspector panel removed from styling canvas');
assert.ok(main.includes('column-resizer'), 'workspace column resizer available');
assert.ok(!main.includes("execution_mode: recipe.family"), 'recipe initialization must not read itself');

for (const status of ['已替换', '保留原部件', '等待审核', '失败']) {
  assert.ok(review.includes(status), `review panel exposes status: ${status}`);
}

console.log('PatternMate UI clarity checks passed.');
