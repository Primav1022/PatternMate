# PatternMate 项目交接文档

> 面向完全没有历史上下文的新会话。最后更新：2026-08-10。工作目录固定为 `F:\CHI27`。

## 1. 我们在做什么

这是一个面向非专业用户的服装智能制版工作台，产品名为 **PatternMate / 智能制版工作台**。目标流程是：

1. 用户录入人体尺寸，并生成、确认与尺寸对应的 3D 数字人体。
2. 用户用自然语言和 AI 设计助手交流；系统将模糊需求映射到受控的服装语义标签和结构约束，并对参考库排序。
3. 用户选择参考款后进入“编辑搭配”，在原型 DXF 上选择领口、袖型、特殊设计或衬衫结构；后端实时放码、替换/重绘接口、自动排版并生成完整 DXF。
4. 用户选择面料、颜色和工艺。只有印花工艺进入“印花创作”，其他工艺直接导出。
5. 印花支持正背面独立设计、密度印花、定位放置、上传素材、裁剪、缩放、旋转、撤销/重做。
6. 最终下载可用于后续工业流程的 ZIP，包含 R12 DXF、生产清单、尺寸/规则、来源和自动校验报告。
7. 新增科研 3D 试穿：以拟合后 SMPL 数字人体和当前组合 DXF 为输入，进行真实版片缝合、布料模拟与碰撞求解。**这一项尚未完成。**

产品约束：

- 当前视觉系统使用固定的暖白、深棕、橙色配色，不再提供主题色切换。
- 中文为默认语言，支持中英文切换。
- 3D 仅用于受开关控制的科研视觉预览，不参与工业 DXF 校验或生产导出。
- 用户已经推翻了早期“全局不要 3D”的要求；现在明确需要真实 3D 人体与服装试穿。不要再按旧要求删除 3D。

## 2. 代码与服务结构

- `apps/web`：React + Vite 前端。
- `apps/geometry-service`：FastAPI；语义分析、参考排序、DXF 组合、校验、SVG 预览和生产 ZIP 导出。
- `apps/tryon-service`：FastAPI；SMPL 人体拟合、GPU 作业队列和未来的真实布料试穿。
- `apps/worker`：Cloudflare Worker/云端编排代码。
- `data/ir/tshirt_v2/pattern_ir`：62 条新版 T 恤/Polo 权威标注。
- `data/ir/shirt_v2/pattern_ir`：31 条新版衬衫权威标注。
- `data/ir/v1_rule_ready`：旧版规则数据；T 恤旧数据不再作为权威库，部分旧衬衫只作为隐藏的 DXF 几何供体。
- `data/seed/dxf`：正式 DXF 种子目录；索引也会检查规定的其他来源目录。
- `packages/catalogs/src/pattern-options.v1.json`：版型选项目录。
- `config/model.local.json`：本地模型/API 配置，含密钥，不得提交或发送到浏览器。
- `docs/realistic-3d-tryon.md`：真实 3D 试穿技术说明。
- `scripts/dev-local.py`：本地三服务启动器。

重要静态素材：

- 简图 Logo：`apps/web/public/brand/logo.svg`
- 文字 Logo：`apps/web/public/brand/patternmate-wordmark.svg`
- favicon：`apps/web/public/brand/favicon.svg`
- 四步导航图标：`apps/web/public/brand/measure.svg`、`design.svg`、`styling.svg`、`print.svg`
- 人体测量图：`apps/web/public/measurement/measurement-diagram.png`
- 参考图：`apps/web/public/reference-images/v2/<CASE_ID>/cover.*`

## 3. 当前数据状态

- 面向参考推荐的权威记录：93 条（62 条 T 恤/Polo + 31 条衬衫）。
- 全项目已发现 DXF：109 个。
- 为补足新版衬衫组件标注，额外保留了 14 条旧衬衫记录作为 `_donor_only` 隐藏几何供体。
- 隐藏供体不会进入参考图、AI 推荐或公开目录。
- 四条旧 case 已按用户要求从参考库删除：`C2330115`、`C2430104`、`C2430271`、`C2590742`。
- `C2690430` 的对位点匹配质量过低，不允许作为组件供体。
- 标注与 DXF 的基础关联规则：从文件名提取不区分大小写的 `C\d+`；case 编号相同即匹配。

## 4. 已完成并验证的工作

### 4.1 主流程和前端

