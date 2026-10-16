# -*- coding: utf-8 -*-
"""初始化数据：管理员账号 + 样例企业账号与各状态的评价项目（口径参照《4400 技术就绪度评价报告》）。

用法：python manage.py seed_sample            # 管理员 + 样例企业与项目（可重复执行，会重建样例项目）
      python manage.py seed_sample --admin-only  # 仅创建 / 重置管理员账号
"""

from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from docx import Document

from apps.accounts.forms import credit_code_check_char
from apps.accounts.models import EnterpriseProfile
from apps.evaluations import services
from apps.evaluations.models import Assessment, CheckItem, EvaluationProject, EvaluationTeamMember
from apps.evidence.services import upload_evidence_file
from apps.experts.sample_data import seed_sample_experts
from apps.notifications.models import Notification
from apps.reports.models import Report
from apps.reports.models import ReportModule
from apps.reports.services import approve_request, get_front_matter, request_export
from apps.reviews import services as review_services
from apps.reviews import fees
from apps.reviews.deadlines import check_deadlines
from apps.reviews.models import ExpertAssignment, RemunerationRecord
from apps.rules.models import RuleItem, Specialty
from apps.rules.systems import TRL

INITIAL_PASSWORD = "Trl@2026"

ADMIN_USERNAME = "admin"

# (用户名, 企业全称, 信用代码前 17 位, 联系人, 手机, 状态)
ENTERPRISES = [
    ("gangyan", "钢研纳克检测技术股份有限公司", "91110000MA0000001", "张工", "13800000001", EnterpriseProfile.STATUS_ACTIVE),
    ("hexin", "核芯光电科技（山东）有限公司", "91370100MA0000002", "李工", "13800000002", EnterpriseProfile.STATUS_ACTIVE),
    ("zhongnan", "中南大学", "12430000MB0000003", "王老师", "13800000003", EnterpriseProfile.STATUS_ACTIVE),
    ("jingce", "江苏精测智能装备有限公司", "91320500MA0000004", "赵经理", "13800000004", EnterpriseProfile.STATUS_PENDING),
]

# 第三方评价组（评价机构填写）
THIRD_TEAM = [
    ("组长", "杜工", "正高级工程师", "测量控制设备及系统评价", "机械工业仪器仪表综合技术经济研究所"),
    ("成员", "李工", "高级工程师", "智能制造与装备技术", "机械工业仪器仪表综合技术经济研究所"),
    ("成员", "王工", "高级工程师", "工业软件测评", "机械工业仪器仪表综合技术经济研究所"),
]

# 自评估工作组（企业填写），按企业用户名
SELF_TEAMS = {
    "gangyan": [
        ("组长", "张工", "研究员 / 项目负责人", "X 射线荧光光谱分析仪器研制", "钢研纳克检测技术股份有限公司"),
        ("成员", "刘工", "高级工程师", "在线取样与流体控制", "钢研纳克检测技术股份有限公司"),
    ],
    "hexin": [
        ("组长", "李工", "高级工程师 / 技术总监", "X 射线光学系统设计", "核芯光电科技（山东）有限公司"),
        ("成员", "陈工", "工程师", "SDD 探测器研发", "核芯光电科技（山东）有限公司"),
        ("成员", "周工", "工程师", "质量管理与检验", "核芯光电科技（山东）有限公司"),
    ],
    "zhongnan": [
        ("组长", "王老师", "教授", "光谱分析与化学计量学", "中南大学"),
        ("成员", "郑老师", "副教授", "工业软件开发与测试", "中南大学"),
    ],
}

OVERVIEW_TEXT = (
    "本项目来源于“十五五”智能制造系统和机器人国家科技重大专项，由钢研纳克检测技术股份有限公司牵头，"
    "针对稀土萃取分离过程成分离线检测时间长、数据获取滞后、工艺参数在线感知不及时等问题，"
    "突破网格化采样、多流路全自动样品传送、严苛环境可靠性设计等关键技术，"
    "研制高能量高稳定激发源、高效降噪光路系统和多参量同步采传模块等核心部件，"
    "开发具备多流路状态监控、流路识别与方法切换协同控制、谱图解算和数据交互等功能的一体化软件，"
    "研制稀土萃取分离过程元素成分在线检测仪表，在稀土、钨钼等湿法冶金场景中完成仪表性能验证。\n"
    "主要技术指标：激发源电压≥65kV、功率≥65W；SDD 探测器分辨率＜136eV@5.9keV；"
    "稀土元素检出限≤0.01%，检测重复性 RSD≤1%，在线检测时间≤2min；整机技术就绪度（TRL）≥8 级。"
)

