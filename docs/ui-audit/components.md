# Page zones 组件约定

在 **Layout rhythm**（`page-lead` / `page-toolbar` / `alert-stack` / `disclosure` 等）之上，全站复用下列语义类。实现见 `static/css/app.css` **Page zones**。禁止每页发明新的首屏结构；禁止 Tailwind 全量重写。

## 首屏原则

任意工作台页首屏只回答三件事：**进度怎样？现在要做什么？点哪里做？**

- 长说明默认进 `details.disclosure`，或压成 3～4 条非阻断 `checklist`。
- 阻断 / 待办用 `checklist--blockers`，禁止 `；` 拼接长句。
- 同一事实只出现一次（尤其退回 + 专家整改）。
- 主操作放 `page-hero__actions` 或既有 `page-toolbar`，与说明分离。

## 类名

| 类 | 用途 | 典型结构 |
|----|------|----------|
| `page-hero` | 首屏两列：左摘要/清单，右主按钮 | `.page-hero` > `__main` + `__actions` |
| `page-hero__title` | hero 内小标题（如「导出准备」） | 可选 |
| `checklist` | 短 bullet 说明 | `<ul class="checklist">` |
| `checklist--blockers` | 待办/阻断清单（带左边框） | 同上 + modifier |
| `status-summary` | 单行状态：tag + 日期 + 短 hint | 替代 alert 内大段加粗 |
| `upload-card` | 单类上传材料卡片 | `__head`（标题+状态 tag）+ `__meta` + `__actions` / `file-upload-row` |
| `file-upload-row` | 文件 input + 上传按钮横排，限制宽度 | 放在 `upload-card` 内 |
| `rectify-table` | 整改表：条目 nowrap、要求列限宽 | 常配 `data-table--compact` |
| `alert--compact` | 更紧的 alert（仅当必须保留 alert 时） | `.alert.alert--compact` |

既有可继续用：`page-lead`、`page-toolbar`、`alert-stack`、`kpi-row`、`disclosure`、`enterprise-doc-row`（逐步迁移到 `upload-card`）。

## 反模式

- 首屏连续 3+ 全宽 alert
- `request_blockers|join:"；"` 一类拼接
- 原生 file input 与下载/上传挤在同一视觉行抢焦点
- 机构退回黄框与专家评审内嵌黄框同屏重复
- KPI 被压到大量提示下方

## 与审计产物

- 路由矩阵：[`route-matrix.md`](route-matrix.md)
- 问题与评分：[`findings.md`](findings.md)
- 批量截图：`python scripts/ui_audit_shot.py`
