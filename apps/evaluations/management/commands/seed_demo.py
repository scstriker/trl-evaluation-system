# -*- coding: utf-8 -*-
"""演示数据：4 个演示账号 + 4 个状态各异的评价项目（口径参照《4400 技术就绪度评价报告》）。"""

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.permissions import ROLE_AUDITOR, ROLE_EVALUATOR, ROLE_SYSTEM_ADMIN, ROLE_VIEWER
from apps.evaluations import services
from apps.evaluations.models import CheckItem, EvaluationProject
from apps.evidence.services import upload_evidence_file
from apps.reports.services import generate_report
from apps.rules.models import RuleItem

DEMO_PASSWORD = "Trl@2026"

DEMO_USERS = [
    ("admin", ROLE_SYSTEM_ADMIN, True),
    ("evaluator", ROLE_EVALUATOR, False),
    ("auditor", ROLE_AUDITOR, False),
    ("viewer", ROLE_VIEWER, False),
]

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

# 各级“满足情况说明 / 评价支撑信息”演示文本（按 TRL 级别 + 轨别 + 序号）
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


def _evidence_bytes(title):
    return f"演示佐证材料 — {title}\n（Demo 占位文件，正式评价请上传真实文档）".encode("utf-8")


class Command(BaseCommand):
    help = "生成演示账号与演示评价项目（可重复执行，会先清空已有演示项目）"

    def add_arguments(self, parser):
        parser.add_argument("--keep", action="store_true", help="保留已有演示项目，只补充账号")

    @transaction.atomic
    def handle(self, *args, **options):
        call_command("init_roles", verbosity=0)
        if not RuleItem.objects.exists():
            call_command("import_trl_rules", verbosity=0)

        users = {}
        for username, role, is_super in DEMO_USERS:
            user, _ = User.objects.get_or_create(username=username)
            user.set_password(DEMO_PASSWORD)
            user.is_staff = is_super
            user.is_superuser = is_super
            user.save()
            user.groups.set([Group.objects.get(name=role)])
            users[username] = user
        self.stdout.write(self.style.SUCCESS(f"演示账号就绪：{', '.join(users)}（统一密码：{DEMO_PASSWORD}）"))

        if options["keep"]:
            return

        EvaluationProject.objects.filter(name__startswith="演示-").delete()
        evaluator, auditor = users["evaluator"], users["auditor"]

        # ① 未开始：稀土检测仪表 · 设备轨道 · 目标 8 级
        p1 = self._create_project(
            evaluator,
            name="演示-稀土萃取分离过程元素成分在线检测仪表（未开始）",
            applicant="钢研纳克检测技术股份有限公司",
            domain="稀土湿法冶金 · 在线检测仪表",
            track=EvaluationProject.TRACK_EQUIPMENT,
            target_level=8,
        )

        # ② 进行中：已通过 TRL1~3，第 4 级部分判定，含佐证
        p2 = self._create_project(
            evaluator,
            name="演示-稀土元素成分在线检测仪表（评价进行中）",
            applicant="钢研纳克检测技术股份有限公司",
            domain="稀土湿法冶金 · 在线检测仪表",
            track=EvaluationProject.TRACK_EQUIPMENT,
            target_level=4,
        )
        self._evaluate_levels(p2, evaluator, levels=[1, 2, 3], pass_levels=True, with_evidence=True)
        self._evaluate_partial(p2, evaluator, level=4, count=5, with_evidence=True)

        # ③ 已完成待出报告：目标 4 级全部通过
        p3 = self._create_project(
            evaluator,
            name="演示-高性能 XRF 光学系统（待出报告）",
            applicant="核芯光电科技（山东）有限公司",
            domain="X 射线探测器 · 核心器件",
            track=EvaluationProject.TRACK_HARDWARE,
            target_level=4,
        )
        self._evaluate_levels(p3, evaluator, levels=[1, 2, 3, 4], pass_levels=True, with_evidence=True)

        # ④ 已出报告：软件轨道 · 目标 3 级 · 已生成 V1
        p4 = self._create_project(
            evaluator,
            name="演示-稀土萃取在线检测一体化软件（已出报告）",
            applicant="中南大学",
            domain="工业软件 · 光谱数据解析",
            track=EvaluationProject.TRACK_SOFTWARE,
            target_level=3,
        )
        self._evaluate_levels(p4, evaluator, levels=[1, 2, 3], pass_levels=True, with_evidence=True)
        generate_report(p4, auditor)

        self.stdout.write(self.style.SUCCESS("演示项目已生成：①未开始 ②进行中 ③待出报告 ④已出报告"))

    # ------------------------------------------------------------------
    def _create_project(self, creator, **fields):
        project = EvaluationProject.objects.create(
            created_by=creator,
            overview_text=OVERVIEW_TEXT,
            summary_text=SUMMARY_TEXT,
            **fields,
        )
        services.generate_check_items(project)
        return project

    def _evaluate_item(self, item, actor, with_evidence):
        statement, support = STATEMENTS.get((item.level, item.track, item.seq), (DEFAULT_STATEMENT, ""))
        if with_evidence:
            title = support.split("；")[0].strip("《》") if support else f"{item.code} 佐证材料"
            upload_evidence_file(
                item,
                actor,
                SimpleUploadedFile(f"{title[:40]}.pdf", _evidence_bytes(title), content_type="application/pdf"),
                description="演示佐证",
            )
        services.evaluate_check_item(
            item,
            actor,
            {"result": CheckItem.RESULT_SATISFIED, "statement": statement, "support_info": support},
        )

    def _evaluate_levels(self, project, actor, levels, pass_levels, with_evidence):
        for level in levels:
            for item in project.items.filter(level=level).order_by("track", "seq"):
                self._evaluate_item(item, actor, with_evidence)
            if pass_levels:
                services.pass_level(project, level, actor)

    def _evaluate_partial(self, project, actor, level, count, with_evidence):
        for item in project.items.filter(level=level).order_by("track", "seq")[:count]:
            self._evaluate_item(item, actor, with_evidence)
