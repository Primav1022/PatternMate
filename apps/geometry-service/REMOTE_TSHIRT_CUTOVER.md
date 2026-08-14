# 远端 T 恤几何主线切换策略

**目标环境:** SeetaCloud AutoDL  
远端根目录: `/root/autodl-tmp/CHI27`  
geometry: `127.0.0.1:8788`（supervisord `program:geometry`）  
前端静态: nginx `root /var/www/chi27`

**本地权威源:** `patternmate/apps/geometry-service`  
**冻结主线:** [`TSHIRT_PIPELINE.md`](./TSHIRT_PIPELINE.md)  
**Pipeline id:** `tshirt.simple_piece_swap.v1`

**范围:** 只切 **T 恤** 匹配 → 组合 → **DXF 导出**。  
**衬衫逻辑尚未编写** — 本轮不宣称衬衫可用，不把衬衫当交付物。

---

## 0. 输入 / 输出契约（先搞清楚）

### 0.1 输入（前端用户侧）

主站用户选完后，经 `POST /compose`（预览）与 `POST /export`（生产包）进入几何服务，核心是一份 **CompositionRecipe + 设计态**，不是裸 DXF：

| 输入类 | 典型字段 / 来源 | 含义 |
|---|---|---|
| 品类 | `family=tshirt` | 本轮仅此交付 |
| 参考模板 | `base_case_id`、参考款缩略图对应的 IR | 用户选的底版 |
| 底版默认选项 | `base_option_ids`（领/袖/衣长等） | 模板当前结构 |
| 用户改款喜好 | `selections`（neckline / sleeve / garment_length / …） | 一系列部件偏好 |
| 体型与合体 | `sex`、`measurements_cm`、`fit`、`ease_cm` | 放码输入 |
| 面料喜好 | `material_id`、`fabric_color` | 缩率等进入 grading |
| 意图/约束（可选） | `intent_constraints`、设计对话分析结果 | 辅助检索与文案，**不得阻塞出图** |
| 印花等（导出时） | `design.print` | 打进生产 zip；几何主体仍是纸样 |

前端职责：把「模板 + 一系列喜好」收成稳定 recipe，调用 compose / export。  
后端职责：按 recipe **确定性组合几何**；模型只做增强，不做唯一通路。

### 0.2 匹配与生成（中间过程）

```text
用户模板 + 喜好
  →（可选）意图解析 / 参考检索   ← 可含 VLM，必须可跳过
  → resolve_execution_mode(tshirt) → simple_piece_swap
  → simple_compose：策略表 + donor 检索 + 袖迁移/领口边 + 放码
  → 预览 SVG / entities
  → export → T 恤 DXF（+ manifest 等 zip）
```

主路径几何 **不依赖** VLM 成功。策略表（`SLEEVE_STRATEGY` / `rule_strategy`）是硬兜底。

### 0.3 输出（交付物）

| 输出 | 接口 | 说明 |
|---|---|---|
| **T 恤 DXF** | `POST /export` → zip 内 `*.trial.dxf` | **本轮正式交付物** |
| 预览 SVG / pieces | `POST /compose` | 前端「专业 DXF」视图用，须先于 export 可用 |
| `pipeline` / `execution_mode` | compose/export meta | 证明走的是 `tshirt.simple_piece_swap.v1` |
| production_manifest / validation | export zip 内 | 伴随文件，不是替代 DXF |

**硬规则:** 用户点生成/导出后，前端必须能拿到纸样结果（至少 compose 预览）；**禁止**因 VLM/外网 API 卡住而空白无响应。

### 0.4 衬衫

- 衬衫 remix **逻辑还没写好**，不是本切分的交付范围。  
- 远端若仍有 `family=shirt` 入口，保持「不误导为已切到新主线」即可；**不要**在文档或 UI 上暗示衬衫已对齐本地 T 恤能力。  
- 本轮验收 **只验 T 恤 DXF**；衬衫回归仅作「别误伤进程」的冒烟，不作功能承诺。