- 已实现首页、人体尺寸、服装设计、编辑搭配、印花创作四步流程及顺序限制。
- 首页使用新版 Logo、固定配色和滚动图片墙。
- 尺寸页有详细测量指导、自适应/可拖动布局、浏览器记忆提示和“生成并预览 3D 人体”确认流程。
- 服装设计页左侧已改为完整对话区；服务端支持完整历史、否定/反悔、结构约束和受控生成式 UI；阿里/OpenAI 兼容 API 不可用时会降级到规则解析。
- 编辑搭配默认显示专业 DXF，可切换 2D 设计预览；支持平移、滚轮缩放、网格、图层显隐、悬停强调、自动排版按钮、面料/工艺和面料颜色。
- 印花正背面状态独立，支持密度/定位/无印花、素材库和私库、拖动、滚轮/边角缩放、旋转、裁剪、辅助线、撤销/重做以及 Delete 删除。
- 导出已修复 React 事件对象造成的 `Converting circular structure to JSON` 问题；只导出实际使用的印花和用户上传素材。

### 4.2 DXF 组合器

- 组合器不再把“没有语义标签”直接当成“没有 DXF 几何”。
- 组件选择顺序为：精确标签供体 → 最接近的兼容几何供体 → 原型纸样参数化几何。
- 新版衬衫库缺失的袖口语义已由隐藏的旧衬衫 DXF 供体补齐；当前 40 个版型选项均不是 `unavailable`。
- V 领不再只替换领条/领片，而会重绘宿主衣身领圈：前片生成深 V，后片同步改为合理的浅后领曲线；肩点保持锁定，并同步重绘重叠的生产裁剪边。
- 对缺少原子 `line_role=neckline`、但原 DXF 有轮廓的样本，组合器会合并 `edge_chains`，并尝试从实际 DXF 上缘边界推断领圈链。
- 已对默认原型 `C2590529` 做实时接口验证：V 领前片深度 `93.727 mm`，后片深度 `21.720 mm`，两者都发生变化；组合状态 `valid`，`trial_ready=true`。
- `POST /compose` 实测返回完整 SVG、版片列表、来源、校验指标和配方哈希。
- 几何服务健康标记为 `service_build=prototype-parametric-v2`，启动器只会复用这一版本，避免继续连接旧进程。

关键文件：

- `apps/geometry-service/composition_engine.py`
- `apps/geometry-service/app.py`
- `apps/geometry-service/tests/test_composition.py`
- `apps/geometry-service/tests/test_v2_registry.py`
- `scripts/dev-local.py`

### 4.3 3D 人体

- 本地 GPU 为 RTX 4060 Laptop 8GB，CUDA 可用。
- 用户已有环境：`F:\Anaconda\envs\pytorch`；Python 3.9.20，PyTorch CUDA、SMPL-X、Warp 可用。
- 独立布料实验环境：`F:\CHI27\.venv-cloth`；Newton 1.4.0、Warp 1.16.0 可在 CUDA 上运行。
- 所有缓存/结果都应放在 `F:\CHI27\.cache`，不要下载到 C 盘。
- SMPL 人体拟合会使用身高、胸围、腰围、肩宽、领围、袖长和上臂围；不同测试尺寸已验证会产生不同 GLB 与哈希。
- `Research3D.tsx` 曾因缺失数值直接调用 `.toFixed()` 崩溃，已做安全数值处理；不要恢复无保护调用。

