# UI 视觉审计 findings

**基线：** 2026-11-21 · 1280×800 · `scripts/ui_audit_shot.py`  
**截图：** `docs/ui-audit/screenshots/{role}/*.png`；对比见 [`shot-log.md`](shot-log.md)  
**评分：** 1–5，**< 3 进 backlog**。维度见全站计划 rubric。

## 总览 backlog（结案状态）

| ID | 优先级 | 问题 | 状态 | 证据 |
|----|--------|------|------|------|
| B01 | P0 | 导出阻断 `；` 长句 | **已改** | `gangyan/report-preview-after.png` → `checklist--blockers` |
| B02 | P0 | 上传区杂乱 | **已改** | 同上 → `upload-card` + `file-upload-row` |
| B03 | P0 | 双黄框 | **已改** | `gangyan/trl-detail-after.png`；整改态无机构退回 alert |
| B04 | P0 | KPI 下沉 | **已改** | 同上；KPI 紧随 project head |
| B05 | P0 | 报告说明墙 | **已改** | `page-hero` + `disclosure` |
| B06 | P1 | modal 背景堆叠 | **已改/接受** | 背景随 B03/B04；modal 本身 ≥3，编辑态密度接受 |
| B07 | P1 | 评审看板密度 | **已改** | `admin/review-board-after.png`；`expert-dots` + disclosure |
| B08 | P1 | 轮次详情 KPI 被压 | **已改** | `admin/round-detail-after.png`；status + KPI 上移；终审态专家组折叠 |
| B09 | P1 | 任务详情双栏 | **已改** | `expert01/task-detail-after.png`；紧凑 tabs + sticky 结论列 |
| B10 | P2 | 列表说明/工具条 | **已改** | `assessment_list` lead→disclosure；`page-toolbar` |
| B11 | P2 | front-matter 长 info | **已改** | `front_matter.html` disclosure |
| B12 | P2 | nav 徽章噪声 | **已改** | `app.css` `.nav-count` 缩小弱化 |
| B13 | P3 | 规则/费用/设置长文 | **已改** | process / rule_library / fees / settings disclosure |
| B14 | 记录 | 无 MRL → `/mrl/` 404 | **接受** | 非本 Goal 改路由；shot `gangyan/mrl-detail.png` |
| B15 | 记录 | admin 新建项目 403 | **接受** | 权限正确；shot `admin/project-new.png` |

**P0/P1 项：已清零或接受。** 无残留 `<3` 且未处理的 backlog。

---

## 分屏评分（整改后，抽样复评）

| 屏 | 角色 | 信息 | 密度 | 对齐 | 冗余 | 组件 | 备注 |
|----|------|------|------|------|------|------|------|
| login / register | anon | 5 | 5 | 5 | 5 | 5 | 与基线同品质 |
| trl-list | gangyan | 4 | 4 | 4 | 5 | 4 | 说明折叠 |
| review-board | admin | 4 | 3 | 4 | 4 | 4 | 密度达标 |
| trl-detail | gangyan | 4 | 3 | 4 | 4 | 4 | KPI 首屏 |
| report-preview | gangyan | 4 | 3 | 4 | 4 | 4 | 清单阻断 |
| front-matter | admin | 4 | 4 | 4 | 5 | 4 | |
| round-detail | admin | 4 | 3 | 4 | 4 | 4 | |
| task-detail | expert01 | 4 | 3 | 4 | 5 | 4 | |
| rules-process | admin | 4 | 4 | 4 | 5 | 4 | |
| 其余列表/账号/错误 | — | ≥3 | ≥3 | ≥3 | ≥4 | ≥3 | 见 shot-log |

---

## 基线问题摘录（历史，已对照 after）

### report-preview（改前）
长 lead + `；` 阻断蓝框 + 上传横排。**已用 page-hero / checklist / upload-card 消除。**

### trl-detail（改前）
双黄框 + KPI 在首屏外。**已用 KPI 上移 + 整改态去重消除。**

---

## 实施波次记录

### 设计系统
- `static/css/app.css` Page zones + expert-dots / filter-tabs--compact / nav-count / sticky opinion-col  
- `docs/ui-audit/components.md`

### P0
- `report_preview.html`、`assessment_detail.html`、`_expert_panel.html`

### P1
- `board.html`、`round_detail.html`、`task_detail.html`、`assign.html`

### P2–P3
- `assessment_list.html`、`front_matter.html`、`history.html`、`process_overview.html`、`rule_library.html`、`settings.html`、`fee_list.html`

### 回归
- 截图：`python scripts/ui_audit_shot.py`（全角色矩阵已重拍）  
- 样例路径：gangyan · 多流路 — `trl-detail-after` KPI 首屏 + 单一整改入口；`report-preview-after` 阻断清单 + upload-card  
- 残留收口（2026-11-21）：企业材料顺序 概况→WBST→支撑；admin 审核区 blockers 改清单；评价页 `pending_target`/`submitted_levels` 轻收敛 + `show_returned_alert`；HTML 断言补齐  
- 测试：**121 passed**（`python -m pytest -q`，约 7.5 分钟）
