# CHI27 前端与原子级规则本地修改手册

> PatternMate 工作区：前端在 `src/`，原子规则在 `apps/geometry-service/`。  
> `data/`、`packages/catalogs/`、`_handoff_pack/` 已实拷到本目录；`data/final/` 含整理后的 tshirt/shirt/hoodie 数据集。

> 用途：在 macOS 上使用 Cursor 逐步修改前端和原子级服装裁片组合规则。  
> 范围：只修改 `src/`、`apps/geometry-service/`、`_handoff_pack/scripts/` 与依赖数据。  
> 原则：自动结果是试样，不是可直接量产的终稿；必须保留人工纸样师审核。

## 1. 项目位置

本地项目根目录：

```text
/Users/primav/Documents/博一/CHI27库淑兰服装/patternmate
```

用 Cursor 打开：

```bash
cd "/Users/primav/Documents/博一/CHI27库淑兰服装/patternmate"
cursor .
```

如果 `cursor` 命令不存在，直接在 Cursor 中选择「Open Folder」并打开上述目录。

## 2. 修改边界

### 2.1 允许修改

```text
src/                               前端 React/Vite
apps/geometry-service/             原子级规则、Planner、Validator、导出
packages/catalogs/                 版型、面料、工艺选项表（符号链接）
_handoff_pack/scripts/             原子规则用到的几何辅助函数
data/ir/v1_rule_ready/             规则可用的基础 IR（符号链接）
data/ir/tshirt_v2/                 T 恤 Pattern IR
data/ir/shirt_v2/                  衬衫 Pattern IR 与 part_label
data/seed/dxf/v1_annotated/        IR 对应的 DXF
```

### 2.2 不允许修改

```text
原仓库 apps/ai-service/            生图等 AI 后端，由同事负责
原仓库 apps/tryon-service/         3D 人体与试穿后端，由同事负责
deploy/、模型权重、服务器密钥
```

前端中的 `Research3D.tsx` 可以调整界面展示，但不要因为本地 3D 接口不可用而修改试穿后端。

## 3. 首次启动

前端依赖用根目录 `pnpm install`。Python 依赖装到 `apps/geometry-service/.venv`。

### 3.1 启动原子规则服务

打开终端 A：

```bash
cd "/Users/primav/Documents/博一/CHI27库淑兰服装/patternmate"
pnpm run geometry
```

检查服务：

```bash
curl -s http://127.0.0.1:8788/health | python3 -m json.tool
```

正常情况应看到：

- `"ok": true`
- `"service_build": "prototype-parametric-v2"`
- `ir_count` 大于 0
- `dxf_count` 大于 0

### 3.2 启动前端

打开终端 B：

```bash
cd "/Users/primav/Documents/博一/CHI27库淑兰服装/patternmate"
pnpm run dev -- --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173/
```

`.env.local` 中已配置：

```env
VITE_GEOMETRY_BASE_URL=http://127.0.0.1:8788
```

修改 `.env.local` 后必须重启 Vite。

### 3.3 本地不启动 3D 服务时

这次修改不包含 `apps/tryon-service`。因此：

- 3D 人体生成不可用是预期状态。
- 3D 试穿按钮可以是禁用状态。
- 不要为了消除接口错误去修改 3D 后端。
- 原子规则可以通过 `/compose`、单元测试和 DXF 预览独立验证。

## 4. 前端文件索引

### 4.0 当前精简界面

界面按一个主任务、一个主操作组织：

- 「人体尺寸」：左侧填写，右侧查看测量图；详细测量方法默认折叠。
- 「服装设计」：左侧描述需求和调整偏好，右侧选择参考款。
- 「编辑搭配」：版片组件默认展开；面料和工艺默认折叠；存在草稿变化时才显示“生成组合预览”。
- 「纸样预览」：闭合、版片完整性、接缝和人工审核状态默认可见；图层、版片清单、排版尺寸和 recipe hash 放在折叠区。
- 「印花创作」：正反面与印花模式保留，编辑说明仅在需要操作提示时出现。

