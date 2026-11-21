# UI 审计路由矩阵

**Viewport:** 1280×800  
**Seed:** `python manage.py seed_sample`（本机 DB 已跑过；ID 随重建会变，截图前用下方「刷新 ID」核对）  
**账号：** `admin` / `gangyan` / `expert01`（密码见仓库 README，此处不复述）  
**基线日：** 2026-11-21

## 种子样例（当前 DB）

| 别名 | 用途 | 当前 ID |
|------|------|---------|
| **P_DUOLIU** | gangyan · 多流路在线自动进样装置（整改中，P0） | `5dd79777-7e25-4699-9fa5-1a19bc1493f6` |
| **A_DUOLIU** | 上项 TRL assessment | `373a5386-d047-4200-81ad-cdaeb5539f64` |
| **R_DUOLIU** | 上项评审轮次 1（rectifying） | `841300fd-bc3a-4f28-a51b-653b6a24ee3d` |
| **T_E01** | expert01 任务（assignment） | `58` |
| **I_L5** | 多流路 TRL5 条目（modal） | `f816490f-4238-4f83-8bd5-117799027343` |
| **P_ZHONGNAN** | 中南 · 光谱数据智能解析软件（front-matter 样例） | `454daaa3-6e43-4bd2-861d-f36d6e2e911c` |
| **E_01** | 专家库详情 expert01 | `1` |

刷新 ID（可选）：

```bash
python manage.py shell -c "from apps.evaluations.models import EvaluationProject as P; p=P.objects.get(name='多流路在线自动进样装置'); print(p.id)"
```

截图路径约定：`docs/ui-audit/screenshots/{role}/{slug}.png`；整改后再拍 `{slug}-after.png`。

---

## 簇 A — 认证与账号

| slug | 屏幕 | URL | 角色 | 模板 | 备注 |
|------|------|-----|------|------|------|
| login | 登录 | `/login/` | anon | `accounts/login.html` + `_auth_brand.html` | |
| register | 注册 | `/register/` | anon | `accounts/register.html` | |
| register-done | 注册完成 | `/register/done/` | anon | `accounts/register_done.html` | 需已提交注册会话或直达看空态 |
| account | 账号信息 | `/account/` | admin / gangyan / expert01 | `accounts/account.html` | 三角色各拍一帧 |

## 簇 B — 导航列表

| slug | 屏幕 | URL | 角色 | 模板 | 备注 |
|------|------|-----|------|------|------|
| trl-list | TRL 项目列表 | `/trl/` | admin, gangyan | `evaluations/assessment_list.html` | |
| mrl-list | MRL 项目列表 | `/mrl/` | admin, gangyan | 同上 | MRL 未启用时仍应可打开或有提示 |
| report-list | 报告列表 | `/reports/` | admin, gangyan | `reports/report_list.html` | |
| review-board | 专家评审看板 | `/reviews/` | admin | `reviews/board.html` | |
| task-list | 专家任务列表 | `/reviews/tasks/` | expert01 | `reviews/task_list.html` | |
| expert-list | 专家库 | `/experts/` | admin | `experts/expert_list.html` | |
| user-list | 用户管理 | `/users/` | admin | `accounts/user_list.html` | |
| audit-list | 审计 | `/audit/` | admin | `audit/audit_list.html` | |

## 簇 C — 项目工作台（P0）

| slug | 屏幕 | URL | 角色 | 模板 | 种子 / query |
|------|------|-----|------|------|--------------|
| trl-detail | TRL 评价 | `/projects/{P_DUOLIU}/trl/` | gangyan, admin | `assessment_detail.html` + `_project_head.html` + `_expert_panel.html` | 多流路整改中 |
| trl-detail-l5 | TRL 某级 | `…/trl/?level=5` | gangyan | 同上 | 打开 L5 |
| trl-item-modal | 条目 modal | `…/trl/?level=5&item={I_L5}` | gangyan | + `_item_modal.html` | 截图需含打开的 modal |
| mrl-detail | MRL 评价 | `/projects/{P_DUOLIU}/mrl/` | gangyan | 期望 `assessment_absent.html` | **基线实测 404**（见 findings B14）；有 MRL 的项目另选 ID 补拍 |
| report-preview | 评价报告 | `/reports/projects/{P_DUOLIU}/` | gangyan, admin | `reports/report_preview.html` | **用户已截图验收** |
| front-matter | 报告前置信息 | `/reports/projects/{P_ZHONGNAN}/front-matter/` | zhongnan→用 admin 代看或登录中南 | `reports/front_matter.html` | 已填样例 |
| project-new | 新建项目 | `/projects/new/` | gangyan / hexin | `evaluations/project_form.html` | |
| project-new-mrl | 新建含 MRL | `/projects/new/?system=mrl` | gangyan | 同上 | |
| recycle-bin | 回收站 | `/recycle-bin/` | admin | `evaluations/recycle_bin.html` | 注意：非 `/recycle/` |