---

## 1. VLM / 模型调用策略

远端 GPU 上已有本地 Qwen-VL（约 `127.0.0.1:8801`，geometry 环境里 `MODEL_BASE_URL` 常指本地网关）。云端/第三方 API 仅作 fallback。

### 1.1 优先级

1. **本地 VLM**（同机 OpenAI-compatible，如 qwen3-vl）— **首选**  
2. **外部 API**（`.env` / 远端配置的公网 endpoint）— **fallback**  
3. **规则表 / 无模型路径** — **最终兜底**（`rule_strategy`、`SLEEVE_STRATEGY`、跳过对话润色）

### 1.2 非阻塞（必须写进实现与验收）

| 规则 | 要求 |
|---|---|
| Compose / Export 主路径 | **不得** `await` 无超时的 VLM；VLM 失败/超时 → 用规则策略继续出图 |
| 超时 | 本地与 API 均设短超时（建议 ≤ 数秒～十余秒量级可配，默认见 `MODEL_TIMEOUT_SECONDS`，主路径宜更短或干脆不调） |
| 前端体验 | VLM 慢时仍显示上一版有效纸样或本次规则结果；禁止无限 loading 无 DXF |
| 沙盒 | `/sandbox/sleeve-vlm`、`/sandbox/strategy-compose` 可显式调 VLM；**失败不阻塞主站** |
| 对话 / analyze | 可优先本地再 fallback API；失败返回规则/模板摘要，不挡用户进入搭配与导出 |

### 1.3 配置意向（远端）

```text
首选: MODEL_BASE_URL=http://127.0.0.1:8791/v1 或 8801/v1（以 supervisord 实际为准）
     MODEL_NAME=qwen3-vl
fallback: 仅当本地 health 失败时改打外网 API（实现上可后续加双 endpoint；切分阶段至少保证「本地失败 → 规则兜底」）
```

切分第一期最低标准：**本地失败或超时 → 规则出图**；双 endpoint 自动切换可列为紧随的增强，但不得拖住 DXF 主路径上线。

---

## 2. 目标状态（T 恤）

| 维度 | 切换后必须成立 |
|---|---|
| 路由 | `family=tshirt` → **强制** `simple_piece_swap` → `simple_compose` |
| 预览 | `/compose` 返回 SVG + `pipeline=tshirt.simple_piece_swap.v1` |
| 交付 | `/export` 产出 **T 恤 DXF**（zip 内） |
| VLM | 本地优先；失败/超时不阻塞 compose/export |
| 衬衫 | **不在交付范围**；不承诺新逻辑 |

---

## 3. 切换前现状（已核实）

1. 远端前端已发 `execution_mode: simple_piece_swap`（T 恤）。  
2. 远端后端无 `simple_compose` / `resolve_execution_mode` → 该 mode **掉进 legacy**。  
3. 实测：`simple_piece_swap` 与 `legacy` 同结果；仅显式 `batch_preview` 走旧 batch。  
4. 因此用户「选模板 + 喜好」后，**并未**走到本地冻结的 T 恤组合，导出的也不是新主线几何。

---

## 4. 原则

1. **I/O 清晰** — 入：模板+喜好；出：T 恤 DXF。  
2. **几何主路径确定性** — 规则表兜底；VLM 只增强。  
3. **本地 VLM 优先，API fallback，超时降级** — 卡住也不能让前端空白。  
4. **服务端强制 T 恤路由** — 不信任客户端 mode。  
5. **最小面同步 + 可回滚** — 不碰 `data/`、AI 权重、Comfy。  
6. **衬衫未就绪** — 不写进交付验收。  
7. **`app.py` 合并不整盖** — 保留远端 `design_brief_text` 等独有逻辑。

---

## 5. Phase 0 — 备份与基线

