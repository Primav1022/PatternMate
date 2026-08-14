# PatternMate 前端 Mock 记录

本地联调 / 演示时临时绕过后端门禁。上线前请关掉或改回真实流程。

## 开关位置

文件：`src/main.tsx`

| 标记 | 作用 | 当前 |
|---|---|---|
| `USE_MOCK_CATALOG = true` | 不请求 `/catalog`，用本地 `fallbackReferences` + `/reference-images/v1/{id}/cover.jpg` | **关** |
| `MOCK UI` in `saveMeasurements` | 「确认并继续」跳过 3D 人体预览门禁，直接进入服装设计 | **开** |
| `MOCK UI` in design `onNext` | 「确认参考款」始终可点；未选时自动选第一款 supported 参考 → 编辑搭配 | **关** |

## 本地参考图

- ID 列表：`MOCK_REFERENCE_IDS`（24 款）
- 封面路径：`public/reference-images/v1/<id>/cover.jpg`

## 恢复真实流程

1. 设 `USE_MOCK_CATALOG = false`
2. 恢复 `saveMeasurements`：须先 3D preview ready 再进 design
3. 恢复 design `onNext`：须已选 supported 参考款
