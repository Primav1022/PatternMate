# 衬衫 Remix IR 基线（已锁定）

**Baseline id:** `shirt.dxf_fused_remix.v1`  
**目录:** `pattern_ir_remix_v1/`（`pattern_ir` → 此目录）  
**Pipeline:** `shirt.simple_piece_swap.v1`

展示、compose、export、迁移都以这份为唯一基础。

## 内容

- 底图：`dxf_entities.json` 全量几何  
- 语义：能对齐的片/线带 `piece_id` / `piece_role` / `line_role`  
- 未归属线：留在 JSON 里；**compose 丢弃**，不进预览/换片

## 不要做的事

- 不要直接改 `pattern_ir_remix_v1/`  
- 重跑 fuse 只写 `pattern_ir_fused/`；确认后再显式晋升  
- 不要用 120 格乱序矩阵当主线验收；用 `run_shirt_filtered_matrix.py`（9 格）

## GPU 对齐

见 `apps/geometry-service/SHIRT_PIPELINE.md`：case_id 对齐 weste 上 `generated-artifacts/` 的 annotated DXF 与 writeback IR。