SUMMARY_TEXT = (
    "本项目聚焦稀土萃取分离过程元素成分在线检测仪表的国产化研制，围绕取样系统、高稳定性光源、"
    "高性能光学系统、加密数据互传系统等关键核心部件，统筹推进方案设计、部件研制、软件开发、整机集成与性能验证。"
    "在设计上已有详细的设计方案及实施方案，并形成了相关专利；试制上已形成初样机，满足设计要求且持续优化中；"
    "部分关键部件已通过第三方检测机构验证。"
)

# 各级“满足情况说明 / 评价支撑信息”样例文本（按 TRL 级别 + 类别 + 序号）
STATEMENTS = {
    (1, "hw", 1): ("明确了 X 射线荧光分析系统、数据处理系统和自动控制系统的完整技术链路，核心原理已通过发明专利公开，为硬件研发奠定理论基础。", "发明专利《一种稀土冶炼分离过程质量配分量在线监测仪》"),
    (1, "hw", 2): ("项目任务书系统界定了仪表的适用环境与检测范围，论文完成原理假设的边界论证，明确技术的适用场景与约束条件。", "《项目任务书》；论文《基于能量色散-X射线荧光光谱方法对轻稀土料液配分含量的在线测定》"),
    (1, "sw", 3): ("项目任务书已完成软件系统全链路技术瓶颈梳理，识别出多流路控制、机器人协同、仪器状态监控、谱数据处理与传输等关键问题。", "《项目任务书》"),
    (1, "sw", 4): ("基于能量色散 X 射线荧光光谱技术建立了在线测定稀土配分含量的方法，核心算法原理已通过论文公开。", "论文《X射线荧光光谱法在线测定稀土冶炼分离过程中钬铒铥镱》"),
    (1, "sw", 5): ("明确了核心算法的运行条件与适用范围，通过整体技术路线可行性论证确认软件系统研发的技术可行性。", "《软件可行性报告》《机器人辅助取样系统开发文档》"),
    (2, "hw", 1): ("任务书明确仪表核心性能指标，完成硬件系统初步实施方案编制，梳理核心硬件模块的技术要素与结构特性。", "《项目任务书》《项目实施工作方案》"),
    (2, "hw", 2): ("初步明确硬件系统可实现的核心功能（多点位取样、在线检测、数据传输），确定了定量指标。", "《项目任务书》"),
    (2, "hw", 3): ("明确仪表在高温、高湿、高酸、强有机腐蚀等萃取分离现场环境下工作的预期应用环境要求。", "《项目任务书》《应用环境分析文档》"),
    (2, "sw", 4): ("完成一体化软件系统需求分析，形成多流路状态监控、谱图解算、数据交互等功能需求清单。", "《软件需求分析文档》"),
    (2, "sw", 5): ("确定基于工业现场数据与机理知识融合的一体化智能分析技术路线。", "《软件设计文档》"),
    (2, "sw", 6): ("完成光谱预处理、重叠峰解析、元素检测等模块的技术准备与预研。", "《软件技术准备报告》"),
    (2, "sw", 7): ("形成一体化软件概要设计，划分自检、采样、检测、数据交互、可视化五大模块。", "《概要设计说明书》"),
    (3, "hw", 1): ("形成两级管道中继取样、机器人辅助取样、X 射线源、XRF 光学、无线加密互传五大子系统的完善实施方案。", "《项目实施工作方案》"),
    (3, "hw", 2): ("通过仿真与台架试验验证了多流路取样、高压稳定激发等关键功能的可行性。", "《关键功能仿真验证报告》"),
    (3, "hw", 3): ("理论分析了光管、探测器、无线系统与整机的集成方案可行性。", "《系统集成可行性分析文档》"),
    (3, "hw", 4): ("形成覆盖五个课题的项目开发计划，明确里程碑与交付物。", "《项目开发计划》"),
    (3, "hw", 5): ("评估了 X 射线管真空封接、SDD 芯片封装等制造条件与现有制造能力。", "《制造能力评估报告》"),
    (3, "sw", 6): ("确定软件需求边界：覆盖 17 种稀土元素与 6 种以上混合料液检测。", "《需求边界确认报告》"),
    (3, "sw", 7): ("完成解谱算法等关键技术验证，软件可对稀土元素谱峰进行识别。", "《软件关键技术验证报告》"),
    (3, "sw", 8): ("完成一体化软件详细设计。", "《软件详细设计文档》"),
    (4, "hw", 1): ("完成两级管道中继取样系统、X 射线源等关键功能试样开发，形成专利与研究报告并被采纳。", "《关键部件开发文档》；发明专利受理通知书"),
    (4, "hw", 2): ("在实验室环境下完成光管 50kV/50W 工况测试、探测器分辨率 176eV@5.9keV 测试。", "《大功率光管测试报告》《高性能XRF光学系统检测报告》"),
    (4, "hw", 3): ("试制了两级管道中继取样系统样件、机器人初样机、X 射线管样机、SDD 探头初样机。", "实物样机照片；《试制记录》"),
    (4, "hw", 4): ("集成光管、探测器、无线加密系统，形成 15 路测试通道的光谱仪初样机。", "《整机集成报告》"),
    (4, "hw", 5): ("评估了真空焊接、无尘装配、SDD 芯片筛选等关键制造工艺。", "《大功率X射线管制造工艺》《高性能XRF光学系统制备工艺方案》"),
    (4, "hw", 6): ("各关键功能试样设计过程文档清晰，可追溯。", "《设计过程文档清单》"),
    (4, "sw", 7): ("完成软件研发实施方案及进度计划。", "《软件实施方案及进度计划文档》"),
    (4, "sw", 8): ("完成软件主框架研发，原型系统在初样机上运行。", "《软件技术路线文档》"),
    (4, "sw", 9): ("基于原型系统开展全流程功能验证、性能测试与兼容性分析。", "《软件关键技术验证报告》"),
}

