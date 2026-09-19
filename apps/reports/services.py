import hashlib
from io import BytesIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from apps.accounts.permissions import can_generate_report
from apps.audit.models import AuditLog
from apps.evaluations.models import CheckItem, EvaluationProject
from apps.evaluations.services import get_achieved_level, get_project_stats
from apps.evidence.models import EvidenceFile
from apps.reports.models import Report
from apps.rules.models import MaturityLevel

RESULT_LABELS = dict(CheckItem.RESULT_CHOICES)

# 评价依据（与 4400 报告口径一致）
EVALUATION_BASIS = [
    ("GB/T 22900-2022", "《科学技术研究项目评价通则》"),
    ("GB/T 41621-2022", "《科学技术研究项目评价实施指南 开发研究项目》"),
    ("—", "《技术就绪度评价标准及细则》"),
]

# 演示用评价组成员（Demo 固定数据）
DEMO_EVALUATORS = [
    ("组长", "杜工", "正高级工程师", "测量控制设备及系统评价", "机械工业仪器仪表综合技术经济研究所"),
    ("成员", "李工", "高级工程师", "智能制造与装备技术", "机械工业仪器仪表综合技术经济研究所"),
    ("成员", "王工", "高级工程师", "工业软件测评", "机械工业仪器仪表综合技术经济研究所"),
]


def _cn_year_month(dt):
    digits = "〇一二三四五六七八九"
    year = "".join(digits[int(ch)] for ch in str(dt.year))
    months = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]
    return f"{year}年{months[dt.month - 1]}月"


def _set_base_style(document):
    style = document.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    for level, size in ((1, 16), (2, 13)):
        heading = document.styles[f"Heading {level}"]
        heading.font.name = "Times New Roman"
        heading.font.size = Pt(size)
        heading.font.bold = True
        heading.font.color.rgb = RGBColor(0, 0, 0)
        heading.element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")


def _para(document, text, *, align=None, bold=False, size=None):
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = bold
    if size:
        run.font.size = Pt(size)
    if align:
        paragraph.alignment = align
    return paragraph


def _add_table(document, headers, rows, *, header_bold=True, widths=None):
    """widths：各列宽度（cm），总宽约 16cm（A4 默认页边距下的正文宽度）。"""
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = str(header)
        if header_bold:
            for run in cell.paragraphs[0].runs:
                run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = "" if value is None else str(value)
    if widths:
        table.autofit = False
        for row in table.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(10)
    return table


def build_report_context(project):
    """报告预览与 docx 共用的数据装配。"""
    achieved = get_achieved_level(project)
    stats = get_project_stats(project)
    level_defs = {lv.level: lv.name for lv in MaturityLevel.objects.all()}
    gates = {gate.level: gate for gate in project.level_gates.select_related("passed_by")}

    level_sections = []
    for level in range(1, achieved + 1):
        items = list(
            project.items.filter(level=level)
            .prefetch_related("evidence_files")
            .select_related("evaluated_by")
            .order_by("track", "seq")
        )
        level_sections.append(
            {
                "level": level,
                "definition": level_defs.get(level, ""),
                "items": items,
                "gate": gates.get(level),
            }
        )

    evidence_files = (
        EvidenceFile.objects.filter(check_item__project=project, is_deleted=False)
        .select_related("check_item", "uploaded_by")
        .order_by("check_item__order_index", "uploaded_at")
    )

    return {
        "project": project,
        "achieved": achieved,
        "stats": stats,
        "level_defs": level_defs,
        "level_sections": level_sections,
        "evidence_files": list(evidence_files),
        "basis": EVALUATION_BASIS,
        "evaluators": DEMO_EVALUATORS,
        "track_label": project.get_track_display(),
    }


def validate_can_generate(project, actor):
    if not can_generate_report(actor):
        raise PermissionDenied("当前用户没有生成报告的权限。")
    if project.status == EvaluationProject.STATUS_ARCHIVED:
        raise ValidationError("当前项目已归档，不能生成报告。")
    achieved = get_achieved_level(project)
    if achieved < project.target_level:
        remaining = project.target_level - achieved
        raise ValidationError(
            f"目标等级 TRL {project.target_level} 尚未全部审核通过（当前达成 TRL {achieved}，还差 {remaining} 级），不能生成报告。"
        )


