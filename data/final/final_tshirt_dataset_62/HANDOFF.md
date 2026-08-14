# T 恤 Pattern IR 数据集 — 二次开发 Handoff 包

> **版本**：v1.0（基于 62 个 tshirt 案例）
> **位置**：`ir_corpus_0807/final_tshirt_dataset_62/`
> **目的**：提供给前端同学做**基于规则的参数变换**与**组件重构**

---

## 0. 拿到包先做什么

```
final_tshirt_dataset_62/
├── pattern_ir/        # 62 份 *.pattern-ir.json    ← 结构化几何
├── sample_images/     # 62 张 *.{jpg,png}          ← 视觉对照
└── INDEX.csv          # 62 行的快速导航            ← 必看
```

先看 `INDEX.csv`，12 列：

| 列 | 含义 | 用途 |
|---|---|---|
| `case_id` | 案例主键 | 反查 IR 与样衣图 |
| `category` | 品类（全是 `tshirt`） | 筛选 |
| `neckline` | 领口 slug | **可索引** |
| `sleeve_style` | 袖型 slug | **可索引** |
| `garment_length` | 衣长 slug | **可索引** |
| `special` | 特殊结构（`\|` 分隔） | **可索引** |
| `pieces` / `edge_chains` / `notches` | 规模 | 排重 |
| `notch_matched` / `notch_unmatched` / `match_rate` | 刀口映射质量 | 评估 |

INDEX 的 `match_rate` < 100% 的只有 **C2690430（52%）**，其余 61 个全是 100% 或没有 notch，可放心做下游。

---

## 1. 文件加载建议

```js
// Node
const fs = require('fs');
const ir = JSON.parse(fs.readFileSync('pattern_ir/C2490033.pattern-ir.json', 'utf-8'));

// 浏览器 / Vite
import data from './pattern_ir/C2490033.pattern-ir.json';
```

> **不要直接 `require` 62 个文件做打包**。单文件 200 KB–2 MB，62 个共约 50 MB，前端请做**按需加载**（路由切到详情页才拉对应 JSON）。

---

## 2. 字段一览：能不能用、能不能拼

### 2.1 总览

| top-level 字段 | 覆盖 | 前端可读 | 可拼接 | 写入安全 |
|---|:-:|:-:|:-:|:-:|
| `case_id` | 62/62 | ✓ | ✓ 主键 | ✓ |
| `ir_version`, `schema` | 62/62 | ✓ | ✗ 只读 | ✗ |
| `created_at`, `source_files` | 62/62 | ✓ 元数据 | ✗ 只读 | ✗ |
| `design_semantics` | 62/62 | ✓ | ✓ 可改 | ⚠ |
| `design_semantics_extra.part_labels` | 62/62 | ✓ **推荐** | ✓ 可改 | ✓ |
| `layer_annotations` | 62/62 | ✓ | ✓ 可改 | ✓ |
| `atomic_entities` | 62/62 | △ | △ 见 §2.2 | ⚠ |
| `piece_instances` | 62/62 | ✓ **最稳** | ✓ | ✓ |
| `containment_relations` | 62/62 | ✓ | ✓ | ✓ |
| `edge_chains` | 62/62 | ✓ | ✓ | ✓ |
| `notches` | 62/62 | ✓ | ✓ | ✓ |
| `notch_edge_assignments` | 62/62 | ✓ 新补 | ✓ | ✓ |
| `notch_alignment_relations` | 62/62 | △ 仅 2 类 | ✗ | ⚠ |
| `seam_relations` | **3/62** | △ | ✗ 数据缺 | ✗ |
| `writeback_plan` / `review_history` / `traceability` / `quality` | 62/62 | △ 元数据 | ✗ 只读 | ✗ |
| `part_type_bindings` | 5/62 | △ | △ | ✗ |
| `layout_transform` / `geometry_edits` | 5/62 | △ | △ | ✗ |
| `image` | ✗（在 part_labels_tshirt） | ✓ | — | — |

### 2.2 atomic_entities 里的"水份"

| entity_type | 占比 | 用途 |
|---|:-:|---|
| `POINT` | 72.9% | 装饰点 / 钻孔点，**没有 geometry.points 数组** |
| `TEXT` | 15.2% | DXF 文字标注 |
| `POLYLINE_SEGMENT` | 4.5% | **真实线段，可用** |
| `LINE` | 1.5% | **真实直线，可用** |
| `POLYLINE` | 5.3% | **真实多段线，可用** |
| `NOTCH_PAIR` | 0.5% | 刀口点，**见 notches 字段** |
| `INSERT` | 0.2% | 块引用，**忽略** |