DEFAULT_STATEMENT = "经评价组核查相关佐证材料，该具体化等级条件已满足。"

# 专家评审样例中判“不通过”的条目及建议
FAIL_SUGGESTIONS = {
    "TRL5-HW-2": "请补充模拟使用环境下的关键性能测试报告，并说明测试条件与实际工况的对应关系。",
    "TRL5-HW-3": "请补充关键部件在模拟环境下的可靠性试验记录（含试验条件、样本数量和失效判据）。",
    "TRL6-HW-2": "请补充系统级原型在相关环境中的验证报告，现有材料只覆盖部件级验证。",
    "TRL7-HW-1": "请补充在用户现场连续运行的试用记录和用户使用报告。",
}


def _evidence_docx(title):
    document = Document()
    document.add_heading(title, level=1)
    document.add_paragraph("本文件为系统初始化时生成的样例佐证材料，正式评价时请上传真实文件。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class Command(BaseCommand):
    help = "初始化管理员账号与样例企业、样例评价项目（可重复执行，会重建样例企业的项目）"

    def add_arguments(self, parser):
        parser.add_argument("--admin-only", action="store_true", help="仅创建 / 重置管理员账号，不生成样例企业与项目")

    @transaction.atomic
    def handle(self, *args, **options):
        if not RuleItem.objects.filter(system=TRL).exists():
            call_command("import_rules", verbosity=0)

        admin, _ = User.objects.get_or_create(username=ADMIN_USERNAME)
        admin.set_password(INITIAL_PASSWORD)
        admin.first_name = "评价中心管理员"
        admin.is_staff = admin.is_superuser = admin.is_active = True
        admin.save()
        self.stdout.write(self.style.SUCCESS(f"管理员账号：{ADMIN_USERNAME}（初始密码 {INITIAL_PASSWORD}，请登录后修改）"))
        if options["admin_only"]:
            return

        users = {}
        for username, company, code17, contact, phone, status in ENTERPRISES:
            user, _ = User.objects.get_or_create(username=username)
            user.set_password(INITIAL_PASSWORD)
            user.first_name = contact
            user.is_active = status == EnterpriseProfile.STATUS_ACTIVE
            user.save()
            EnterpriseProfile.objects.update_or_create(
                user=user,
                defaults={
                    "company_name": company,
                    "credit_code": code17 + credit_code_check_char(code17),
                    "contact_name": contact,
                    "phone": phone,
                    "status": status,
                    "reviewed_by": admin if status == EnterpriseProfile.STATUS_ACTIVE else None,
                    "reviewed_at": timezone.now() if status == EnterpriseProfile.STATUS_ACTIVE else None,
                },
            )
            users[username] = user
        sample_projects = EvaluationProject.all_objects.filter(owner__in=users.values())
        # 重建样例项目前清掉与之相关的站内私信，避免收件箱残留旧消息
        Notification.objects.filter(project__in=sample_projects).delete()
        sample_projects.delete()
        self.stdout.write(self.style.SUCCESS(f"样例企业账号：{', '.join(users)}（初始密码 {INITIAL_PASSWORD}；jingce 为待审核注册）"))

        gangyan, hexin, zhongnan = users["gangyan"], users["hexin"], users["zhongnan"]

        created = seed_sample_experts(admin)
        self.stdout.write(self.style.SUCCESS(f"样例评审专家：expert01～expert20（新建 {created} 位，初始密码 {INITIAL_PASSWORD}）"))

        # ① 填写中：已通过 1~3 级，第 4 级部分填写
        p1 = self._create(
            gangyan,
            name="稀土萃取分离过程元素成分在线检测仪表",
            domain="稀土湿法冶金 · 在线检测仪表",
            tech_type=EvaluationProject.TECH_EQUIPMENT,
            target=8,
            specialty="instrument",
        )
        self._fill(p1, gangyan, levels=[1, 2, 3])
        self._submit_and_pass(p1, gangyan, admin, up_to=3)
        self._fill_partial(p1, gangyan, level=4, count=5)
        self._team(p1, EvaluationTeamMember.GROUP_SELF, SELF_TEAMS["gangyan"])

        # ② 待审核：第 1~4 级一次提交
        p2 = self._create(
            gangyan,
            name="稀土萃取过程多流路在线取样系统",
            domain="稀土湿法冶金 · 取样与预处理",
            tech_type=EvaluationProject.TECH_HARDWARE,
            target=4,
            specialty="instrument",
        )
        self._fill(p2, gangyan, levels=[1, 2, 3, 4])
        services.submit_levels(p2.get_assessment(TRL), gangyan, 4)
        # 评价机构审核时直接修改了一条的满足情况说明（留痕）
        item = p2.get_assessment(TRL).items.filter(level=1).order_by("track", "seq").first()
        services.admin_edit_item_text(
            item,
            admin,
            item.statement + "（评价组核对：专利已授权，授权公告号见附件。）",
            item.support_info,
        )

        # ③ 待出报告：目标 4 级全部通过，两组评价组已填写，企业已申请报告导出（待审核）
        p3 = self._create(
            hexin,
            name="高性能 XRF 光学系统",
            domain="X 射线探测器 · 核心器件",
            tech_type=EvaluationProject.TECH_HARDWARE,
            target=4,
            specialty="optics",
        )
        self._fill(p3, hexin, levels=[1, 2, 3, 4])
        self._submit_and_pass(p3, hexin, admin, up_to=4)
        self._team(p3, EvaluationTeamMember.GROUP_SELF, SELF_TEAMS["hexin"])
        self._team(p3, EvaluationTeamMember.GROUP_THIRD, THIRD_TEAM)
        request_export(p3, Report.TYPE_TRL, hexin)

        # ④ 已退回：第 1 级通过，第 2~3 级退回修改（含逐条审核意见）
        p4 = self._create(
            hexin,
            name="大功率 X 射线管",
            domain="X 射线源 · 核心部件",
            tech_type=EvaluationProject.TECH_HARDWARE,
            target=3,
            specialty="optics",
        )
        self._fill(p4, hexin, levels=[1, 2, 3])
        assessment = p4.get_assessment(TRL)
        services.submit_levels(assessment, hexin, 3)
        services.pass_levels(assessment, admin, 1)
        item = assessment.items.filter(level=2).order_by("track", "seq").first()
        services.set_review_comment(item, admin, "请补充方案评审会议纪要或专家评审意见，作为技术方案已确定的佐证。")
        services.return_levels(assessment, admin, [2, 3], "第 2 级技术方案的佐证不充分，请按条目意见补充材料后，与第 3 级一并重新提交。")

        # ⑤ 已出报告：软件 · 目标 3 级 · 导出申请已通过（V1），企业又申请将目标调高到 4 级（待审核）
        p5 = self._create(
            zhongnan,
            name="稀土萃取在线检测一体化软件",
            domain="工业软件 · 光谱数据解析",
            tech_type=EvaluationProject.TECH_SOFTWARE,
            target=3,
            specialty="software",
        )
        self._fill(p5, zhongnan, levels=[1, 2, 3])
        self._submit_and_pass(p5, zhongnan, admin, up_to=3)
        self._team(p5, EvaluationTeamMember.GROUP_SELF, SELF_TEAMS["zhongnan"])
        self._team(p5, EvaluationTeamMember.GROUP_THIRD, THIRD_TEAM)
        approve_request(request_export(p5, Report.TYPE_TRL, zhongnan), admin)
        services.request_target_change(
            p5.get_assessment(TRL),
            zhongnan,
            4,
            "软件已在钢研纳克在线检测仪表初样机上完成联调，关键功能在实验室环境下验证通过，申请将目标调整为 TRL 4。",
        )

        self.stdout.write(
            self.style.SUCCESS(
                "样例项目已生成：填写中 / 待审核 / 待出报告（导出申请待审核）/ 已退回 / 已出报告（目标调整待审核）各一个"
            )
        )

        self._expert_samples(admin, gangyan, hexin, zhongnan)
        self.stdout.write(
            self.style.SUCCESS("专家评审样例已生成：评审中（一位专家逾期）/ 整改中 / 评审结束并出具报告 各一个")
        )

    # ------------------------------------------------------------------
    def _create(self, owner, *, name, domain, tech_type, target, specialty=None):
        project = EvaluationProject(
            name=name,
            applicant=owner.enterprise.company_name,
            domain=domain,
            specialty=Specialty.objects.filter(code=specialty).first(),
            tech_type=tech_type,
            overview_text=OVERVIEW_TEXT,
        )
        services.create_project(project, owner, {TRL: target})
        assessment = project.get_assessment(TRL)
        assessment.summary_text = SUMMARY_TEXT
        assessment.save(update_fields=["summary_text"])
        return project

    def _team(self, project, group, members):
        start = project.team_members.count()
        for order, (role, name, title, specialty, org) in enumerate(members, start=start + 1):
            EvaluationTeamMember.objects.create(
                project=project, group=group, order=order, role=role, name=name, title=title, specialty=specialty, org=org
            )

    def _fill_item(self, item, owner):
        statement, support = STATEMENTS.get((item.level, item.track, item.seq), (DEFAULT_STATEMENT, ""))
        title = support.split("；")[0].strip("《》") if support else f"{item.code} 佐证材料"
        upload_evidence_file(
            item,
            owner,
            SimpleUploadedFile(
                f"{title[:40]}.docx",
                _evidence_docx(title),
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
        services.evaluate_check_item(
            item, owner, {"result": CheckItem.RESULT_SATISFIED, "statement": statement, "support_info": support}
        )

    def _fill(self, project, owner, levels):
        assessment = project.get_assessment(TRL)
        for item in assessment.items.filter(level__in=levels).order_by("level", "track", "seq"):
            self._fill_item(item, owner)

    def _fill_partial(self, project, owner, level, count):
        assessment = project.get_assessment(TRL)
        for item in assessment.items.filter(level=level).order_by("track", "seq")[:count]:
            self._fill_item(item, owner)

    def _submit_and_pass(self, project, owner, admin, up_to):
        assessment = project.get_assessment(TRL)
        services.submit_levels(assessment, owner, up_to)
        services.pass_levels(assessment, admin, up_to)

    # ------------------------------------------------------------------
    # 专家评审样例
    # ------------------------------------------------------------------
    def _expert_fill(self, project, owner):
        """专家评审方式：填写全部等级；每级第一条挂一份佐证，控制样例体积。"""
        assessment = project.get_assessment(TRL)
        review_services.set_review_mode(assessment, owner, Assessment.MODE_EXPERT)
        seen_levels = set()
        for item in assessment.items.order_by("level", "track", "seq"):
            statement, support = STATEMENTS.get((item.level, item.track, item.seq), (DEFAULT_STATEMENT, ""))
            if item.level not in seen_levels:
                seen_levels.add(item.level)
                title = support.split("；")[0].strip("《》") if support else f"第 {item.level} 级佐证材料"
                upload_evidence_file(
                    item,
                    owner,
                    SimpleUploadedFile(
                        f"{title[:40]}.docx",
                        _evidence_docx(title),
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                )
            services.evaluate_check_item(
                item, owner, {"result": CheckItem.RESULT_SATISFIED, "statement": statement, "support_info": support}
            )
        return assessment

    def _experts(self, *usernames):
        return [User.objects.get(username=name) for name in usernames]

    def _opinions(self, assignment, fails=(), draft_only=False, half=False):
        items = review_services.task_items(assignment)
        if half:
            items = items[: len(items) // 2]
        data = {}
        for round_item in items:
            code = round_item.check_item.code
            if code in fails:
                data[round_item.id] = {
                    "verdict": "fail",
                    "suggestion": FAIL_SUGGESTIONS.get(code, "请补充能直接证明该条件的第三方检测或试验报告。"),
                    "comment": "现有佐证只能说明方案设计，尚不能证明该条件已经达成。",
                }
            else:
                data[round_item.id] = {"verdict": "pass", "suggestion": "", "comment": "佐证材料与条件对应，满足要求。"}
        review_services.save_opinions(assignment, assignment.expert, data, submit=not draft_only)

    def _expert_samples(self, admin, gangyan, hexin, zhongnan):
        # ⑥ 评审中：专家 A 已提交，专家 B 填了一半，专家 C 尚未接受且已逾期
        p6 = self._create(
            hexin,
            name="SDD 硅漂移探测器",
            domain="X 射线探测器 · 核心器件",
            tech_type=EvaluationProject.TECH_HARDWARE,
            target=6,
            specialty="optics",
        )
        a6 = self._expert_fill(p6, hexin)
        self._team(p6, EvaluationTeamMember.GROUP_SELF, SELF_TEAMS["hexin"])
        r6 = review_services.apply_expert_review(a6, hexin)
        review_services.assign_experts(r6, admin, [u.pk for u in self._experts("expert11", "expert12", "expert13")])
        s1, s2, s3 = r6.assignments.order_by("slot")
        review_services.respond_assignment(s1, s1.expert, True, pledge=True)
        self._opinions(s1, fails={"TRL5-HW-2"})
        review_services.respond_assignment(s2, s2.expert, True, pledge=True)
        self._opinions(s2, draft_only=True, half=True)
        # 专家 C 9 天前被分配、期限 7 天，至今未接受
        backdated = timezone.now() - timezone.timedelta(days=9)
        ExpertAssignment.objects.filter(pk=s3.pk).update(assigned_at=backdated, due_at=backdated + timezone.timedelta(days=7))

        # ⑦ 整改中：第 1 轮终审 3 条不通过，整改期 10 天
        p7 = self._create(
            gangyan,
            name="多流路在线自动进样装置",
            domain="稀土湿法冶金 · 取样与预处理",
            tech_type=EvaluationProject.TECH_HARDWARE,
            target=6,
            specialty="instrument",
        )
        a7 = self._expert_fill(p7, gangyan)
        self._team(p7, EvaluationTeamMember.GROUP_SELF, SELF_TEAMS["gangyan"])
        r7 = review_services.apply_expert_review(a7, gangyan)
        review_services.assign_experts(r7, admin, [u.pk for u in self._experts("expert01", "expert02", "expert03")])
        fails7 = ["TRL5-HW-3", "TRL6-HW-2", "TRL7-HW-1"]
        for index, assignment in enumerate(r7.assignments.order_by("slot")):
            review_services.respond_assignment(assignment, assignment.expert, True, pledge=True)
            # 席位 3 认为 TRL6-HW-2 已满足：形成一条分歧
            self._opinions(assignment, fails=fails7 if index < 2 else [fails7[0], fails7[2]])
        r7.refresh_from_db()
        review_services.confirm_unanimous(r7, admin)
        for round_item in r7.items.select_related("check_item"):
            code = round_item.check_item.code
            if code in fails7:
                review_services.rule_item(
                    round_item,
                    admin,
                    "fail",
                    reason="两位专家判不通过，佐证确有缺口" if code == "TRL6-HW-2" else "",
                    feedback=FAIL_SUGGESTIONS[code],
                )
        review_services.finalize_round(r7, admin, "rectify", rectify_days=10, summary="整体研制工作扎实，3 条需补充验证证据后复评。")
        # 劳务记录：填入样例金额，其中 2 条已结算
        fee_records = list(RemunerationRecord.objects.filter(project=p7).order_by("id"))
        for record in fee_records:
            fees.update_record(record, admin, 800, "样例金额")
        fees.settle_records([record.id for record in fee_records[:2]], admin)

        # ⑧ 评审结束：软件类，第 1 轮全部通过，已出具报告
        p8 = self._create(
            zhongnan,
            name="光谱数据智能解析软件",
            domain="工业软件 · 光谱数据解析",
            tech_type=EvaluationProject.TECH_SOFTWARE,
            target=7,
            specialty="software",
        )
        a8 = self._expert_fill(p8, zhongnan)
        self._team(p8, EvaluationTeamMember.GROUP_SELF, SELF_TEAMS["zhongnan"])
        r8 = review_services.apply_expert_review(a8, zhongnan)
        review_services.assign_experts(r8, admin, [u.pk for u in self._experts("expert08", "expert09", "expert10")])
        for assignment in r8.assignments.order_by("slot"):
            review_services.respond_assignment(assignment, assignment.expert, True, pledge=True)
            self._opinions(assignment)
        r8.refresh_from_db()
        review_services.confirm_unanimous(r8, admin)
        review_services.finalize_round(r8, admin, "close", summary="软件各级条件均有充分佐证，同意通过。")
        self._front_matter(p8)
        approve_request(request_export(p8, Report.TYPE_TRL, zhongnan), admin)
        # 生成逾期提醒（专家 C 已逾期）
        check_deadlines()

    def _front_matter(self, project):
        """报告前置信息样例（光谱数据智能解析软件）。"""
        front = get_front_matter(project)
        front.compiled_by, front.checked_by, front.approved_by = "王工", "李工", "杜工"
        front.standard_version = "《技术就绪度评价标准及细则》（2026 年版）"
        front.s1_1_1 = (
            "本项目来源于稀土萃取分离过程在线检测仪表研制任务，由中南大学承担配套软件的研发，"
            "解决在线光谱数据量大、谱峰重叠严重、人工解析滞后等问题。"
        )
        front.s1_1_2 = "研制具备谱图自动解析、多流路数据管理和检测结果实时上传功能的光谱数据智能解析软件，支撑在线检测仪表连续运行。"
        front.s1_1_3 = "17 种稀土元素同时解析；单次解析时间≤5 s；解析结果与实验室离线分析偏差≤3%；软件连续运行无故障时间≥720 h。"
        front.s1_1_4 = "光谱数据智能解析软件 1 套，软件著作权 2 项，测试报告 1 份。"
        front.s1_1_5 = (
            "软件采用“采集—预处理—解析—发布”四层结构，提供谱图预处理、重叠峰解析、元素定量和结果发布功能，"
            "可与在线检测仪表的控制系统通过标准接口交换数据。"
        )
        front.s1_2_1 = "软件已完成全部功能开发，在实验室和稀土分离企业现场完成联调与试运行。"
        front.s1_2_2 = "主要技术指标均已达到任务书要求，其中单次解析时间实测 3.2 s。"
        front.s1_2_4 = "完成需求分析、概要设计、详细设计、编码与测试全过程，形成配套开发文档。"
        front.s3_1 = "光谱数据智能解析软件运行于在线检测仪表配套工控机，负责光谱数据的预处理、解析与结果发布。"
        front.s3_4_software = "软件经第三方软件测评机构测试，功能、性能和可靠性指标满足要求。"
        front.save()
        ReportModule.objects.filter(front_matter=front).delete()
        ReportModule.objects.create(
            front_matter=front,
            order=1,
            name="重叠峰解析算法",
            design="基于约束非负最小二乘的重叠峰解析方法，结合元素特征峰库自动识别谱峰。",
            trial="在 6 类稀土混合料液样本上完成算法试制与参数整定。",
            verification="与实验室离线分析结果比对，偏差均在 3% 以内。",
        )
        ReportModule.objects.create(
            front_matter=front,
            order=2,
            name="多流路数据管理模块",
            design="按流路组织检测数据，支持流路切换时的方法自动匹配。",
            trial="在 15 路在线取样系统上完成联调。",
            verification="现场连续运行 30 天，数据完整率 100%。",
        )
