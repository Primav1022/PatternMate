# PatternMate

从人体尺寸、服装偏好到纸样组合与印花预览的服装设计工具。

- 在线使用：https://primav1022.github.io/PatternMate/
- 源码：本仓库

在线版前端托管在 GitHub Pages。对话和生图走 Cloudflare Worker（密钥在 Worker Secrets，不进仓库）。纸样组合 / 3D 试穿仍需要演示机。

本地 `.env` 不要提交。Cloudflare 部署需要在仓库 Settings → Secrets 填写：

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `MODEL_API_KEY`
- `MODEL_BASE_URL`

部署成功后把 Worker 地址写入 Settings → Variables → `WORKER_BASE_URL`，再跑一次 Pages workflow。

## 目录

| 路径 | 内容 |
|---|---|
| `src/` | React/Vite 前端 |
| `apps/geometry-service/` | 原子规则与纸样组合 |
| `data/ir/`、`data/seed/dxf/`、`data/final/` | 规则用 IR / DXF / 整理数据集 |
| `public/` | 参考图与 UI 资源 |

## 本地运行

```bash
cp .env.example .env
pnpm install
bash scripts/run-geometry.sh
pnpm run dev -- --port 5173
```

把模型地址和密钥写在本地 `.env`，不要提交。

健康检查：`curl -s http://127.0.0.1:8788/health`

## 品类管线

| 品类 | 默认 execution_mode |
|---|---|
| T 恤 | `simple_piece_swap` |
| 衬衫 | `shirt_strategy` |