**结论**：要做"参数变换"应直接用 `edge_chains` + `piece_instances`；`atomic_entities` 只在要做原始 DXF 还原时再下钻。

---

## 3. 可索引字段（前端做筛选/分组/分类时用）

### 3.1 强索引（来自 part_labels_tshirt）

这些字段直接抄到 `INDEX.csv`，**前端建议把它们镜像到一个独立索引文件**以便 O(1) 检索：

| 字段 | slug 字典 | 样本数 |
|---|---|---|
| `neckline` | `crew` (46) / `polo` (5) / `high-mock` (4) / `v-neck` (3) / `boat` (2) / `scrunch` (1) / `non_composable` (1) | 62 |
| `sleeve_style` | `set-in` (50) / `flutter` (3) / `raglan` (3) / `non_composable` (3) / `puff` (1) / `batwing` (1) / `unknown` (1) | 62 |
| `garment_length` | `regular` (35) / `long` (16) / `short` (11) | 62 |
| `special` | 自由文本数组，常用值：`高领/半高领` `插肩袖` `落肩袖` | — |

注意：**slug 是规范值，前端做 i18n 必须查这份字典**，不要自己造。

### 3.2 中等索引（来自 IR 内部）

| 字段 | 字典 | 说明 |
|---|---|---|
| `piece_instances[].piece_role` | `sleeve` (183) / `back_body` (101) / `front_body` (99) / `neck_binding` (73) / `cuff` (1) / `unknown` (8) | 看一个 case 里有几片、都是什么片 |
| `edge_chains[].edge_role` | `side_seam`, `hem`, `cuff_edge`, `sleeve_cap`, `armhole_*`, `front_neckline`, `back_neckline`, `shoulder`, `underarm`, `construction_*`, `pattern_boundary` …（22 类） | 看一个片有几条边、每条是什么角色 |
| `atomic_entities[].source.layer` | 1/2/3/4/7/8/11/13 | 看线条/文字/刀口在哪个图层 |

### 3.3 弱索引（看 IR 才知道，不推荐做分组）

- `design_semantics.style_tags`：长度 0–10，自由文本，不稳定
- `design_semantics._category_source`：字符串标签

---

## 4. 可拼接字段（前端做"组件替换"用）

下面这些字段都是**结构化 ID + 几何坐标**，可以拆，可以拼。

### 4.1 piece 级别拼接

```
piece_instances[i]
├── piece_id              ← 主键
├── piece_role            ← 类型，可被同 piece_role 替换
├── bbox                  ← 4 元组 {min_x, min_y, max_x, max_y}    ✓ 可拼接
├── boundary_entity_ids[] ← 该片的边界 atomic_entity_id 列表       ✓ 可遍历
└── internal_entity_ids[] ← 该片的内部 atomic_entity_id 列表       ✓ 可遍历
```

**拼接方法**：
1. 在 `piece_instances[]` 里找 `piece_role == "sleeve"` 的 piece
2. 读 `boundary_entity_ids` → 在 `atomic_entities` 里找对应几何
3. 替换它时，确保 `edge_chains[].piece_id` 跟着改

### 4.2 edge_chain 级别拼接

```
edge_chains[i]
├── edge_chain_id         ← 主键
├── piece_id              ← 反向到 piece
├── edge_role             ← 22 类边角色字典（见 §3.2）
├── ordered_entity_ids[]  ← 沿方向排列的 atomic_entity_id 列表  ✓ 可遍历
├── direction             ← "forward" / "reverse"
└── part_type             ← {neckline, sleeve_style, garment_length} 三选一  ✓
```

**拼接方法**（典型："把正肩袖换成插肩袖"）：
1. 找到所有 `edge_role == "shoulder"` 或 `edge_role == "armhole_*"` 的 edge_chains
2. 用同 `edge_role` 的另一 case 的对应 edge 替换
3. 注意 `ordered_entity_ids` 顺序必须跟新边几何一致（方向性敏感）

### 4.3 刀口级别拼接

```
notches[i]
├── notch_id              ← 主键
├── points[[x,y], [x,y]]  ← 刀口点对（成对使用）             ✓ 可改坐标
├── piece_id              ← 反向到 piece（手工 12 条）       △ 多数为 null
├── edge_chain_id         ← 反向到 edge（手工 12 条）       △ 多数为 null
└── source_layer          ← 总是 "4"

notch_edge_assignments[i]   ← **新补全的字段**
├── notch_id              ← 关联 notches
├── edge_chain_id         ← 自动匹配 776/791 = 98.6%        ✓
├── piece_id              ← 自动匹配 744/791 = 94.1%        ✓
├── distance              ← 匹配距离（mm）                  ✓ 评估用
└── source                ← "auto" / "manual_in_notch" / "far_match" / "unmatched"
```

