import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';

const sourcePath = new URL('./compositionDraft.ts', import.meta.url);
const source = readFileSync(sourcePath, 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const outPath = join(tmpdir(), `compositionDraft.${process.pid}.mjs`);
await import('node:fs/promises').then(({ writeFile }) => writeFile(outPath, compiled));
const { hasDraftPatternChanges, submitPatternDraft } = await import(pathToFileURL(outPath));

const submitted = { neckline: 'tshirt.neckline.crew', sleeve: 'tshirt.sleeve.set-in', garment_length: 'tshirt.garment-length.regular' };
assert.equal(hasDraftPatternChanges({ ...submitted }, submitted), false);
assert.equal(hasDraftPatternChanges({ ...submitted, neckline: 'tshirt.neckline.v-neck' }, submitted), true);
const generated = submitPatternDraft({ ...submitted, sleeve: 'tshirt.sleeve.puff' });
assert.deepEqual(generated, { ...submitted, sleeve: 'tshirt.sleeve.puff' });
assert.notEqual(generated, submitted);
console.log('compositionDraft tests passed');
