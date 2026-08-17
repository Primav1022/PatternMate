# 衬衫组合主线（执行版）

**Pipeline id:** `shirt.simple_piece_swap.v1`  
**入口:** `POST /compose` → `compose_recipe` → `compose_shirt`  
**代码:** `shirt_compose.py` + `shirt_strategy.py` + `shirt_side_seam.py` + `shirt_sleeve_fit.py` + `composition_engine.grading_profile`  
**几何真源:** `data/ir/shirt_v2/pattern_ir_compose/{case_id}.json`（`_annotate` 优先读 compose IR：外轮廓 + 缝份 + 片内线）  
**旧 IR:** `pattern_ir/`、`pattern_ir_remix_v1/` 不改；无 compose IR 时才回退旧 IR。

T 恤另见 [`TSHIRT_PIPELINE.md`](./TSHIRT_PIPELINE.md)。T 恤不传 `shoulder` / `armhole` 放码系数（衣身袖窿按 1）。

## 一次 compose 输入

1. **模板** `base_case_id`（31 款衬衫）
2. **部件** `selections` + `base_option_ids`：`collar` / `placket` / `silhouette` / `sleeve` / `cuff` / `garment_length`
3. **身材** `sex` + `measurements_cm` + `fit` / `ease_cm`
4. **面料** `material_id`（只进缩水修正，不换片）
5. **版本** `compose_version`（可空 = 最新一步，通常是放码）

**出:** 预览 SVG、`versions[]`、`version_id`；`POST /export` 导出当前 `compose_version`。

## 执行顺序

每步成功后更新 `host_ref`（袖对**当前**衣身，不冻换片前的旧衣身）。有变化就打一版快照。

| 顺序 | 版本 id | 做什么 |
|---|---|---|
| 0 | `original` | 底款 compose IR 原纸样 |
| 1 | `body` | 领或门襟相对底款变了 → **一次整换**前后衣身（含领附件、育克、门襟） |
| 2 | `silhouette` | 廓形变了 → 侧缝 morph（cut **和** sew） |
| 3 | `sleeve` | **用户选了袖型**才换/补袖；没选则保持原纸样（无袖底款不补袖） |
| 3b | （并入 sleeve） | 换袖成功后，把**同一供体**的袖口 / 袖叉带上 |
| 4 | `cuff` | 用户另选了袖口 → 再换袖口片（覆盖 3b） |
| 5 | `grade` | 结构点放码 → 袖对袖窿 → 袖长/领围微调 → 袖口对袖肥 |

预览可点「原版本 / 上一版本 / 各步骤」。导出跟当前选中版。

## 换片规则

| 操作 | 执行 |
|---|---|
| 领型 + 前门襟 | 一起整换 `BODY_SWAP_ROLES`（前后片 + 领 + 门襟 + 育克） |
| 廓形 | 不换片。从供体取**一条左、一条右**侧缝（半片只取外侧，不要前中），前后片共用；cut 和 sew 都改。臂根接点锁住，下摆 y 锁住，下摆 x 可外放/收进 |
| 袖型 | 只换袖片。供体袖必须是可用袖身（拒绝短宽抽褶带，如 C2431105 那种被标成袖的袖口）。无袖底款且用户选了袖型 → 补袖 |
| 袖附件 | 换袖后默认带上同供体 `cuff` / `rib_cuff` / `sleeve_placket` / `sleeve_placket_extension` |
| 袖口（用户另选） | 只换袖口片，覆盖上面的同供体袖口 |
| 失败 | 该步 `retained_current`，不整单作废。袖换成细条则回退换袖前的袖 |

## 放码系数（`grading_profile`）

女装基码：身高 160、胸围 84、腰围 68、肩宽 38.88、颈围 34、袖长 58、上臂围 28。松量默认 8（合体 4、宽松 12）。缩水面料：\(s_{\text{缩水}}=1/(1-\text{缩水率})\)，否则 1。

