# CHI27 AI4Manufacturing

云端服装设计与工业制版工作台。当前仓库包含：

- `apps/web`：React + Vite 工作台，固定为 2D 流程，不含 3D 入口。
- `apps/worker`：Cloudflare Worker API、D1、R2 和 Queue 编排。
- `apps/geometry-service`：Python/FastAPI 几何服务，可作为 Cloudflare Container 运行。
- `packages/catalogs`：版型、面料、工艺清单与素材校验器。
- `data/seed`：需要导入 R2 的参考图与系统印花素材。

## 本地开发

```powershell
npm install
npm run validate-assets
npm run sync:local-config
npm run dev:web
```

本地模型/API 配置集中在 `config/model.local.json`（该文件不会提交到 Git）。阿里或其他 OpenAI 兼容接口填写 `model.provider/baseUrl/name/apiKey`，确认无误后将 `model.enabled` 改为 `true`；`npm run dev:local` 会只把密钥注入 Python 几何服务，密钥不会进入浏览器。`npm run sync:local-config` 仍可同步 Worker 与前端的非敏感地址配置。

几何服务：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r apps/geometry-service/requirements.txt
uvicorn app:app --app-dir apps/geometry-service --reload --port 8788
```

首次安装后也可以直接使用：

```powershell
npm run dev:geometry
npm run dev:web
```

两个命令分别在两个终端运行。浏览器进入“编辑搭配”后，每次选择都会调用本地几何服务生成完整试样 DXF；“下载全部数据”会实际下载包含 R12 DXF、生产清单和几何校验报告的 ZIP。运行 `npm run test:geometry` 可检查全部版型选项。

Cloudflare 部署前请先阅读 [`docs/CLOUD_DEPLOYMENT.md`](docs/CLOUD_DEPLOYMENT.md)。

素材投放规则见 [`docs/ASSET_INTAKE.md`](docs/ASSET_INTAKE.md)。
