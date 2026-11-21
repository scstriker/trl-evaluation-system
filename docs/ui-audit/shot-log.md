# 截图对照日志（route-matrix 验收）

**Viewport:** 1280×800 · **脚本:** `scripts/ui_audit_shot.py`  
**约定:** `{slug}.png` = 当前态（整改后全量重拍）；有明确 before 的另存 `{slug}-before` 或保留早期浏览器基线文件名。`{slug}-after.png` = 整改后副本（便于并排对比）。

## 对照策略

| 类型 | 处理 |
|------|------|
| P0 核心（报告/评价） | 保留改前 `trl-detail.png` / `report-preview.png`（浏览器基线）+ `*-after.png` |
| P1 评审簇 | `review-board` / `round-detail` / `task-detail` 有 `*-after.png`；当前 `{slug}.png` 同为 after |
| 仅 disclosure / toolbar 收敛的屏 | **与基线布局同构、仅说明折叠** → 记「无实质视觉变化」；当前 png 即验收帧 |
| 错误/权限 | 单帧即可：`err-404`、`err-403`、`project-new`(admin 403)、`mrl-detail`(404，B14 接受) |

## 矩阵清单

### anon
| slug | 文件 | 对照 |
|------|------|------|
| login | `anon/login.png` | 无实质变化（品牌页） |
| register | `anon/register.png` | 同上 |
| err-404 | `anon/err-404.png` | DEBUG 下为 Django 调试 404；自定义 `404.html` 在非 DEBUG 生效 → **接受** |

### gangyan
| slug | 文件 | 对照 |
|------|------|------|
| account | `gangyan/account.png` | 无实质变化 |
| trl-list | `gangyan/trl-list.png` | after；说明折叠 |
| mrl-list | `gangyan/mrl-list.png` | 无实质变化 |
| report-list | `gangyan/report-list.png` | 无实质变化 |
| trl-detail | `trl-detail.png` ↔ `trl-detail-after.png` | **P0 before/after** |
| trl-detail-l5 | `trl-detail-l5.png` | after（随 P0） |
| trl-item-modal | `trl-item-modal.png` | after；B06 接受 |
| mrl-detail | `mrl-detail.png` | 404 · B14 接受 |
| report-preview | `report-preview.png` ↔ `report-preview-after.png` | **P0 before/after** |
| project-new | `project-new.png` | 无实质变化 |
| review-history | `review-history.png` | after（disclosure） |
| inbox | `inbox.png` | 无实质变化 |
| rules-process | `rules-process.png` | after |
| err-403 | `err-403.png` | 接受 |

### admin
| slug | 文件 | 对照 |
|------|------|------|
| account … audit-list / recycle-bin | 各 `admin/*.png` | 列表类：说明/徽章弱化；无单独 before |
| review-board | `review-board.png` + `review-board-after.png` | **P1 after** |
| trl-detail / report-preview | `admin/trl-detail.png` 等 | after（机构视角） |
| front-matter | `front-matter.png` | after |
| round-detail | `round-detail.png` + `round-detail-after.png` | **P1 after** |
| round-assign | `round-assign.png` | after |
| review-history | `review-history.png` | after |
| expert-* / fees / rules-* / settings / inbox | 各 png | after 或无实质变化 |
| project-new | `project-new.png` | 403 · B15 接受 |

### expert01
| slug | 文件 | 对照 |
|------|------|------|
| account / task-list / inbox | 各 png | 无实质变化或轻量 |
| task-detail | `task-detail.png` + `task-detail-after.png` | **P1 after** |

## 未单独 URL 的共享片段

随宿主页验收：`base.html`、`_project_head.html`、`_expert_panel.html`、`_item_modal.html`（见 P0/P1 帧）。

## 缺拍说明

| 矩阵项 | 说明 |
|--------|------|
| register-done | 需注册会话；与 register 同认证壳 → **与基线无变化（跳过）** |
| project-new-mrl | 与 project-new 同模板 query → **与基线无变化（跳过）** |
| expert-edit | 与 expert-new 同 `expert_form.html` → **与基线无变化（跳过）** |
| err-500 | 未安全触发 → **跳过**（模板 `500.html` 存在） |
