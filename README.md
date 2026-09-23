# 技术就绪度与制造成熟度评价系统

机械工业仪器仪表综合技术经济研究所（ITEI）技术就绪度（TRL）与制造成熟度（MRL）评价系统。企业自助注册、逐条自评并上传佐证、逐级提交审核；评价机构逐级审核或退回，并出具技术就绪度、制造成熟度或两者综合的 Word 评价报告。系统只承载流程与留痕，不做任何自动达标判断，是否通过由评价机构确认。

## 快速启动

```bash
pip install django python-docx
python manage.py migrate
python manage.py seed_sample
python manage.py runserver
```

浏览器打开 <http://127.0.0.1:8000/>。`seed_sample` 生成的初始账号（初始密码均为 `Trl@2026`，登录后可在“账号信息”修改）：

| 账号 | 类型 | 说明 |
|---|---|---|
| `admin` | 管理员（评价机构） | 审核注册、审核提交、生成报告、细则库、用户管理、审计 |
| `gangyan` | 企业用户 | 钢研纳克：一个项目填写中、一个已提交待审核 |
| `hexin` | 企业用户 | 核芯光电：一个项目待出报告、一个被退回待修改 |
| `zhongnan` | 企业用户 | 中南大学：一个项目已出报告 |
| `jingce` | 企业用户（待审核） | 用于体验注册审核流程，审核通过前无法登录 |

`python manage.py seed_sample --admin-only` 只创建 / 重置管理员账号，不生成样例企业与项目。

## 办理流程

1. 企业在登录页“企业注册”（用户名、联系人、密码、企业全称、统一社会信用代码、手机号、图形验证码），管理员在“用户管理”审核启用。每家企业一个账号，企业只能看到自己的项目。
2. 企业“申报评价项目”：选择技术类型（设备技术 / 一般硬件产品技术 / 计算机软件技术），勾选技术就绪度评价和 / 或制造成熟度评价并确定目标等级。
3. 企业对目标等级以内的每条细则给出自评结论（满足 / 不满足 / 不适用）和说明，上传佐证材料（SHA-256 存档）。
4. 企业“提交审核”：可逐级提交，也可一次提交至目标等级；须从最低未提交级起连续提交，且所选级别全部填完。审核期间已提交级别冻结。
5. 管理员逐级核查，自第 1 级起按顺序“审核通过”（可批量），或填写审核意见（可具体到条目）“退回修改”。
6. 目标等级及以下全部通过后，管理员填写评价组成员与综述，生成三类报告之一；企业在“评价报告”下载。

## 制造成熟度评价细则导入

出厂时制造成熟度只有 MRL 1~10 级定义，尚无逐条细则，企业暂不能申报制造成熟度评价。拿到正式细则后：

```bash
# 1. 按模板 data/templates/制造成熟度评价细则-模板.xlsx 整理细则（需要 openpyxl）
python scripts/build_seed.py mrl 制造成熟度评价细则.xlsx   # 生成 data/seed/mrl_rules_seed.csv
python manage.py import_rules                                  # 导入系统（幂等）
```

`python scripts/build_seed.py mrl-template` 可重新生成空白模板；`python scripts/build_seed.py trl` 从上级目录 `app_GUI.py` 重新生成技术就绪度细则。

## 工程结构

```
config/            Django 配置（SQLite；数据库与附件目录可由 TRL_DB_PATH / TRL_MEDIA_ROOT 指定）
apps/accounts      企业注册、图形验证码、登录状态提示、用户管理、权限口径（permissions.py）
apps/rules         评价体系配置（systems.py）、等级定义与评价细则（import_rules）
apps/evaluations   技术项目、评价（TRL/MRL）、条目、逐级提交与审核（services.py，seed_sample）
apps/evidence      佐证材料：SHA-256、软删除、权限校验下载
apps/audit         审计流水
apps/reports       三类报告：统一正文结构 → 在线预览 + python-docx 导出
data/seed/         种子 CSV；data/templates/ 细则填写模板
static/css/tokens.css   ITEI 设计体系 token（勿改）；app.css 页面样式
tests/             pytest-django（python -m pytest -q）
```

## 设计规范

UI 遵循 `itei-design-system`：白底 1px 边框分层、无圆角卡片、品牌 navy 为主色、每屏至多一处品牌红、渐变只用于页眉、45° 斜切品牌形状、微软雅黑字体栈、单色线性图标，状态色均配文字。