```bash
cd /root/autodl-tmp/CHI27
STAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p .backup/geometry_$STAMP
cp -a apps/geometry-service .backup/geometry_$STAMP/
# 若改前端静态站：
cp -a /var/www/chi27 .backup/www_$STAMP
```

基线（已测）：T 恤 + `simple_piece_swap` → 返回 `legacy`（错误路径）。

---

## 6. Phase 1 — 后端（T 恤几何）

### 6.1 文件动作

源: `patternmate/apps/geometry-service/`  
目标: `/root/autodl-tmp/CHI27/apps/geometry-service/`

| 文件 | 动作 | 说明 |
|---|---|---|
| `simple_compose.py` | 新增 | T 恤主线 |
| `edit_strategy.py` | 新增 | 规则兜底 +（可选）模型判定 |
| `sleeve_vlm_sandbox.py` | 新增 | 沙盒；主站非必须 |
| `TSHIRT_PIPELINE.md` | 新增 | 冻结说明 |
| `REMOTE_TSHIRT_CUTOVER.md` | 新增 | 本文 |
| `composition_engine.py` | 覆盖前 diff | 加入 `resolve_execution_mode` → `compose_simple`；确认 `geometry_ops` path |
| `donor_similarity.py` | 覆盖前 diff | 检索增强 |
| `app.py` | **合并** | 并入默认 mode、`pipeline`、sandbox；**保留**远端 `design_brief_text` / 较长 `score_semantics` |
| `batch_executor.py` | 可选第二批 | 与衬衫旧路径相关；本轮可不动以降低风险 |
| `dxf_export` 依赖 | 确认 | export 已依赖；保证 `_handoff_pack` 可 import |

**不要覆盖:** `data/`、`.venv*`、`apps/ai-service`、`apps/tryon-service`、Comfy、模型目录。

### 6.2 路由（仅 T 恤强制）

```text
POST /compose | /export
  → family == tshirt → simple_piece_swap → compose_simple
  → family == shirt → 保持远端现有行为即可（不承诺、不宣传）
  → 响应含 execution_mode；tshirt 另含 pipeline
```

### 6.3 VLM 与主路径隔离（实现检查点）

- [ ] `compose_simple` / `/export` **同步路径上**无必成的 VLM HTTP  
- [ ] `edit_strategy.judge_*` 若被主站调用：超时 → `rule_strategy`  
- [ ] supervisord 里 geometry 的 `MODEL_*` 指向 **本地** 网关；外网 key 仅作后备配置  
- [ ] 人为停掉 VLM 进程后：compose + export 仍能出 DXF

### 6.4 重启与 import 冒烟

```bash
cd /root/autodl-tmp/CHI27
PYTHONPATH="/root/autodl-tmp/CHI27/_handoff_pack/scripts:$PYTHONPATH" \
  python -c "import simple_compose; import composition_engine; print('ok')"
supervisorctl -c deploy/autodl/supervisord.conf restart geometry
curl -s http://127.0.0.1:8788/health
```

### 6.5 后端验收（T 恤）

| 检查 | 期望 |
|---|---|
| compose `family=tshirt`（任意客户端 mode） | `execution_mode=simple_piece_swap`，`pipeline=tshirt.simple_piece_swap.v1`，有 SVG |
| 停 VLM / 错 `MODEL_BASE_URL` | compose **仍成功**，策略 `source=rule` 或等价 |
| export 同 recipe | zip 内存在 `*.trial.dxf`，可下载 |
| 袖策略抽测 | puff→sleeve_only；raglan→body_and_sleeve；flutter→body_integrated；v-neck→领口边 |

---

## 7. Phase 2 — 前后端对应（围绕 I/O）

### 7.1 前端输入侧

保持 recipe 组装：`base_case_id` + `base_option_ids` + `selections` + 尺码/面料 +  
`execution_mode: family === 'tshirt' ? 'simple_piece_swap' : …`（衬衫值不重要，因未交付）。

