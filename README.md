# 技术就绪度（TRL）评价系统 Demo

机械工业仪器仪表综合技术经济研究所（ITEI）技术就绪度评价软件演示版。以**功能与流程展示**为主：TRL 1~9 级逐级核验、佐证材料管理、级别审核确认、Word 评价报告生成。系统不做任何自动达标判断，判定结论全部由评价人员在界面选择。

## 快速启动

```bash
pip install django python-docx
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

浏览器打开 <http://127.0.0.1:8000/>，演示账号（统一密码 `Trl@2026`）：

| 账号 | 角色 | 权限 |
|---|---|---|
| `admin` | 系统管理员 | 全部操作 |
| `evaluator` | 评价员 | 新建项目、逐条判定、上传佐证、审核通过本级 |
| `auditor` | 审核员 | 锁定/解锁项目、生成报告、查看审计 |
| `viewer` | 观察员 | 只读 |

`seed_demo` 会生成 4 个状态各异的演示项目：①未开始 ②评价进行中 ③达标待出报告 ④已出报告。重复执行会重建演示项目（`--keep` 仅补账号）。

## 推荐演示路径

1. `evaluator` 登录 → 评价工作台（KPI 统计 + 项目列表）
2. 「新建评价项目」→ 选择评价轨道与目标等级 → 系统物化核验条目（设备 77 / 硬件 44 / 软件 33）
3. 评价工作区：TRL 阶梯导航 → 点击细则「判定」弹窗 → 选择满足/不满足/不适用、填写说明 → 上传佐证（SHA-256）
4. 本级全部判定后「审核通过本级」→ 确认弹窗 → 下一级解锁
5. 打开「演示-高性能 XRF 光学系统（待出报告）」→ 评价报告页 → 在线预览
6. 切换 `auditor` → 「生成 Word 报告」→ 下载 .docx（结构对照《4400 技术就绪度评价报告》）
7. 评价细则库（全部 77 条规则）/ 评价流程 / MRL 框架 / 审计日志

## 工程结构

```
config/            Django 配置（SQLite，静态资源直接由 static/ 提供）
apps/rules         规则库：TRL 等级定义 + 77 条细则 + MRL 1~10 框架（import_trl_rules）
apps/evaluations   评价项目、条目快照、逐级推进状态机（seed_demo）
apps/evidence      佐证材料：SHA-256、软删除
apps/audit         审计流水
apps/reports       报告：在线预览 + python-docx 导出
data/seed/         由 scripts/build_seed.py 从评价细则生成的种子 CSV
static/css/tokens.css   ITEI 设计体系 token（勿改）；app.css 页面样式
tests/             pytest-django（python -m pytest -q）
```

## 设计规范

UI 遵循 `itei-design-system`：白底 1px 边框分层、无圆角卡片、品牌 navy 为主色、每屏至多一处品牌红（当前核验级 / 危险操作）、渐变只用于页眉、45° 斜切品牌形状、微软雅黑字体栈、单色线性图标。