### 4.4 最近一次验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s apps\geometry-service\tests -v
npm --prefix apps/web run build
```

结果：18 项 Python 测试全部通过；Vite production build 通过。仅有 bundle 超过 500 kB 的警告，不是构建失败。

实时服务最近的健康状态：

- 几何服务 `127.0.0.1:8788`：`prototype-parametric-v2`，93 条公开 IR、14 条隐藏供体、40 个版型选项。
- 3D 服务 `127.0.0.1:8790`：CUDA/SMPL/Warp 均可用，但 `cloth_solver_available=false`。

进程 ID 会变化，不要写死或盲杀进程。

## 5. 当前卡在哪里

### 5.1 真实 3D 服装试穿尚未完成

这是当前最重要的未完成项。GPU、SMPL 和 Warp/Newton 已安装并不等于真实试穿已经完成。现有 `apps/tryon-service/glb.py` 中的 `garment_mesh()` 仍是程序化外壳，不是从组合 DXF 缝出来的服装，不能向用户展示或称为真实试穿。

目前前端明确显示“3D真实试穿未就绪”，3D 作业接口会返回 HTTP 503，防止再次输出假的占位服装。健康接口会列出四个 blocker：

1. DXF 闭合版片提取与三角化。
2. 经过验证的缝合边/对位点图。
3. 版片在拟合人体周围的 3D 初始放置。
4. GPU 布料缝合、人体碰撞、自碰撞求解与 GLB 输出。

相关文件：

- `apps/tryon-service/app.py`
- `apps/tryon-service/glb.py`
- `apps/tryon-service/body_fit.py`
- `apps/web/src/Research3D.tsx`
- `apps/web/src/PatternPreview.tsx`

### 5.2 并非所有原型的领圈语义都完整

部分旧 DXF 确有几何，但标注存在前片/后片领圈链缺失、piece role 错分或版片方向异常。当前推断逻辑覆盖了常规样本，但不能宣称所有 109 个 DXF 都已经可无人工复核地重绘任意领口。

尤其曾暴露问题的 case：`C2490260`、`C2490443`、`C2590428`、`C2590582`。不要通过删除测试、伪造标签或只画一条装饰线来“通过”；应该建立闭合轮廓和接口级验证。

## 6. 下一步计划（按顺序执行）

### 阶段 A：建立 DXF → 3D 的可靠中间描述

1. 在几何服务中，从最终组合后的实体而不是原始标注重新构建每个版片的闭合生产轮廓。
2. 输出 `tryon_descriptor`：版片二维顶点、三角形、版片角色、边链、缝合配对、对位点、单位、配方哈希。
3. 领圈、袖窿/袖山、肩缝、侧缝和袖底缝必须有明确的成对边；缺少关键配对时 `tryon_ready=false`，但不影响 DXF/2D/工业导出。
4. 为前片、后片、袖片和领口部件分别写单元测试；组合后再提取，不能直接沿用供体的旧坐标或旧关系。

### 阶段 B：生成真正与 DXF 一致的服装网格

1. 在 tryon-service 读取 `tryon_descriptor`，按真实毫米比例三角化。
2. 根据 SMPL 关节和人体包围面初始化前后片与袖片位置。
3. 对配对边重采样，创建缝合约束；保留领口、袖口和下摆为开口边。
4. 先离线输出“未模拟但由 DXF 版片构建”的调试 GLB，检查版片角色、朝向、法线和尺度；这个调试结果不得作为正式试穿开放。

### 阶段 C：接入 Newton/Warp GPU 布料求解

1. 使用 `.venv-cloth` 中 Newton Style3D/Warp 的 CUDA 路径。
2. 加入拉伸、弯曲、缝合、人体碰撞和自碰撞约束。
3. 草模采用低网格和少迭代，精算采用更高网格；RTX 4060 8GB 上一次只运行一个 GPU 作业。
4. 用户连续修改时取消旧精算，只让最新 `avatar_hash + recipe_hash + material_hash` 更新 UI。
5. 连续运行显存测试，确认任务结束释放张量和碰撞结构。

### 阶段 D：重新开放 3D UI

只有以下条件同时满足才将 `cloth_solver_available` 改为 `true`：

- 服装网格确实来自本次组合 DXF。
- 缝合图通过接口校验。
- GPU 求解真实运行，不是 sleep 或程序化 shell。
- 输出 GLB 的 recipe hash 与当前页面完全一致。
- 至少完成一套 T 恤/Polo 的自动化端到端测试和人工视觉验收。

衬衫应在 T 恤/Polo 稳定后再开放，领座、门襟、克夫和袖衩的缝合关系更复杂。

### 阶段 E：继续加强 DXF 几何

1. 用图结构重建闭合外轮廓，摆脱“最高处就是领圈”的方向假设。
2. 根据肩缝端点、前/后中线和对位点定位领圈，而不是只依赖 `line_role`。
3. 对每个可选参考原型跑圆领→V领、袖型替换和导出重读测试。
4. 自动排版只能平移/旋转展示坐标，绝不能改变生产几何或接口长度。

## 7. 本地启动方式

推荐在 `F:\CHI27` 的 PowerShell 中：

```powershell
npm run dev:local
```

它会尝试启动/复用：

- Web：Vite，通常是 `http://127.0.0.1:5173`
- Geometry：`http://127.0.0.1:8788`
- Try-on：`http://127.0.0.1:8790`

如果出现 `[Errno 10048]`，说明端口已被旧进程占用。先检查健康接口，不要重复启动：

```powershell
Invoke-RestMethod http://127.0.0.1:8788/health
Invoke-RestMethod http://127.0.0.1:8790/research/health
```