组合审核状态含义：

| 状态 | 含义 |
|---|---|
| 已替换 | 候选组件已迁移并完成该步自动校验 |
| 保留原部件 | 迁移不可靠或校验失败，本步回滚到基础版型状态 |
| 等待审核 | 自动操作完成，但仍需纸样师确认 |
| 失败 | 当前步骤没有得到可提交或可保留的有效状态 |

前端减法设计与执行任务分别记录在：

```text
docs/superpowers/specs/2026-08-12-patternmate-clarity-refresh-design.md
docs/superpowers/plans/2026-08-12-patternmate-clarity-refresh.md
```

| 文件 | 作用 | 建议修改方式 |
|---|---|---|
| `src/main.tsx` | 整体步骤、状态、接口调用、选项组合 | 一次只改一个步骤或一组状态 |
| `src/PatternPreview.tsx` | DXF、2D 设计、3D 切换与组合结果 | 原子组件预览的主要修改入口 |
| `src/CompositionReviewPanel.tsx` | 显示每个组件的 applied/rollback/审核状态 | 保留失败原因和人工审核提示 |
| `src/compositionTypes.ts` | 组合结果、审核记录的 TypeScript 类型 | 后端字段改变时先更新这里 |
| `src/compositionDraft.ts` | 判断用户选择是否改变、生成组合草稿 | 修改选项逻辑后运行对应测试 |
| `src/GarmentPreview.tsx` | 服装与印花视觉预览 | 只改表现，不当作 DXF 几何真值 |
| `src/garmentGeometry.ts` | 2D 服装预览几何 | 与真实 DXF 结果分开理解 |
| `src/styles.css` | 主样式 | 布局修改优先放在这里 |
| `src/overrides.css` | 迭代期覆盖样式 | 小步调整可以先放这里，稳定后再收敛 |
| `src/catalogs.ts` | 加载版型、面料、工艺选项 | 不要在组件中另外写一份选项表 |

## 5. 原子级规则文件索引

| 文件 | 责任 |
|---|---|
| `composition_contracts.py` | Planner 和 Executor 共用的数据契约 |
| `edge_role_resolver.py` | 将 IR 中的 `edge_role` 解析为规范边链角色 |
| `component_index.py` | 建立 `edge_role -> edge chain -> entity` 的组件索引 |
| `donor_similarity.py` | 按 `part_label` 和有限几何特征排序候选供体 |
| `batch_planner.py` | 将用户一次完成的选择生成有顺序的操作计划 |
| `batch_executor.py` | 逐组件迁移、校验、回滚与 provenance 记录 |
| `edge_transfer.py` | 记录可变边和受保护边的迁移关系 |
| `piece_topology.py` | 裁片闭合性、成对组件和服装裁片数量校验 |
| `composition_validation.py` | NaN/Inf 等基础几何合法性检查 |
| `composition_engine.py` | 尺寸适配、组合总流程、排版、整体校验 |
| `review_ledger.py` | 人工接受、拒绝、修改等审核记录 |
| `preview_outline.py` | 预览用的轮廓整理，不能代替生产边界校验 |
| `app.py` | `/compose`、`/catalog`、`/export` 等 HTTP 接口 |

`_handoff_pack/scripts` 中的辅助文件主要包括：

- `geometry_ops.py`：基础点、长度、边界和几何变换。
- `interface_morph.py`：领口、袖山与连接边的匹配。
- `run_experiments.py`：旧版的尺寸与裁片辅助函数。
- `dxf_export.py`：DXF 导出辅助。

修改原子规则时，优先改 `apps/geometry-service` 中的新流程，不要继续向 `_handoff_pack` 堆新业务逻辑。

## 6. 当前固定执行流程

用户先选完基础版型、领口、袖型、袖口、衣长和人体尺寸，然后一次生成组合计划。不是用户每点一个选项就立即执行一次迁移。

