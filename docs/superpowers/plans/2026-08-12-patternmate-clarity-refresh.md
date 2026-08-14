# PatternMate Clarity Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove unnecessary PatternMate UI elements and make the existing garment-pattern workflow clear, compact, and visually consistent without changing backend or geometry behavior.

**Architecture:** Keep the current React page flow and component boundaries. Remove redundant JSX at its source, consolidate status presentation, then replace conflicting presentation rules in `overrides.css` with one coherent warm-studio system; do not add another permanent override stylesheet.

**Tech Stack:** React 18, TypeScript 5, Vite 4, plain CSS, Node test scripts.

## Global Constraints

- Do not modify geometry services, GPU services, DXF data, composition rules, or request/response shapes.
- Do not add a UI framework or runtime dependency.
- Preserve Chinese and English behavior.
- Each screen has at most one visually primary action.
- Failed component replacement must remain distinguishable from a retained current component and from total composition failure.
- Use the tokens and deletion decisions in `docs/superpowers/specs/2026-08-12-patternmate-clarity-refresh-design.md`.
- This directory is not currently a Git repository; skip commit steps unless the user initializes Git before execution.

---

### Task 1: Add source-level UI regression checks ✅

**Files:**
- Create: `src/uiClarity.test.mjs`
- Modify: `package.json`

**Interfaces:**
- Consumes: source text from `src/main.tsx`, `src/PatternPreview.tsx`, and `src/CompositionReviewPanel.tsx`.
- Produces: `pnpm test:ui`, a dependency-free structural guard for removed clutter and required status labels.

- [ ] **Step 1: Write a failing source-structure test**

Create `src/uiClarity.test.mjs` using `node:assert/strict` and `node:fs`. Assert that the source no longer contains `home-pixel-art`, `home-gallery-track`, `side-heading"><span className="dot"`, `点击收起/展开`, or the disabled label `当前预览已是最新`. Assert that `CompositionReviewPanel.tsx` contains labels for `已替换`, `保留原部件`, `等待审核`, and `失败`.

- [ ] **Step 2: Add and run the UI test command**

Add `"test:ui": "node src/uiClarity.test.mjs"` and change `test` to run both the existing composition test and UI clarity test. Run `pnpm test:ui` and expect failure on the current redundant elements.

- [ ] **Step 3: Keep the test narrowly scoped**

Verify the test checks user-visible structure and wording only; it must not snapshot full TSX files or CSS formatting.

### Task 2: Simplify the global shell and home screen ✅

**Files:**
- Modify: `src/main.tsx`
- Modify: `src/overrides.css`
- Test: `src/uiClarity.test.mjs`

**Interfaces:**
- Consumes: existing `showHome`, `steps`, project-name, language, export, and service status state.
- Produces: the same navigation callbacks and application state with fewer visual nodes.

- [ ] **Step 1: Remove decorative home nodes**

Remove `.home-pixel-art`, the animated `.home-gallery` section, and `scrollingGallery`. Keep the brand, a two-line description, language picker, and one `开始设计 / Start designing` action.

- [ ] **Step 2: Simplify top navigation markup**

Remove step image icons and the sidebar heading dot. Render each step with a numerical `aria-hidden` marker and label. Keep disabled navigation behavior unchanged.

- [ ] **Step 3: Consolidate global notices**

Derive one highest-priority global message in this order: export error, analysis error, design-service connection. Render one `role="status"` or `role="alert"` strip instead of three independent banners.

- [ ] **Step 4: Remove premature global export emphasis**

Change the topbar export button from `.export` primary styling to a low-emphasis text action, or hide it until `stylingConfirmed`; leave the existing `exportAll` callback untouched.

- [ ] **Step 5: Implement the warm minimal shell styles**

In the final clarity section of `overrides.css`, define semantic CSS custom properties, neutral page surfaces, a 64 px topbar, restrained navigation, visible focus rings, and reduced-motion handling. Delete obsolete home-gallery and pixel-art overrides rather than shadowing them.

- [ ] **Step 6: Verify Task 2**

Run `pnpm test:ui` and `pnpm build`. Expect the home/shell assertions to pass and TypeScript/Vite build to complete.

### Task 3: Reduce measurement and design-page repetition ✅

**Files:**
- Modify: `src/main.tsx`
- Modify: `src/overrides.css`
- Test: `src/uiClarity.test.mjs`

**Interfaces:**
- Consumes: measurement state, avatar generation callbacks, design intent conversation state, and reference selection.
- Produces: unchanged measurement and design request behavior with progressively disclosed help.

- [ ] **Step 1: Collapse detailed measurement help**

Remove the duplicate left measurement paragraph. Keep a short line above fields. In `MeasureCanvas`, keep the diagram and replace the always-visible methods section with `<details className="measurement-methods">` whose summary is `详细测量方法 / Detailed measurement methods`.

- [ ] **Step 2: Remove design-assistant wrapper clutter**

Remove `.design-guidance`. Render `.design-conversation` only when it has messages. Show `.analysis-mode` only when `analysisMode === 'rules'` or the service is unavailable.

- [ ] **Step 3: Merge design preferences**

Replace the divider, muted instruction, tags, saved requirements, conditional sleeve notes, and selected-category heading with one compact preferences section. Preserve all conditions and callbacks, but avoid repeating headings and family state.

- [ ] **Step 4: Simplify reference cards**

Remove the always-visible `查看详情 / View details` state. Provide a visible selected marker only for the selected reference and preserve the `aria-label` and modal behavior.

- [ ] **Step 5: Verify Task 3**

Run `pnpm test`, `pnpm build`, then manually confirm measurement persistence, avatar gating, intent submission, image attachment, preference editing, and reference selection.

### Task 4: Make component selection the primary Pattern Mix task ✅

