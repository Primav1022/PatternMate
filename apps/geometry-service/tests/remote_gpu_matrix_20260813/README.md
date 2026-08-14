# Remote GPU T-shirt matrix acceptance

## 这张 collage 是什么
`remix_compose_collage.png` 由 GPU 上 `apps/geometry-service/tests/run_remix_matrix_v4.py` 生成：  
对若干 **base 款** ×（原样 / 换领 / 换袖 / 加长）跑 `simple_piece_swap`，把 SVG 渲成瓦片再拼成验收图。  
不是前端产品截图，是后端组合质量肉眼检查用的。

## C2490257（已处理）
中间行锯齿/畸变来自 **底片 DXF/IR 本身质量差**，不是新算法偶发花屏。  
已从 GPU 服务数据隔离，并踢出矩阵基款：

- 隔离目录：`/root/autodl-tmp/CHI27/.backup/quarantine_C2490257_20260813_012432`
- `BLOCKED_DONORS` 含 `C2490257`
- compose `base_case_id=C2490257` → 422
- 本地同样隔离到 `patternmate/.backup/quarantine_C2490257_20260813`

## 重跑矩阵（无 C2490257）
- total **64** · ok **64** · fail **0** · ok_rate **100%**
- bases: `C2590529`, `C2490278`
- 见更新后的 `remix_compose_collage.png` / `minimal_remix_matrix_report.json`

## 备注
整列 **neck=invalid** 是领口组合校验问题，与 C2490257 无关，需另修。