```text
用户完整选择
    ↓
加载基础 Pattern IR
    ↓
通过 part_label 筛选候选供体
    ↓
从 IR edge_chains/atomic_entities 建立 edge_role 索引
    ↓
Planner 生成有限操作序列
    ↓
领口 → 领型 → 袖型 → 袖口 → 衣长
    ↓
每个组件：迁移 → 接口边调整 → 闭合/拓扑校验
    ↓
通过：提交当前状态；失败：回滚并 retained_current
    ↓
处理下一个组件
    ↓
整体裁片数量检查 → 排版 → 导出试样 → 人工审核
```

### 6.1 不可破坏的语义

1. 替换单位是 IR 中的 `edge_role` 边链，不是整个 `piece_role`。
2. `piece_role` 用来判断边链属于前片、后片、左袖或右袖等上下文。
3. 候选供体优先按 `part_label` 匹配，不应脱离金标 IR 自由猜测。
4. 供体少标、边链不明确或迁移后不闭合时，保留基础版型当前状态。
5. 不允许为了让预览「看起来有变化」而凭空新画一条线冒充供体组件。
6. 未标注或不确定的 yoke、内部线、结构线默认保留。
7. 缝份应与最终轮廓绑定同步更新，或在几何确定后统一重新生成。
8. 自动校验通过仍然是 trial/review required，不等于量产通过。

### 6.2 Planner 当前顺序

`batch_planner.py` 中固定顺序为：

```python
OPERATION_ORDER = ("neckline", "collar", "sleeve", "cuff", "garment_length")
```

当袖型和袖口同时改变时，袖口操作依赖袖型操作。

## 7. 当前有限规则

### 7.1 T 恤领口

- 前片领口：`front_neckline`
- 后片领口：`back_neckline`
- 领口必须成组处理，不允许只换前片或只换后片。
- 保留肩线 `shoulder_seam`，迁移后两侧肩线不能消失。
- 迁移后前、后身裁片必须继续闭合。

### 7.2 T 恤袖型

- 替换两个完整袖片，不是只替换单侧袖口边。
- 候选袖片至少需要：`sleeve_cap`、`sleeve_underarm`、`sleeve_hem`。
- 新袖山与前、后袖窟不匹配时，按金标 IR 的前/后关系调整 `armhole_front` 与 `armhole_back`。
- 只有两个袖片都闭合，且前后袖窟接口通过检查，才能提交该步。
- 袖长和袖宽优先使用人体尺寸；数据缺失时再使用从 `part_label` 金标统计得到的固定比例。

### 7.3 衬衫领口与领片

- 前、后身领圈是一组接口边。
- 领片、领座等独立裁片使用 `piece_role` 确认所属裁片，但替换定位仍以 `edge_role` 为准。
- 基础版型中有 yoke 时保留；不要因为没有 yoke 标注而伪造 yoke 线。

### 7.4 袖口

- 袖口必须在袖型替换之后处理。
- 检查 `sleeve_hem` 与 `cuff_attach`、`cuff_outer` 的连接关系。
- 左右袖口必须对称成对处理，不允许只应用一侧。
- 候选组件无法可靠连接时回滚，不自动补一条没有来源的直线。

### 7.5 衣长和人体尺寸

- 衣长主要调整纵向结构和 `garment_hem`。
- 侧缝 `side_seam` 是衣长的依赖边，必须同步保持连续。
- 胸围、腰围影响横向尺寸，衣长影响纵向尺寸。
- 衬衫有左/右两个前片时，胸围不能对每个前片重复应用整体尺寸。

## 8. Validator 最低通过条件

### 8.1 T 恤裁片清单

- 1 个闭合前片。
- 1 个闭合后片。
- 2 个闭合袖片，无袖意图除外。
- 没有游离线、飞出袖口线或独立孤线。

### 8.2 衬衫裁片清单

- 1 个完整前片，或左/右 2 个闭合前片。
- 1 个闭合后片。
- 2 个闭合袖片，无袖意图除外。
- 需要时包含可审核的领片、领座、袖口；标注不足时可保留原状并进入人工审核。

### 8.3 每个组件都要检查