**拼接方法**（典型："把拼接处的刀口对齐到新缝上"）：
1. 读 `notch_alignment_relations[]`（每 case 1–2 条对齐关系，标了哪两个 notch 必须配对）
2. 用 `notch_edge_assignments` 找每个 notch 所在的新 edge_chain_id
3. 改 `notches[i].points` 即可，几何自动跟上

---

## 5. 不要碰的字段

| 字段 | 为什么 |
|---|---|
| `ir_version`, `schema` | schema 版本锚点，改了等于换数据格式 |
| `writeback_plan` | 标注平台回写路径，改了上传会失败 |
| `review_history` | 审阅历史，是审计链 |
| `traceability` | 来源追溯链 |
| `quality` | 数据质量评分，下游算法依赖 |
| `seam_relations` | **只有 3/62 案例有效**（C2490033 2 条 + 1 条），大多数是空数组，不是数据缺失就是解析失败，**不要拿来做下游决策** |
| `part_type_bindings` | 仅 5/62 案例有数据 |
| `layout_transform`, `geometry_edits` | 仅 5/62 案例有数据 |

---

## 6. 推荐的"组件重构"工作流

> 场景：把 A case 的"正肩短袖"换成 B case 的"插肩袖"

1. **加载两个 IR**：用 `case_id` 索引
2. **找部件**：
   ```js
   const targetSleeves = irB.piece_instances.filter(p => p.piece_role === 'sleeve');
   const sourceSleeves = irA.piece_instances.filter(p => p.piece_role === 'sleeve');
   ```
3. **搬迁 edge_chain**：把 irB 的 sleeve 对应 edge_chains 复制到 irA（改 piece_id 指 irA 的新 sleeve piece_id）
4. **搬迁 atomic**：把 edge_chains.ordered_entity_ids 引用的 atomic_entities 复制到 irA
5. **重定位坐标**：用 irA 的 layout（origin/rotation）做仿射变换，可参考 `layout_transform` 字段（仅 5/62 有，写自己的仿射函数更稳）
6. **修正刀口**：用 `notch_edge_assignments` 的 distance < 200mm 自动重写 `notches[i].piece_id / edge_chain_id`
7. **更新 INDEX**：把新生成的 case 写回 `INDEX.csv`

---

## 7. 已知数据限制

| 问题 | 影响 | 缓解 |
|---|---|---|
| C2690430 有 11 个 notch 未匹配（距离 5000–7900mm） | 该 case 的 sleeve seam 边缘不齐 | 前端可标记"未对齐"，建议不参与变换 |
| 4 个 case 有 `seam_relations = []` | 缝合关系缺 | 自行基于 `edge_chains + notch_alignment_relations` 推断 |
| `atomic_entities` 里 80%+ 是 POINT/TEXT | 前端做"原始 DXF 还原"时性能差 | 只渲染 `LINE/POLYLINE/POLYLINE_SEGMENT` 三类 |
| 8 张样衣图是 PNG 而不是 JPG | 部分图片查看器支持差 | 已统一在 `sample_images/` 下 |
| `part_labels_tshirt` 与 IR 的 `design_semantics_extra.part_labels` 是两份独立数据 | slug 可能不一致 | 以前者为准（人手审过） |

---

## 8. 速查：JSON 加载示例

```js
// 一键列出所有可索引 slug 分布
const fs = require('fs');
const ir = JSON.parse(fs.readFileSync('pattern_ir/C2490033.pattern-ir.json','utf-8'));
console.log(ir.case_id);                 // "C2490033"
console.log(ir.piece_instances.map(p => p.piece_role));  // ["back_body", ...]
console.log(ir.edge_chains.filter(e => e.edge_role === 'shoulder').map(e => e.edge_chain_id));
console.log(ir.notch_edge_assignments.filter(a => a.source === 'unmatched').length);  // 0
```

需要更深字段可读 `ir_corpus_0807/v1_pattern_ir_merged/MANIFEST.json` 里的 `items[]`，里面有每 case 的 pieces_total/edge_chains_total/notches_total 等聚合统计。

---

**联系**：`final_tshirt_dataset_62/` 在 `ir_corpus_0807/` 下；上游脚本 `scripts/notch_to_edge_remap.js`；原始 IR 备份在 `v1_pattern_ir_merged/`（已被 notch 映射覆盖；如需最原始手工版请看 `part_labels_tshirt/{case_id}.json`）。