\[
\begin{aligned}
s_{\text{胸}} &= (\text{胸围}+\text{松量})/(84+8) \\
s_{\text{腰}} &= (\text{腰围}+0.5\times\text{松量})/(68+4) \\
s_{\text{宽}} &= 0.78\,s_{\text{胸}} + 0.22\,s_{\text{腰}} \\
s_{\text{衣宽}} &= \operatorname{clamp}_{0.75}^{1.55}\big((0.72\,s_{\text{宽}} + 0.28\,\tfrac{\text{肩宽}}{38.88})\times s_{\text{缩水}}\big) \\
s_{\text{衣长}} &= \operatorname{clamp}_{0.80}^{1.45}(\text{身高}/160 \times s_{\text{缩水}}) \\
s_{\text{肩}} &= \operatorname{clamp}_{0.80}^{1.40}(\text{肩宽}/38.88 \times s_{\text{缩水}}) \\
s_{\text{袖窿}} &= \operatorname{clamp}_{0.80}^{1.40}\big((0.55\,s_{\text{宽}} + 0.45\,\tfrac{\text{上臂围}}{28})\times s_{\text{缩水}}\big) \\
s_{\text{领}} &= \operatorname{clamp}_{0.80}^{1.35}(\text{颈围}/34) \\
s_{\text{袖长}} &= \operatorname{clamp}_{0.75}^{1.45}(\text{袖长}/58)
\end{aligned}
\]

衣长选项再乘：short 0.92 / regular 1.0 / long 1.10。男装先乘一层女→男原型（同形）。预览标题「袖×」是 **\(s_{\text{袖长}}\)**，不是袖窿系数。

## 衣身结构点（`grade_body_structure`）

不是整片仿射拉伸。

- **胸围：** 胸围线以下外放，肩线以上不跟胸围走
- **衣长：** 只加长胸围线以下
- **肩宽：** 胸围线以上按 \(s_{\text{肩}}\) 过渡
- **袖窿：** 只动胸围线以上、外侧凹（不碰领圈）。相对中线再乘 \(1+0.28(s_{\text{袖窿}}-1)\)，并往下加深 \(0.16 H (s_{\text{袖窿}}-1)\)（越靠近肩点越少）
- **领圈：** 单独按 \(s_{\text{领}}\) 缩放
- 缝份 / 片内线跟对应点走；前育克跟前片

## 袖对袖窿（`fit_sleeves_to_armholes`）

不做袖山 morph，只定尺寸。先认前后袖窿弧 \(A_f,A_b\) 和袖窿深 \(D_f,D_b\)：前后都有短弧标注才用标注，否则从外轮廓肩点→袖窿底推断（整圈误贴的标注丢掉）。

\[
\begin{aligned}
W_{\text{袖肥}} &= \operatorname{clamp}_{0.48W_{\text{衣}}}^{0.80W_{\text{衣}}}\big(0.55(A_f+A_b)\big) \\
H_{\text{袖}} &\ge 1.2\times (D_f+D_b)/2
\end{aligned}
\]

前后弧差太大（比 \(<0.55\)）时改用 \(\max(A_f,A_b)\times 1.10\)。认不出弧则 \(0.58 W_{\text{衣}}\)。袖肥已跟袖窿走，不再用上臂围二次拉宽。缩成细条则回退这次缩放。

袖已经对过袖窿则不再乘 \(s_{\text{袖长}}\)（避免高度缩两次）。只有袖窿拟合没套上时，才用 \(s_{\text{袖长}}\) 调原袖。领片仍按 \(s_{\text{领}}\) 缩放。

## 袖口对袖肥（`fit_cuffs_to_sleeves`）

按袖片下摆一带宽度缩放 `cuff` / `rib_cuff`（不含袖叉）。不跟上臂围。

## 资料层

| 层 | 路径 | 用途 |
|---|---|---|
| **Compose 真源** | `data/ir/shirt_v2/pattern_ir_compose/` | 当前预览/换片/放码 |
| 旧语义 IR | `data/ir/shirt_v2/pattern_ir/` | 索引、part labels、回退 |
| 融合锁定基线 | `pattern_ir_remix_v1/` | 归档，compose 不再当主几何 |
| Part labels | `data/ir/shirt_v2/part_labels/` | 领/袖/袖口/门襟/廓形供体检索 |
| 原 DXF | 标注平台 / `data/seed/dxf/` | 建 compose IR 的闭合裁片 |

## 不要做

袖山 morph、没选袖型却补袖、廓形只改 cut 不动 sew、袖口只跟上臂围、换衣身后袖仍对旧衣身、把抽褶袖口当袖身。

## 测试

- 侧缝 / 缝份：`tests/test_shirt_side_seam.py`
- 袖对袖窿 / 袖口对袖肥：`tests/test_shirt_sleeve_fit.py`
- 无袖底款不补袖、泡泡袖带袖身+同供体袖口、工业放码：`tests/test_composition.py`