1. 坐标全部是有限数，无 NaN/Inf。
2. 可变边确实发生变化。
3. 非可变边的 hash 不应被无关改写。
4. 边链端点连接，裁片闭合。
5. 左/右成对组件数量正确。
6. 失败时恢复本次操作之前的 `entities`。
7. `component_results` 中记录供体、修改图元、校验结果和回滚原因。

## 9. 供体相似度

当前使用轻量、可解释排序，最多返回 3 个候选供体。

排序特征：

- `label_match`：`part_label.slug` 是否与用户选择相同。
- `interface`：与当前基础版型接口长度/尺寸是否接近。
- `topology`：所需 `edge_role` 是否完整。
- `proportion`：高宽比、袖长/衣长、袖宽/胸围等是否合理。
- `quality`：标注置信度、闭合性和几何质量。

修改相似度时不要首先增加复杂模型。先保证：

1. 精确 `part_label` 匹配优先。
2. 缺少必要 `edge_role` 的候选不进入可自动应用集合。
3. 得分分解可以在前端审核面板中显示。

## 10. 手动调用 `/compose`

先启动 geometry service，然后运行：

```bash
curl -s http://127.0.0.1:8788/compose \
  -H 'content-type: application/json' \
  -d '{
    "family": "tshirt",
    "sex": "female",
    "base_case_id": "C2590529",
    "measurements_cm": {
      "height": 160,
      "chest": 84,
      "waist": 68,
      "shoulder": 39,
      "neck": 34,
      "sleeveLength": 58,
      "upperArm": 28
    },
    "selections": {
      "neckline": "tshirt.neckline.v-neck",
      "sleeve": "tshirt.sleeve.puff",
      "garment_length": "tshirt.garment-length.regular"
    },
    "base_option_ids": {
      "neckline": "tshirt.neckline.crew",
      "sleeve": "tshirt.sleeve.set-in",
      "garment_length": "tshirt.garment-length.regular"
    },
    "execution_mode": "batch_preview"
  }' > /tmp/chi27-compose-result.json

python3 -m json.tool /tmp/chi27-compose-result.json | less
```

重点查看：

```text
status
validation.valid
validation.trial_ready
component_results[].status
component_results[].modified_entity_ids
component_results[].validation_issues
component_results[].provenance.donor_candidates
component_results[].provenance.edge_transfer
review_required
```

一个组件的常见状态：

- `applied`：该组件迁移和局部校验已通过。
- `retained_current`：该组件未通过，已回滚并保留上一个稳定状态。
- `applied_review_required`：前端表示为已应用但必须人工复核。

## 11. 每次修改后的最小验证

### 11.1 只改前端样式

```bash
cd "/Users/primav/Documents/博一/CHI27库淑兰服装/CHI27_AI4Manufacturing/apps/web"
npm test
npm run build
```

同时在浏览器检查：

- 页面无白屏。
- 中英文切换没有破坏布局。
- 1280px 宽度下主要面板可用。
- 列表、选中态、禁用态和审核警告可分辨。

### 11.2 改原子规则

```bash
cd "/Users/primav/Documents/博一/CHI27库淑兰服装/CHI27_AI4Manufacturing/apps/geometry-service"
PYTHONPATH="$PWD:$PWD/../../_handoff_pack/scripts" \
  .venv/bin/python -m unittest discover -s tests -v
```

当前基线是：

```text
Ran 54 tests
OK
```

只改一条规则时，先运行最小相关测试：

```bash
# edge_role 解析
PYTHONPATH="$PWD:$PWD/../../_handoff_pack/scripts" \
  .venv/bin/python -m unittest discover -s tests -p 'test_edge_role_resolver.py' -v

# Planner
PYTHONPATH="$PWD:$PWD/../../_handoff_pack/scripts" \
  .venv/bin/python -m unittest discover -s tests -p 'test_batch_planner.py' -v

# 逐组件执行与回滚
PYTHONPATH="$PWD:$PWD/../../_handoff_pack/scripts" \
  .venv/bin/python -m unittest discover -s tests -p 'test_batch_executor.py' -v

# 闭合性和裁片清单
PYTHONPATH="$PWD:$PWD/../../_handoff_pack/scripts" \
  .venv/bin/python -m unittest discover -s tests -p 'test_piece_topology.py' -v

# 真实袖片/袖口集成样例
PYTHONPATH="$PWD:$PWD/../../_handoff_pack/scripts" \
  .venv/bin/python -m unittest discover -s tests -p 'test_real_sleeve_cuff_integration.py' -v
```

