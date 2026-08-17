# PatternMate AAMA/R12 Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PatternMate's flat generic DXF output with structurally valid AAMA/R12 piece-block output that apparel CAD importers can recognize.

**Architecture:** Keep the existing `write_entities_dxf` public function and production endpoint. The writer groups optimized entities by `piece_id`, serializes one ASCII R12 block per piece with numeric manufacturing-function layers, and inserts each block once from model space. Focused tests parse the resulting group-code pairs rather than asserting source text.

**Tech Stack:** Python 3.13, `unittest`, FastAPI production endpoint, ASCII AutoCAD R12/AAMA-style DXF.

## Global Constraints

- Preserve all unrelated dirty-worktree changes.
- Do not emit `LWPOLYLINE`, custom `AI4M_*` layers, XDATA, or post-R12 header variables.
- One non-empty `piece_id` maps to one `BLOCK` and one `INSERT`.
- First delivery excludes `.rul` generation.
- Never write or print the GPU password outside ignored local environment files.
- Push only reviewed task-related changes, then deploy the pushed revision to the GPU.

---

### Task 1: AAMA/R12 Writer Contract

**Files:**
- Modify: `apps/geometry-service/tests/test_dxf_export.py`
- Modify: `_handoff_pack/scripts/dxf_export.py`

**Interfaces:**
- Consumes: `write_entities_dxf(entities: list[dict], path: str, *, piece_role_by_id: dict[str, str] | None = None, optimize: bool = True) -> dict`
- Produces: AAMA/R12 ASCII DXF and a report with `format`, block/insert/entity counts, skipped counts, and piece/block mappings.

- [ ] **Step 1: Write failing behavioral tests**

Add real-output tests that parse code/value pairs and assert the literal contract: `999/ANSI/AAMA`, `BLOCKS` then `ENTITIES`, two piece blocks and two inserts, numeric function layers, classic polylines, duplicate closing-point removal, no custom/post-R12 records, and rejection when no valid piece exists.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest apps/geometry-service/tests/test_dxf_export.py`

Expected: failures because the current writer emits `HEADER/TABLES`, no AAMA banner or blocks, custom layers, and accepts ungrouped/empty output.

- [ ] **Step 3: Implement the minimum compatible writer**

Refactor `_handoff_pack/scripts/dxf_export.py` into small private serializers for safe points, numeric layer selection, ASCII block names, geometry rows, block rows, and insert rows. Group by `piece_id`, skip invalid/ungrouped rows with counters, raise `ValueError` when no blocks remain, and return the specified report.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest apps/geometry-service/tests/test_dxf_export.py`

Expected: all exporter tests pass with no warnings.

### Task 2: Representative Composition Export

**Files:**
- Modify only if a newly exposed compatibility defect requires it: `apps/geometry-service/app.py`
- Test: `apps/geometry-service/tests/test_dxf_export.py`

**Interfaces:**
- Consumes: composition entities produced by the existing geometry pipeline.
- Produces: a representative export whose block/insert counts match its valid piece groups.

- [ ] **Step 1: Add a regression around realistic grouped entities**

Use a literal multi-piece fixture with boundary, grainline, notch, construction, seam, invalid-coordinate, and ungrouped entities. Assert report counts and a balanced pair structure.

- [ ] **Step 2: Run the regression and verify RED if behavior is missing**

Run: `python3 -m unittest apps/geometry-service/tests/test_dxf_export.py`

Expected: fail only for uncovered realistic-input behavior; if Task 1 already satisfies it, mutation-check the assertion by temporarily breaking the relevant serializer, observe failure, then restore.

- [ ] **Step 3: Make the smallest production adjustment**

Change only the exporter or narrow `/export` error boundary needed by the failing behavior. Do not refactor the composition pipeline.

- [ ] **Step 4: Run focused and geometry tests**

Run: `python3 -m unittest apps/geometry-service/tests/test_dxf_export.py`

Run: `cd apps/geometry-service && .venv/bin/python -m pytest tests/test_composition.py tests/test_preview_outline.py tests/test_dxf_export.py -q`

Expected: zero failures.

### Task 3: Server Address Cutover

**Files:**
- Modify: address-bearing tracked files identified by the server-switch dry run.
- Modify: ignored `.env` files for `REMOTE_*` credentials.
- Do not modify: `.env.example` with a real password.

**Interfaces:**
- New SSH endpoint: `root@connect.westd.seetacloud.com:25965`.
- New public base: `https://u1120192-44ru-e0d4d607.westd.seetacloud.com:8443/`.

- [ ] **Step 1: Dry-run the server-switch helper**

Provide the password through `SERVER_SWITCH_NEW_PASSWORD` via hidden stdin, run the bundled `update_server.py --dry-run`, and review every proposed file.

- [ ] **Step 2: Apply the reviewed address update**

Run the same helper without `--dry-run`. Spot-check `public/gpu.json` and ignored `.env` paths without printing password values.

- [ ] **Step 3: Verify SSH and public health**

Run a trivial authenticated SSH command and HTTPS health request. Report TLS or service failures exactly; do not disable certificate validation silently.

### Task 4: Full Verification, GitHub Push, and GPU Deploy

**Files:**
- Commit only the exporter, its tests, server address files, and the approved design/plan documents.

**Interfaces:**
- GitHub remote: existing `origin`.
- GPU deploy root: discover read-only over SSH before changing files.

- [ ] **Step 1: Run fresh local verification**

Run exporter tests, relevant geometry tests, `npm test`, `npm run build`, `git diff --check`, and a structural audit over a freshly generated representative DXF.

- [ ] **Step 2: Review and commit scoped changes**

Inspect the diff, stage only task files, and commit without including unrelated dirty files.

- [ ] **Step 3: Push the verified commit**

Run `git push origin main` and confirm the remote accepted the exact commit.

- [ ] **Step 4: Discover and update the GPU checkout**

Use authenticated SSH to locate the PatternMate checkout and process manager. Preserve remote-only configuration, pull the pushed commit, install only required dependencies, and restart the geometry service using its existing supervisor mechanism.

- [ ] **Step 5: Verify the deployed exporter**

Check remote health, call `/export` with a representative recipe, inspect the returned DXF for the AAMA banner, block/insert parity, R12-safe entities, and numeric layers, and report that proprietary-application opening remains the final manual gate.