### 7.2 前端输出侧

1. compose 成功 → 专业 DXF 预览有内容（可保留上一版，避免空白）。  
2. export → 下载生产包，用户可拿到 **T 恤 DXF**。  
3. 建议展示只读 `pipeline` / `execution_mode`，便于发现再次掉 legacy。  
4. VLM/analyze 失败时 toast 即可，**不阻断**「生成组合预览 / 导出」。

### 7.3 构建

```bash
cd /root/autodl-tmp/CHI27/apps/web && npm run build
# rsync dist/ → /var/www/chi27（按现网习惯）
```

本地 `patternmate/src` 与远端 `apps/web` 分离 — **cherry-pick，禁止整目录盲拷。**

---

## 8. Phase 3 — 端到端验收（交付视角）

- [ ] 用户选 T 恤模板 + 改袖/领/衣长/面料 → 预览有纸样  
- [ ] 导出 zip 含 **T 恤 DXF**，能在 CAD/看图软件打开  
- [ ] 响应 `pipeline=tshirt.simple_piece_swap.v1`  
- [ ] 杀掉本地 VLM 后再走一遍：预览与 DXF 仍出  
- [ ] （非必须）本地 VLM 恢复后，沙盒/对话可走本地  
- [ ] **不**把衬衫导出列为通过条件  

---

## 9. Phase 4 — 回滚

```bash
# 恢复 .backup/geometry_$STAMP → apps/geometry-service
supervisorctl -c deploy/autodl/supervisord.conf restart geometry
# 如有：恢复 /var/www/chi27
```

触发: health 挂、compose/export 持续 5xx、T 恤 DXF 无法生成、误伤其它 supervisord 程序。

---

## 10. 风险摘要

| 风险 | 等级 | 缓解 |
|---|---|---|
| 整盖 `app.py` 丢掉远端对话逻辑 | 中 | **合并** |
| geometry 重启 import 失败 | 中 | 冒烟后再 restart；目录备份 |
| T 恤视觉相对旧 legacy「变了」 | 预期内 | 矩阵/袖型肉眼验 |
| VLM 拖死请求 | 高（体验） | 主路径不依赖；超时→规则 |
| 衬衫被当成已切换 | 产品误解 | 文档与验收明确未就绪 |
| 动 data/模型 | 高 | 本方案禁止 |

**不会:** 清 IR、清 Comfy 权重、不可逆写语料。坏了主要是 geometry 行为，目录级可回滚。

---

## 11. 本轮明确不做

- 不交付衬衫新 remix 逻辑  
- 不把 VLM 成功设为导出前提  
- 不整仓覆盖 `CHI27`  
- 不以 sandbox UI 为上线门槛  
- 不重标 IR  

---

## 12. 推荐落地顺序

1. 备份  
2. 新增 `simple_compose` 等 + **合并** `composition_engine` / `app.py`  
3. import 冒烟 → restart geometry  
4. curl：compose 强制路由 + **停 VLM 仍 compose** + **export 出 DXF**  
5. 主站点几款 T 恤预览/导出  
6. 可选：前端展示 pipeline；双 endpoint fallback 增强  
7. deploy note: `tshirt simple_piece_swap.v1 cutover; output=DXF; VLM non-blocking @ <date>`

---

## 13. 相关文件

| 路径 | 角色 |
|---|---|
| `TSHIRT_PIPELINE.md` | T 恤冻结主线 |
| `simple_compose.py` | 组合实现 |
| `composition_engine.py` | 路由 / 放码 |
| `edit_strategy.py` | 规则兜底（+可选模型） |
| `app.py` `/compose` `/export` | 预览与 **DXF 交付** |
| `patternmate/src/main.tsx` | 前端 recipe / export |
| 远端 `deploy/autodl/supervisord.conf` | geometry + 本地 MODEL_* |