局部测试通过后，仍然要运行全部 54 项测试。

## 12. 用 Cursor 的建议修改节奏

每轮只让 Cursor 处理一个明确问题。

### 第 1 步：只定位

示例提示词：

```text
请只分析，不要改代码。
定位「袖型替换后前后袖窟不一致」从前端 recipe 到 batch_executor 的完整调用链。
列出涉及的函数、edge_role、校验门和回滚点。
不要修改 apps/ai-service 或 apps/tryon-service。
```

### 第 2 步：先加一个失败测试

```text
只在 apps/geometry-service/tests 中新增一个最小回归测试，
复现左右袖片有一侧未闭合却被标记为 applied 的问题。
先运行并证明测试失败，不要改实现。
```

### 第 3 步：最小修复

```text
只修改使这个回归测试通过所需的最少代码。
不改 Planner 顺序，不改数据，不降低闭合性门槛。
失败时必须返回 retained_current 并恢复操作前 entities。
```

### 第 4 步：小范围视觉验收

```text
在不改后端返回结构的前提下，
让 CompositionReviewPanel 明确显示：替换了什么、修改了哪些图元、为什么回滚。
只改 apps/web/src/CompositionReviewPanel.tsx 和必要的 CSS。
```

### 第 5 步：全量验证

每轮结束前运行：

```bash
cd apps/web && npm test && npm run build

cd ../geometry-service
PYTHONPATH="$PWD:$PWD/../../_handoff_pack/scripts" \
  .venv/bin/python -m unittest discover -s tests -v
```

## 13. 建议的修改顺序

建议按下面顺序逐项修，不要同时改多个几何规则：

1. 前端显示「实际替换了什么」。
2. 领口：前/后领口边链成组替换，肩线不丢失。
3. T 恤袖型：两个袖片成对替换，两片均闭合。
4. 袖山/袖窟：前后接口关系和长度误差。
5. 袖长/袖宽：基于人体尺寸和金标比例的稳定规则。
6. 衬衫袖口：左右成对迁移，不用自由补线。
7. 衣长、胸围、腰围尺寸适配。
8. 缝份与最终轮廓的重生成。
9. 整体排版与散乱线清理。
10. 导出文件和人工审核记录。

## 14. 不要这样修

- 不要把 `piece_role` 当成替换定位键。
- 不要绕过 `edge_role_resolver` 直接用坐标猜哪条线是领口或袖口。
- 不要用「预览图看起来闭合」代替图拓扑闭合性检查。
- 不要将失败组件标记为 applied。
- 不要在一次 Cursor 任务中同时修前端、Planner、迁移、Validator 和数据。
- 不要删除未识别线条；先保留并标记为待审核。
- 不要将生成的试样宣称为可直接量产。
- 不要修改或覆盖同事正在处理的生图与 3D 后端。

## 15. 恢复与备份

这次定向拉取之前的本地备份在：

```text
.sync-backups/20260811-before-focused-pull/
```

恢复前先确认具体文件，不要将整个工作区盲目覆盖。

远端后续同步仍然必须使用白名单，且不使用 `--delete`。

## 16. 当前已验证基线

拉取完成时的基线：

- 前端 `npm test`：通过。
- 前端 `npm run build`：通过。
- 原子规则测试：54 项通过。
- 前端、geometry service、catalogs、IR、DXF 和原子辅助函数的远端/本地 checksum 差异为 0；本地 `.env.local`、`.npmrc`、依赖和构建产物除外。

修改任何规则后，请把这个基线当作回归下限：新规则不能通过降低闭合性、删除失败检查或将回滚伪装成成功来换取「绿色状态」。
