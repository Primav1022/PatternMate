# Shirt Pattern IR 数据集 — 二次开发 Handoff 包（清理版）

> **版本**：v2（基于 31 个衬衫案例，已去 tshirt 残留）
> **位置**：`ir_corpus_0807/final_shirt_dataset_31/`
> **生成时间**：2026-08-10

---

## 0. 数据来源 / 整理规则

| 来源 | 旧路径 | 处理 |
|---|---|---|
| `final_shirt_dataset_28` (31 件) | `final_shirt_dataset_28_legacy/` | 已归档 |
| `final_shirt_dataset_6` (6 件) | `final_shirt_dataset_6_legacy/` | 已归档（6 件与 28 完全重叠） |

合并去重后 = **31 件衬衫**（旧 28 的全集），其中：
- **15 件**原本就标 `shirt`
- **16 件**原本错标 `tshirt`，本版本统一改 `shirt`

`category` 字段已全部为 `shirt`，无 tshirt 残留。

---

## 1. 目录结构

```
final_shirt_dataset_31/
├── pattern_ir/        31 份 *.pattern-ir.json（category 全=shirt）
├── sample_images/     样衣图
├── part_labels/       部件标签
├── INDEX.csv          31 行快速导航
└── HANDOFF.md         本文件
```

---

## 2. 数字汇总

| 指标 | 值 |
|---|---|
| 总数 | **31** |
| category=shirt | **31 / 31** ✅ |
| category=tshirt 残留 | **0** ✅ |
