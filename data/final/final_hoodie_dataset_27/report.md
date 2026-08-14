# 卫衣 27 件数据集 — 最终报告 (v1)

生成时间: 2026-08-09T23:39:14.586163

## 1. 数据集合并规则

按你确认的口径合并：

| 来源集合 | 原始 category | 数量 | 处理 |
|---|---|---|---|
| A 组 | hoodie | 6 | 直接保留 category=hoodie |
| B 组 | sweatshirt | 15 | sweatshirt 也算卫衣 → 改 category=hoodie |
| C 组 | tshirt / polo（GPU 误分类）| 6 | 人标"任务E·卫衣" → 改 category=hoodie |

**合计 27 件，category 统一为 hoodie**

## 2. 文件位置

```
ir_corpus_0807/final_hoodie_dataset_27/
├── IR/                    27 件 IR（已改 category，注入 needs_human_review）
├── part_labels/           27 件 part_labels（16 件真实标注 + 11 件占位）
├── merged_IR_copy/        27 件已并入 v1_pattern_ir_merged 的副本
├── needs_review/          needs_human_review.json（待人工标注清单）
├── manifest.json          数据集总清单
└── report.md              本报告

ir_corpus_0807/v1_pattern_ir_merged/
├── MANIFEST.json          tshirt/polo 等原 merge 结果
├── MANIFEST_HOODIE.json   卫衣 27 件 merge 结果 ← 新增
└── (134 个 IR 文件，含 27 件卫衣 + 107 件原 tshirt/polo)
```

## 3. 数字汇总

| 指标 | 值 |
|---|---|
| 卫衣总数 | **27** 件 |
| 改了 category | **27** 件 |
| 有真实 part_labels | **16** 件 |
| 占位 part_labels | **11** 件 |
| IR 通过（piece≥3） | **15** 件 |
| IR 空壳（piece=0） | **11** 件 |
| 需要人工 review | **11** 件 |

## 4. 需要人工补 4 个 tag 的件

字段含义：
- **neckline**：领型（crew 圆领 / high-mock 半高领 / polo Polo 领 / hood 连帽 / v-neck V领）
- **sleeve_style**：袖型（set-in 正肩 / raglan 插肩 / drop-shoulder 落肩）
- **garment_length**：衣长（short 短 / regular 常规 / long 长）
- **front_opening**：前襟（pullover 套头 / zipper_full 全拉链 / zipper_half 半拉链 / button 单排扣）

| case_id | 真实/占位 | 已有标签 | 缺什么 |
|---|---|---|---|
| C2590024 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2590444 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2590445 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2590446 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2690447 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2490033 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2490340 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2490439 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2590251 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2590408 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |
| C2690313 | 占位 | - | neckline,sleeve_style,garment_length,front_opening |


## 5. 27 件完整明细

| case_id | 原 cat | 新 cat | piece | 完整度 | neck | sleeve | length | 备注 |
|---|---|---|---|---|---|---|---|---|
| C2590024 | - | hoodie | 1 | minimal | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2590082 | - | hoodie | 3 | passing | polo | raglan | - | 该服装为带有连帽的半拉链长袖上衣，领口归类为 Polo 领，袖型为插肩袖。 |
| C2590444 | - | hoodie | 0 | empty_shell | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2590445 | - | hoodie | 13 | passing | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2590446 | - | hoodie | 9 | passing | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2690447 | - | hoodie | 0 | empty_shell | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2390077 | - | hoodie | 0 | empty_shell | crew | set-in | - | 该T恤为圆领设计，袖型为正肩袖，无特殊结构设计。 |
| C2390270 | - | hoodie | 0 | empty_shell | crew | set-in | - | 经典圆领黑白条纹长袖上衣，正肩袖设计，无特殊结构。 |
| C2490277 | - | hoodie | 0 | empty_shell | crew | set-in | - | 圆领正肩袖T恤 |
| C2490437 | - | hoodie | 0 | empty_shell | high-mock | raglan | - | 高领带抽绳，插肩袖设计 |
| C2490577 | - | hoodie | 3 | passing | crew | raglan | - | 圆领插肩袖无袖款式 |
| C2490583 | - | hoodie | 3 | passing | crew | raglan | - | 圆领插肩袖短袖T恤 |
| C2590023 | - | hoodie | 0 | empty_shell | crew | set-in | - | 这是一件圆领正肩袖卫衣，无特殊设计。 |
| C2590029 | - | hoodie | 4 | passing | high-mock | set-in | - | 这是一件带有拉链高领和正肩袖的抓绒衫。 |
| C2590069 | - | hoodie | 3 | passing | high-mock | set-in | - | 样衣为白色长袖T恤，领口为半高领，袖型为正肩袖。 |
| C2590214 | - | hoodie | 29 | passing | crew | raglan | - | 圆领插肩袖卫衣式样T恤 |
| C2590218 | - | hoodie | 14 | passing | high-mock | set-in | - | 灰色半拉链高领套头衫，正肩袖。 |
| C2590246 | - | hoodie | 6 | passing | high-mock | set-in | - | 样衣为高领半高领设计，袖型为正肩袖。 |
| C2590280 | - | hoodie | 0 | empty_shell | crew | set-in | - | 这是一款拼色长袖圆领T恤，袖子为正肩袖。 |
| C2590337 | - | hoodie | 0 | empty_shell | high-mock | raglan | - | 半高领设计，搭配插肩袖长袖T恤结构。 |
| C2590427 | - | hoodie | 0 | empty_shell | high-mock | set-in | - | 服装为半高领正肩长袖T恤结构 |
| C2490033 | - | hoodie | 10 | passing | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2490340 | - | hoodie | 3 | passing | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2490439 | - | hoodie | 3 | passing | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2590251 | - | hoodie | 6 | passing | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2590408 | - | hoodie | 10 | passing | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |
| C2690313 | - | hoodie | 0 | empty_shell | - | - | - | ⚠️ 自动生成的占位 part_labels，需在服务器平台人工补齐 4 个 t |


## 6. 后续动作

1. ✅ 27 件 IR 已生成（含 category 修正 + needs_human_review 标记）
2. ✅ 27 件 part_labels 已就绪（16 真实 + 11 占位）
3. ✅ 27 件已并入 `v1_pattern_ir_merged/`
4. ⏳ 等待你在服务器标注平台补齐 4 类 tag（衣长/袖型/领型/前襟）
5. ⏳ 11 件 IR 空壳需要在服务器上重跑 piece_assemble

## 7. 重要约束

- ⚠️ `merged_IR_copy/` 只是副本，**真正可被 DXF 流水线消费的是** `ir_corpus_0807/v1_pattern_ir_merged/`
- ⚠️ 11 件空壳 IR 即便并入 merged，下游 piece_assemble 也跑不出 piece
- ⚠️ 16 件已有真实 part_labels 的 IR 是 production-ready 的最小可用集