def _docx_bytes(project, context, report_no, version, generated_by):
    achieved = context["achieved"]
    now = timezone.localtime()
    document = Document()
    _set_base_style(document)

    # ---------- 封面 ----------
    _para(document, f"报告编号：{report_no}", align=WD_ALIGN_PARAGRAPH.RIGHT)
    for _ in range(4):
        document.add_paragraph()
    _para(document, "技术就绪度评价报告", align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, size=26)
    document.add_paragraph()
    _para(document, project.name, align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, size=16)
    for _ in range(6):
        document.add_paragraph()
    _para(document, project.issuer, align=WD_ALIGN_PARAGRAPH.CENTER, size=14)
    _para(document, _cn_year_month(now), align=WD_ALIGN_PARAGRAPH.CENTER, size=12)
    document.add_page_break()

    # ---------- 扉页：编制/审核/批准 ----------
    _para(document, "技术就绪度评价报告", align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, size=16)
    _para(document, project.name, align=WD_ALIGN_PARAGRAPH.CENTER, size=12)
    for _ in range(4):
        document.add_paragraph()
    for label in ("编 制：", "审 核：", "批 准："):
        _para(document, f"{label}                    ", size=12)
        document.add_paragraph()
    _para(document, project.issuer, align=WD_ALIGN_PARAGRAPH.CENTER, size=12)
    document.add_page_break()

    # ---------- 1 概况 ----------
    document.add_heading("1 概况", level=1)
    document.add_heading("1.1 项目基本信息", level=2)
    _add_table(
        document,
        ["项目", "内容"],
        [
            ["技术项目名称", project.name],
            ["委托（受评）单位", project.applicant],
            ["评价出具单位", project.issuer],
            ["应用领域", project.domain or "—"],
            ["评价轨道", context["track_label"]],
            ["目标就绪度等级", f"TRL {project.target_level}"],
            ["评价达成等级", f"TRL {achieved}"],
            ["报告版本", f"V{version}"],
            ["报告生成时间", f"{now:%Y-%m-%d %H:%M}"],
            ["报告生成人", generated_by.get_full_name() or generated_by.username],
        ],
        widths=[4.0, 12.0],
    )
    document.add_heading("1.2 项目概况", level=2)
    document.add_paragraph(project.overview_text or "（项目概况由委托单位提供，参见项目任务书及研制总结材料。）")

    # ---------- 2 评价实施情况 ----------
    document.add_heading("2 评价实施情况", level=1)
    document.add_heading("2.1 评价目的和范围", level=2)
    document.add_paragraph(
        f"对{project.applicant}研制的“{project.name}”开展技术就绪度评价，"
        f"验证其是否满足任务书中规定的技术就绪度 {project.target_level} 级（TRL {project.target_level}）的要求。"
        f"评价范围覆盖 TRL 1 级至 TRL {achieved} 级的全部核验细则条目。"
    )
    document.add_heading("2.2 评价依据", level=2)
    _add_table(document, ["标准编号", "标准名称"], [[code, name] for code, name in context["basis"]])
    document.add_heading("2.3 评价人员", level=2)
    _add_table(
        document,
        ["角色", "姓名", "职称", "专业及特长", "工作单位"],
        [list(row) for row in context["evaluators"]],
    )
    document.add_heading("2.4 评价过程", level=2)
    document.add_paragraph(
        "评价工作按照“受理委托—组建评价组—资料收集与佐证核验—逐级核验判定—级别审核确认—报告编制—审核批准”的流程实施。"
        "评价组依据《技术就绪度评价标准及细则》，对各级核验细则逐条比对佐证材料并作出判定，"
        "每一级全部细则判定完成并经审核确认后，方进入下一级核验。全过程判定痕迹与佐证材料均在系统中留痕可溯。"
    )

    # ---------- 3 评价准则 ----------
    document.add_heading("3 评价准则", level=1)
    document.add_paragraph("技术就绪度等级定义见表 3-1，各级具体化条件见表 3-2 至表 3-%d。" % (1 + achieved))
    document.add_paragraph("表 3-1 技术就绪度等级定义")
    _add_table(
        document,
        ["等级", "技术就绪度等级定义"],
        [[level, context["level_defs"].get(level, "")] for level in range(1, 10)],
        widths=[1.5, 14.5],
    )
    for section in context["level_sections"]:
        document.add_paragraph()
        document.add_paragraph(f"表 3-{1 + section['level']} 技术就绪度 {section['level']} 级条件")
        _add_table(
            document,
            ["序号", "适用", f"第{section['level']}级：{section['definition']}", "评价支撑信息"],
            [
                [item.seq, item.get_track_display(), item.name, item.evidence]
                for item in section["items"]
            ],
            widths=[1.2, 1.4, 9.4, 4.0],
        )

    # ---------- 4 技术就绪度评价 ----------
    document.add_heading("4 技术就绪度评价", level=1)
    document.add_heading("4.1 综述", level=2)
    document.add_paragraph(project.summary_text or "")
    document.add_paragraph(
        f"经评价组综合评估，“{project.name}”达到了《技术就绪度评价标准及细则》规定的技术就绪度 {achieved} 级（TRL {achieved}）的条件。"
    )
    document.add_heading("4.2 确定等级的分析说明", level=2)
    document.add_paragraph(f"技术就绪度等级分析及确定见表 4-1 至表 4-{achieved}。")
    for section in context["level_sections"]:
        document.add_paragraph()
        document.add_paragraph(f"表 4-{section['level']} 技术就绪度等级结论的分析说明（{section['level']} 级）")
        _add_table(
            document,
            ["序号", "适用", "是否满足", "具体化等级条件", "满足情况说明", "评价支撑信息"],
            [
                [
                    item.seq,
                    item.get_track_display(),
                    RESULT_LABELS.get(item.result, ""),
                    item.name,
                    item.statement,
                    item.support_info
                    or "、".join(f"《{e.original_filename}》" for e in item.evidence_files.all() if not e.is_deleted),
                ]
                for item in section["items"]
            ],
            widths=[1.0, 1.2, 1.5, 4.3, 5.0, 3.0],
        )

    # ---------- 5 总结 ----------
    document.add_heading("5 总结", level=1)
    stats = context["stats"]
    gate_lines = "；".join(
        f"TRL {section['level']} 级于 {timezone.localtime(section['gate'].passed_at):%Y-%m-%d} 审核通过"
        for section in context["level_sections"]
        if section["gate"]
    )
    document.add_paragraph(
        f"本次评价累计核验细则条目 {stats['completed']} 条，其中判定满足 {stats['satisfied']} 条、"
        f"不满足 {stats['not_satisfied']} 条、不适用 {stats['not_applicable']} 条。{gate_lines}。"
    )
    document.add_paragraph(
        f"综上所述，“{project.name}”的技术就绪度状态达到了 {achieved} 级（TRL {achieved}）水平。"
    )

    # ---------- 附录 A ----------
    document.add_heading("附录A 评价支撑信息清单", level=1)
    _add_table(
        document,
        ["编号", "评价支撑信息（佐证材料）", "关联细则", "SHA-256（前 16 位）"],
        [
            [index, evidence.original_filename, evidence.check_item.code, evidence.sha256[:16]]
            for index, evidence in enumerate(context["evidence_files"], start=1)
        ]
        or [["—", "（无佐证材料）", "—", "—"]],
        widths=[1.2, 8.3, 2.8, 3.7],
    )

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@transaction.atomic
def generate_report(project, actor):
    validate_can_generate(project, actor)
    context = build_report_context(project)
    achieved = context["achieved"]

    max_version = project.reports.aggregate(max_version=Max("version"))["max_version"] or 0
    version = max_version + 1
    now = timezone.localtime()
    report_no = project.report_no or f"ITEI-TRL-{now:%Y}-{str(project.id)[:6].upper()}"

    payload = _docx_bytes(project, context, report_no, version, actor)
    sha256 = hashlib.sha256(payload).hexdigest()
    filename = f"技术就绪度评价报告_{project.name}_V{version}.docx"

    report = Report(
        project=project,
        version=version,
        filename=filename,
        sha256=sha256,
        achieved_level=achieved,
        generated_by=actor,
    )
    report.file.save(filename, ContentFile(payload), save=True)

    before = {"status": project.status}
    project.status = EvaluationProject.STATUS_REPORTED
    if not project.report_no:
        project.report_no = report_no
    project.save(update_fields=["status", "report_no", "updated_at"])

    AuditLog.objects.create(
        actor=actor,
        action="report.generate",
        model_name="Report",
        object_id=str(report.id),
        object_repr=filename,
        project=project,
        before=before,
        after={"status": project.status, "version": version, "sha256": sha256},
    )
    return report