几何服务只有返回 `service_build: prototype-parametric-v2` 才可复用。若端口被错误进程占用，应先通过端口查到 PID，再核对 `CommandLine` 确实是本项目对应服务，最后只终止这个准确 PID。绝对不要按进程名批量杀 Python。

单独启动：

```powershell
npm run dev:geometry
npm run dev:tryon
npm run dev:web
```

API 配置路径：`F:\CHI27\config\model.local.json`。只修改该文件；不要把 API key 写进前端 `.env`、源码、日志或交接文档。

## 8. 绝对不要再踩的坑

1. **不要再展示与 DXF 无关的 3D 占位服装。** 之前的胶囊人体/程序化衣服严重损害可信度。没有真实版片缝合和布料求解就必须禁用入口并明确说明。
2. **不要把“标注没有部件类型”当成“DXF 没有几何”。** 先查原型、同编号 DXF、旧衬衫隐藏供体、边链和轮廓，再决定是否无法生成。
3. **领口修改不能只换领片或前片。** 必须同时更新前后衣身领圈及领口部件；后领通常是浅弧而不是深 V，但必须同步重新计算并在元数据中可验证。
4. **不要把装饰性 PNG 盖在 DXF 上冒充组合。** 中央专业视图必须来自实际组合实体；PNG 只用于左侧选项示意。
5. **不要让旧服务悄悄复用。** `8788 /health` 必须检查 build marker。只检查 `"ok":true` 曾导致页面持续显示已经从源码删除的旧错误。
6. **不要下载虚拟环境、模型或缓存到 C 盘。** 使用 `F:\Anaconda\envs\pytorch`、项目内 `.venv*` 和 `F:\CHI27\.cache`。设置 `PIP_CACHE_DIR`、`TORCH_HOME`、`XDG_CACHE_HOME`、`TEMP/TMP`、`WARP_CACHE_PATH`。
7. **不要将 SMPL 模型打包进公开镜像、静态资源或工业 ZIP。** 仅以只读目录挂载，且受 `ENABLE_RESEARCH_3D` 控制。
8. **不要把 3D 视觉结果当工业正确性证明。** 生产导出只依据组合 DXF 和几何校验。
9. **不要直接序列化 React DOM 事件。** 导出处理器必须使用无参闭包；否则会出现 `Converting circular structure to JSON`。
10. **不要让正面和背面共享同一印花选择/状态。** 两面可以分别为密度、定位或无印花，切换素材不能污染另一面。
11. **不要导出整个内置印花库。** 只导出实际使用的素材及其位置、尺寸、旋转、裁剪、密度、间距和视图信息。
12. **不要恢复主题切换。** 当前已采用固定新版配色。
13. **不要在未通过校验时启用下一步或工业导出。** 左右两处校验状态必须来源于同一个最新 compose 响应，过期请求不能覆盖新请求。
14. **不要循环自动替换失败选项。** 校验失败保留上一个有效组合，只给经过服务端预校验的替代项；没有替代项就让用户手动调整。
15. **不要随意清理工作区。** 当前目录不是可用的 Git 仓库状态，无法依赖 `git reset` 恢复；用户素材和历史修改都要保留。
16. **注意历史文件存在中文编码乱码。** 不要用全文件格式化或错误编码重写；只做局部补丁。新文档统一 UTF-8。
17. **不要宣称自动校验试样等于可直接量产。** 当前导出报告必须保留“自动校验试样、未经人工纸样师确认”的边界说明。

## 9. 新会话接手后的首轮检查清单

1. 阅读本文件、`docs/realistic-3d-tryon.md`、`apps/geometry-service/composition_engine.py` 和 `apps/tryon-service/app.py`。
2. 调用两个健康接口，确认端口与 build marker；不要先盲目启动第二套服务。
3. 运行 `npm run test:geometry` 和 `npm run build:web`。
4. 在网页选 `C2590529`，分别生成圆领和 V 领，确认 SVG/DXF 中前后片领圈均不同，且 `validation.metrics.body_neckline.chains` 同时包含 `front_body` 和 `back_body`。
5. 检查 `/pattern-catalog` 不存在 `mapping_status=unavailable`。
6. 在真实布料链路完成前保持 `cloth_solver_available=false`，不要绕过前端禁用。
7. 下一段主要开发从“组合后 DXF 的闭合版片与缝合图导出”开始，而不是再次调整一个程序化 3D 外壳。