## 簇 D — 评审深度

| slug | 屏幕 | URL | 角色 | 模板 | 种子 |
|------|------|-----|------|------|------|
| round-detail | 评审轮次详情 | `/reviews/rounds/{R_DUOLIU}/` | admin | `reviews/round_detail.html` | 多流路 round 1 |
| round-assign | 分配专家 | `/reviews/rounds/{R_DUOLIU}/assign/` | admin | `reviews/assign.html` | 席位可能已满，仍拍布局 |
| review-history | 评审历史 | `/reviews/assessments/{A_DUOLIU}/history/` | gangyan, admin | `reviews/history.html` | |
| task-detail | 专家任务详情 | `/reviews/tasks/{T_E01}/` | expert01 | `reviews/task_detail.html` | assignment 58 |
| inbox | 站内信 | `/inbox/` | admin / gangyan / expert01 | `notifications/inbox.html` | 注意：非 `/notifications/` |
| expert-detail | 专家详情 | `/experts/{E_01}/` | admin | `experts/expert_detail.html` | |
| expert-new | 新建专家 | `/experts/new/` | admin | `experts/expert_form.html` | |
| expert-edit | 编辑专家 | `/experts/{E_01}/edit/` | admin | `experts/expert_form.html` | 与 new 同模板，可只拍一帧注明 |

## 簇 E — 规则与专家费用

| slug | 屏幕 | URL | 角色 | 模板 | 备注 |
|------|------|-----|------|------|------|
| rules-process | 评价流程 | `/rules/process/` | admin, gangyan | `rules/process_overview.html` + `_rules_tabs.html` | |
| rules-trl | TRL 细则库 | `/rules/trl/` | admin | `rules/rule_library.html` | |
| rules-mrl | MRL 细则库 | `/rules/mrl/` | admin | 同上 | |
| expert-fees | 专家费用 | `/experts/fees/` | admin | `experts/fee_list.html` | |

## 簇 F — 系统与错误

| slug | 屏幕 | URL | 角色 | 模板 | 备注 |
|------|------|-----|------|------|------|
| settings | 系统设置 | `/settings/` | admin | `accounts/settings.html` | |
| err-403 | 403 | 企业访问 `/users/` 或 `/settings/` | gangyan | `403.html` | |
| err-404 | 404 | `/this-page-does-not-exist-ui-audit/` | any | `404.html` | |
| err-500 | 500 | 仅在可安全触发时拍；否则记「跳过」 | — | `500.html` | 勿故意破坏生产态 DB |

## 共享片段（不单独 URL，改一次全站生效）

| 片段 | 路径 | 随哪些屏验收 |
|------|------|--------------|
| base / nav | `templates/base.html` | 所有已登录页 |
| project head | `evaluations/_project_head.html` | trl-detail, report-preview, history… |
| expert panel | `reviews/_expert_panel.html` | trl-detail（整改中） |
| item modal | `evaluations/_item_modal.html` | trl-item-modal |
| item row | `evaluations/_item_row.html` | trl-detail |
| team formset | `reports/_team_formset.html` | front-matter |
| auth brand | `accounts/_auth_brand.html` | login / register |
| rules tabs | `rules/_rules_tabs.html` | 规则簇 |

---

## 拍摄顺序（阶段 1）

1. **anon：** login → register  
2. **gangyan：** account → trl-list → mrl-list → report-list → **trl-detail** → trl-detail-l5 → **trl-item-modal** → **report-preview** → project-new → review-history → inbox → rules-process → err-403  
3. **admin：** account → 列表簇 B 全套 → recycle-bin → round-detail → round-assign → expert-* → fees → rules-* → settings → audit → front-matter（中南项目）→ inbox  
4. **expert01：** account → task-list → task-detail → inbox  

每帧：`browser` 视口 1280×800 → `browser_take_screenshot` → 存入对应 `screenshots/{role}/`。

## 与 `test_accounts_access.py` 对齐

测试覆盖的企业页：`/trl/`、`/mrl/`、`/projects/new/`、`/projects/new/?system=mrl`、`/projects/{id}/trl/`、`?level=`、`/mrl/`、`/reports/`、`/reports/projects/{id}/`、`/rules/process/`、`/account/`。  
管理额外：`/rules/trl/`、`/rules/mrl/`、`/users/`、`/audit/`。  

本矩阵 **刻意更宽**（评审、专家、设置、错误页、modal）；后续扩展测试时以本文件为单一来源增补 URL 列表。
