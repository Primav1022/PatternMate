# 衬衫组合主线（已锁定）

**Pipeline id:** `shirt.simple_piece_swap.v1`  
**IR baseline:** `shirt.dxf_fused_remix.v1`  
**代码:** `shirt_compose.py` + `shirt_strategy.py` + `shirt_side_seam.py` + `donor_similarity.py`  
**沙盒:** `#/shirt-sandbox`

T 恤已冻结（`TSHIRT_PIPELINE.md`）。衬衫定这一版，不再改规则换矩阵乱配。

## 用户输入（一次 compose）

1. **模板** `base_case_id` — 31 款锁定 remix IR 里选一件  
2. **要换的部件** `selections.collar / silhouette / placket / sleeve / cuff`（可空=不换）  
3. **衣长** `garment_length`（short / regular / long，只缩放）  
4. **身材** `sex` + `measurements_cm` + `fit` / `ease_cm`  
5. **面料** `material_id`（目前只进缩水修正，不换片）

**出:** 预览 SVG + `POST /export` DXF

## 规则（锁定）

| 操作 | 做法 |
|---|---|
| 选模板 | 只读 host 的 labeled 片；未归属 DXF 线留在 IR 里，**compose 丢掉** |
| 领型 + 前门襟 | **一起**整换前后衣身（领口/门襟都在衣身上，含领附件） |
| 廓形 | 只改前后片两条侧缝弧度（臂根接点锁住）。换完衣身后再改线 |
| 袖型 / 袖口 | 只换袖片 / 袖口片 |
| 袖片大小 | 仅换袖后：袖肥 ≈ 0.55×(前袖窿弧+后袖窿弧)，夹在衣身宽的 48–80%；袖长跟同一比例，且不低于袖窿深×1.2。不做袖山 morph |
| 衣长 / 衣宽 | 衣身纵向 / 横向比例（grading） |
| 袖长 / 颈围 | 袖长可再按身材微调；袖肥已跟袖窿走，不再用 upper_arm 二次拉宽 |
| 不做 | 袖山 morph、插肩连身肩、体型×面料乱序矩阵 |

## 这一版资料（31 款）

| 层 | 本地路径 | 来源 |
|---|---|---|
| **Compose 读的 IR** | `data/ir/shirt_v2/pattern_ir` → `pattern_ir_remix_v1/` | 融合锁定基线 |
| 融合底图几何 | 标注平台 `source_cases/{CASE}/dxf_entities.json` | 原 DXF 全量线 |
| 融合用的语义 IR | `pattern_ir_before_writeback/`（择优后的 writeback / 本地） | GPU 写回 + 本地备份 |
| Part labels | `data/ir/shirt_v2/part_labels/` + IR 内 `design_semantics_extra.part_labels` | 领/袖/袖口/门襟/廓形 |
| 导出对照 DXF | `data/seed/dxf/v1_annotated/{CASE}.annotated.dxf` | GPU annotated DXF |
| 清单 | `data/seed/dxf/shirt31_*_from_weste32902.tsv` | 拉取记录 |

几何挂在原 DXF 线上；IR 只把能对齐的片/线打上 `piece_id` / `piece_role` / `line_role`。对不上的线仍在 remix IR 里，组合预览不用。

## 和 GPU 怎么对齐

GPU（当时 weste:32902）上标注产物：

```
/root/autodl-tmp/ai4manufacturing_annotation/annotation_data/cases/{CASE}/generated-artifacts/
  ├── *.annotated.dxf          → 本地 v1_annotated/（31/31）
  └── *.pattern-ir.json        → 写回 IR，择优进 before_writeback（17 用写回、14 留本地）
```

对齐键是 **case_id**（C2431105 等）。本地 `CHI27_AI4Manufacturing/ir_corpus/` 有同样一份 annotated DXF / writeback IR 备份。

以后 GPU 上标注有更新：

1. 再拉 `annotated.dxf` + writeback `pattern-ir.json`  
2. `fuse_shirt_ir_dxf.py` 只写到 `pattern_ir_fused/`（禁止直接改 remix_v1）  
3. 确认后再**显式晋升**覆盖 `pattern_ir_remix_v1`

## 测试台

- UI：`#/shirt-sandbox`（先选模板，再换部件）  
- 验收矩阵：`tests/run_shirt_filtered_matrix.py`（3 模板 × 领/袖/袖口 = 9 格，筛过 donor）  
- 旧的 120 格乱配矩阵不再作为衬衫主线验收
