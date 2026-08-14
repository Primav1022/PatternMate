# PatternMate

从人体尺寸、服装偏好到纸样组合与印花预览的服装设计工具。

- 在线使用：https://primav1022.github.io/PatternMate/
- 源码：本仓库

在线版前端托管在 GitHub Pages，对话 / 生图 / 纸样 / 试穿目前都打到演示机。**仓库里不含任何 API Key**。Cloudflare Worker 代码在 `workers/api/`，演示机关机后再切。

本地 `.env` 不要提交。

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