**Files:**
- Modify: `src/main.tsx`
- Modify: `src/overrides.css`
- Test: `src/uiClarity.test.mjs`

**Interfaces:**
- Consumes: `selections`, `setSelection`, material/process state, `hasDraftPatternChanges`, `ready`, and `onNext`.
- Produces: identical composition recipe changes and submission timing.

- [ ] **Step 1: Merge context notices**

Replace `.family-lock` and `.base-pattern-note` with one short `.pattern-context` that names the category and states that unchanged components come from the selected base pattern.

- [ ] **Step 2: Remove redundant disclosure copy**

Remove every `点击收起/展开 / Collapse / expand` span. Keep native details/summary affordances and current open defaults for the component section only.

- [ ] **Step 3: Reduce option-card content**

Keep thumbnail, name, selected outline, and a text selected marker. Do not show descriptions inside component option cards. Preserve fabric compatibility warnings only for the active selected item.

- [ ] **Step 4: Show only actionable preview control**

Render the secondary `生成组合预览` button only when `hasDraftPatternChanges` is true. Place a short readiness explanation beside the main action; keep the main label stable instead of embedding every disabled reason in it.

- [ ] **Step 5: Verify Task 4**

Run `pnpm test` and `pnpm build`. Manually select neckline, sleeve, garment length, cuff, fabric, and process values and confirm recipe submission remains unchanged.

### Task 5: Simplify DXF controls and make validation dominant ✅

**Files:**
- Modify: `src/PatternPreview.tsx`
- Modify: `src/CompositionReviewPanel.tsx`
- Modify: `src/overrides.css`
- Test: `src/uiClarity.test.mjs`

**Interfaces:**
- Consumes: existing result validation, component results, review ledger, layer state, piece list, paper info, and export callback.
- Produces: explicit component display states `applied`, `retained_current`, `review_required`, and `failed`; no backend schema changes.

- [ ] **Step 1: Shorten the preview title**

Use `纸样预览 / Pattern preview`, `设计预览 / Design preview`, or `3D 试穿 / 3D try-on`, followed only by garment family. Do not repeat `完整`, `人体适配`, and `试样` in the same title.

- [ ] **Step 2: Group low-frequency display controls**

Wrap layer and construction-line controls in one `<details className="display-controls">` with summary `显示 / Display`. Preserve all toggle and hover behavior.

- [ ] **Step 3: Keep validation visible and move metadata into details**

Keep the automatic validation state and warnings outside disclosure. Move piece list and paper information into one `详细信息 / Details` disclosure. Keep export below validation.

- [ ] **Step 4: Normalize review status presentation**

Map applied results to `已替换 / Applied`, retained results to `保留原部件 / Kept original`, review-required results to `等待审核 / Review required`, and validation failure to `失败 / Failed`. Put modified-entity and donor counts inside per-operation `<details>` elements.

- [ ] **Step 5: Simplify failure overlays**

Each failure message must show the affected component, whether the previous valid state was retained, and one recovery action. Preserve `lastValid`, replacement-candidate, and selection callbacks.

- [ ] **Step 6: Verify Task 5**

Run `pnpm test`, `pnpm build`, and `pnpm test:geometry` when the local geometry environment is available. Manually verify applied, retained, review-required, invalid-base, and invalid-replacement states.

### Task 6: Reduce print-page instruction noise ✅

**Files:**
- Modify: `src/PrintDesign.tsx`
- Modify: `src/GarmentPreview.tsx`
- Modify: `src/overrides.css`

**Interfaces:**
- Consumes: current print mode, view, selected placement, asset list, settings, and export callback.
- Produces: unchanged print editing and production export behavior.

- [ ] **Step 1: Make help contextual**

Render editor help only for manual mode with no active placement, density mode before interaction, or an empty side. Do not show the full gesture legend continuously.

- [ ] **Step 2: Flatten empty states**

Replace `.print-control-card.no-print-card` with a plain compact empty state. Preserve front/back independent-mode behavior.

- [ ] **Step 3: Normalize print controls**

Apply the same field, button, selected, focus, and spacing tokens used elsewhere; retain all current controls and numeric bounds.

- [ ] **Step 4: Verify Task 6**

Run `pnpm test` and `pnpm build`. Manually verify front/back switching, density settings, manual placement move/rotate/scale/crop, undo/redo, asset upload, and export.

### Task 7: Responsive and accessibility verification ✅

**Files:**
- Modify: `src/overrides.css`
- Modify if required by findings: `src/main.tsx`, `src/PatternPreview.tsx`, `src/PrintDesign.tsx`

**Interfaces:**
- Consumes: completed simplified UI.
- Produces: the same workflows at 1440, 1024, 768, and 375 px widths with accessible focus and motion behavior.

- [ ] **Step 1: Verify desktop layouts**

At 1440 and 1024 px, inspect every step for one primary action, readable canvas width, no overlapping notices, and no unintended horizontal overflow.

- [ ] **Step 2: Verify narrow layouts**

At 768 and 375 px, stack sidebar and canvas, turn the step navigation into a horizontally scrollable compact row, disable column resize affordances, and keep all controls at least 40 px high.

- [ ] **Step 3: Verify keyboard and motion behavior**

Tab through navigation, inputs, cards, disclosures, view toggles, validation details, and modals. Confirm focus visibility and Escape behavior where already supported. Emulate reduced motion and ensure galleries, transitions, and spinners do not create unnecessary movement.

- [ ] **Step 4: Run final automated verification**

Run `pnpm test`, `pnpm build`, and—when available—`pnpm test:geometry`. Record any skipped geometry check with the exact missing local prerequisite.

- [ ] **Step 5: Update the usage guide**

Modify the existing PatternMate usage documentation to describe the simplified screens, status meanings, and where detailed technical information is now located.
