# T 恤组合主线（已冻结）

**Pipeline id:** `tshirt.simple_piece_swap.v2`  
**代码入口:** `apps/geometry-service/simple_compose.py`  
**路由:** `compose_recipe` → `resolve_execution_mode(tshirt)` → 强制 `simple_piece_swap`  
**主站:** `src/main.tsx` 对 `family=tshirt` 发送 `simple_piece_swap`  
**远端切换:** 见同目录 [`REMOTE_TSHIRT_CUTOVER.md`](./REMOTE_TSHIRT_CUTOVER.md)

**I/O:** 入 = 前端模板 `base_case_id` + 用户喜好（`selections` / 尺码 / 面料等）；出 = **T 恤 DXF**（`POST /export`）。  
**衬衫**另见 [`SHIRT_PIPELINE.md`](./SHIRT_PIPELINE.md)（执行版 `shirt.simple_piece_swap.v1`）。

## 固定逻辑

1. **策略（按 sleeve slug）**
   - puff / set-in / bell / regular → 只换袖
   - raglan / batwing → 前+后+袖一起换
   - flutter → 换衣身、去掉独立袖
   - neckline → 只改领口边（+可选领条），不整片换衣身

2. **检索** — `rank_donors` 按 option / interface / topology

3. **袖 / 部件** — 按 slug **整片拼贴**（换片）。set-in / regular / bell 再按针织工业逻辑对袖窿：袖山高 ≈ 0.48×袖窿深，前后袖山弧对前后袖窿（吃势 1.01），袖肥由这段弧+高反算；袖长只加在袖肥线以下。puff 不压矮袖山。raglan / batwing / flutter 不走这套。  
4. **放码** — `grading_profile`：衣身宽/长、领、面料缩率；袖肥/袖山跟当前袖窿走，不再整片纵向拉袖山  
5. **失败策略** — 组件级 `retained_current`（暂不整单硬拒绝）
6. **VLM** — 本地优先、API fallback；主路径 compose/export 不得因 VLM 卡住

## 不要轻易改

改 T 恤主线前先升 `pipeline` 版本号，并重跑：

```bash
apps/geometry-service/tests/run_remix_matrix_v4.py
# 或至少 C2590529 puff/raglan/flutter + 放码叠图
```
