"""评价报告：技术就绪度 / 制造成熟度 / 两者综合三种类型。

报告分两部分：
- 前置信息（1 概况、2 评价实施情况、3 评价对象研发信息）按评价机构提供的固定模板填写，
  由 docx_render 填入原版式 Word 模板；
- 第 4 章起的评价准则、等级评价、总结和附录由系统按审核 / 终审结果自动拼接。
两部分都装配为统一的“块”结构，在线预览与 Word 导出共用同一份数据，保证内容一致。
"""

import hashlib
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import can_generate_report, is_project_owner
from apps.audit.models import AuditLog
from apps.evaluations.models import Assessment, CheckItem, EvaluationProject, EvaluationTeamMember, LevelReview
from apps.evaluations.services import get_achieved_level, get_reviews, mrl_enabled
from apps.evidence.models import EvidenceFile
from apps.notifications.models import CATEGORY_TODO
from apps.notifications.services import notify, notify_admins
from apps.reports import docx_preview
from apps.reports import docx_render
from apps.reports import pdf as pdf_service
from apps.reports.models import Report, ReportFrontMatter, ReportRequest
from apps.rules.models import LevelDefinition
from apps.rules.systems import MRL, TRL, get_system

logger = logging.getLogger(__name__)

RESULT_LABELS = {
    CheckItem.RESULT_SATISFIED: "是",
    CheckItem.RESULT_NOT_SATISFIED: "否",
    CheckItem.RESULT_NOT_APPLICABLE: "不适用",
    CheckItem.RESULT_UNEVALUATED: "—",
}

TYPE_SYSTEMS = {
    Report.TYPE_TRL: [TRL],
    Report.TYPE_MRL: [MRL],
    Report.TYPE_COMBINED: [TRL, MRL],
}

TYPE_SUFFIX = {Report.TYPE_TRL: "TRL", Report.TYPE_MRL: "MRL", Report.TYPE_COMBINED: "ZH"}

TYPE_TITLES = dict(Report.TYPE_CHOICES)

HEADER_SUFFIX = {
    Report.TYPE_TRL: "技术就绪度评价",
    Report.TYPE_MRL: "制造成熟度评价",
    Report.TYPE_COMBINED: "技术就绪度与制造成熟度评价",
}

# 第 1 章“概况”的小节标题与对应字段（与报告模板一致）
CHAPTER_ONE = [
    ("1.1 装备研制目标", None),
    ("1.1.1 任务来源", "s1_1_1"),
    ("1.1.2 研究目标", "s1_1_2"),
    ("1.1.3 主要技术指标", "s1_1_3"),
    ("1.1.4 成果形式", "s1_1_4"),
    ("1.1.5 产品介绍及主要功能", "s1_1_5"),
    ("1.2 总体进展情况", None),
    ("1.2.1 项目总体进展概述", "s1_2_1"),
    ("1.2.2 技术指标完成情况", "s1_2_2"),
    ("1.2.3 关键部件研制", "s1_2_3"),
    ("1.2.4 软件开发", "s1_2_4"),
]

TEAM_HEADERS = ["角色", "姓名", "职务 / 职称", "专业及特长", "工作单位"]
TEAM_WIDTHS = [1.6, 2.0, 2.6, 4.4, 5.4]


def enabled_report_types():
    """制造成熟度未开放时，只提供技术就绪度评价报告。"""
    if mrl_enabled():
        return [key for key, _ in Report.TYPE_CHOICES]
    return [Report.TYPE_TRL]


def team_groups(project):
    members = list(project.team_members.all())
    return [
        (label, [m for m in members if m.group == group])
        for group, label in EvaluationTeamMember.GROUP_CHOICES
    ]


# ---------------------------------------------------------------------------
# 数据装配
# ---------------------------------------------------------------------------


def _assessment_part(assessment):
    sys = assessment.sys
    reviews = get_reviews(assessment)
    achieved = get_achieved_level(assessment, reviews)
    level_defs = {d.level: d.name for d in LevelDefinition.objects.filter(system=assessment.system)}
    items = list(
        assessment.items.filter(level__lte=achieved)
        .prefetch_related("evidence_files")
        .order_by("level", "track", "seq")
    )
    sections = []
    for level in range(1, achieved + 1):
        sections.append(
            {
                "level": level,
                "definition": level_defs.get(level, ""),
                "items": [item for item in items if item.level == level],
                "review": reviews.get(level),
            }
        )
    counts = {"completed": len(items), "satisfied": 0, "not_satisfied": 0, "not_applicable": 0}
    for item in items:
        if item.result in counts:
            counts[item.result] += 1
    return {
        "assessment": assessment,
        "sys": sys,
        "achieved": achieved,
        "target": assessment.target_level,
        "level_defs": level_defs,
        "sections": sections,
        "counts": counts,
        "has_general": any(item.track == CheckItem.TRACK_GENERAL for item in items),
    }


def report_availability(project):
    """每种报告能否生成及原因（报告页页签使用）。"""
    assessments = {a.system: a for a in project.assessments.all()}
    result = {}
    for report_type, systems in TYPE_SYSTEMS.items():
        reasons, missing = [], False
        for system in systems:
            assessment = assessments.get(system)
            if assessment is None:
                missing = True
                reasons.append(f"该项目未开展{get_system(system).name}评价")
                continue
            achieved = get_achieved_level(assessment)
            if achieved < assessment.target_level:
                sys = assessment.sys
                reasons.append(
                    f"{sys.name}目标等级 {sys.abbr} {assessment.target_level} 尚未全部审核通过（当前达成 {sys.abbr} {achieved}）"
                )
        result[report_type] = {"available": not missing, "reasons": reasons}
    return result


def _read_enterprise_file_bytes(obj):
    if not obj or not obj.file:
        return None
    obj.file.open("rb")
    try:
        return obj.file.read()
    finally:
        obj.file.close()


def _enterprise_files(project, package=None):
    from apps.reports.enterprise_docs import current_enterprise_file
    from apps.reports.models import EnterpriseProjectFile

    if package is None:
        package = current_enterprise_file(project, EnterpriseProjectFile.KIND_PACKAGE)
    overview = support = wbst = None
    if package is None:
        overview = current_enterprise_file(project, EnterpriseProjectFile.KIND_OVERVIEW)
        support = current_enterprise_file(project, EnterpriseProjectFile.KIND_SUPPORT)
        wbst = current_enterprise_file(project, EnterpriseProjectFile.KIND_WBST)
    return {
        "package": package,
        "overview": overview,
        "support": support,
        "wbst": wbst,
    }


def _placeholder_front_blocks():
    return [
        {"kind": "h1", "text": "1 概况"},
        {
            "kind": "p",
            "text": "第 1～3 章与附录待企业下载「企业报告材料」模板填写并上传；上传后本节将显示企业正文摘要。第 4 章起由系统按审核结果生成。",
            "muted": True,
        },
    ]


def _evidence_table_block(evidence_files):
    return {
        "kind": "table",
        "headers": ["编号", "相关文件（佐证材料）", "关联细则", "SHA-256（前 16 位）"],
        "rows": [
            [index, e.original_filename, e.check_item.code, e.sha256[:16]]
            for index, e in enumerate(evidence_files, start=1)
        ]
        or [["—", "（无佐证材料）", "—", "—"]],
        "widths": [1.2, 8.3, 2.8, 3.7],
    }


def appendix_chapter_number(num_parts: int) -> int:
    """正文末章为「总结」；附录章号 = 4（评价准则）+ len(parts) + 1（总结） + 1。"""
    return 5 + num_parts + 1


def _appendix_trailer_blocks(evidence_files, *, merge_mode):
    if not merge_mode:
        return []
    return [
        {"kind": "h2", "text": "佐证材料 SHA 归档清单"},
        _evidence_table_block(evidence_files),
    ]


def _preview_appendix_blocks(wbst, support, evidence_files, appendix_chapter):
    blocks = [{"kind": "h1", "text": f"{appendix_chapter} 附录"}]
    if wbst:
        blocks += docx_preview.appendix_status_block("附录A WBST 表", wbst.original_filename)
        preview_rows = docx_preview.extract_blocks(wbst.file, limit_paras=40)
        blocks += preview_rows[:12] if preview_rows else []
    if support:
        blocks += docx_preview.appendix_status_block("附录B 评价支撑信息", support.original_filename)
        preview_rows = docx_preview.extract_blocks(support.file, limit_paras=40)
        blocks += preview_rows[:12] if preview_rows else []
    blocks += [
        {"kind": "h2", "text": "佐证材料 SHA 归档清单"},
        _evidence_table_block(evidence_files),
    ]
    return blocks


def _preview_appendix_blocks_from_package(package, evidence_files, appendix_chapter, *, apps_bytes=None):
    blocks = [{"kind": "h1", "text": f"{appendix_chapter} 附录"}]
    if apps_bytes:
        preview_rows = docx_preview.extract_blocks(apps_bytes, limit_paras=40)
    elif package:
        preview_rows = docx_preview.extract_blocks_from_appendix(package.file)
    else:
        preview_rows = []
    if preview_rows:
        blocks += preview_rows[:20]
    elif package:
        blocks.append(
            {
                "kind": "p",
                "text": f"附录内容见上传的「{package.original_filename}」；生成报告时将自该 Word 合并。",
                "muted": True,
            }
        )
    blocks += [
        {"kind": "h2", "text": "佐证材料 SHA 归档清单"},
        _evidence_table_block(evidence_files),
    ]
    return blocks


def build_report(project, report_type, package=None, wbst=None, overview=None, support=None):
    systems = TYPE_SYSTEMS[report_type]
    assessments = {a.system: a for a in project.assessments.all()}
    parts = [_assessment_part(assessments[system]) for system in systems if system in assessments]
    if not parts:
        raise ValidationError("该项目没有可用于此类报告的评价。")
    team = team_groups(project)
    evidence_files = list(
        EvidenceFile.objects.filter(check_item__assessment__in=[part["assessment"] for part in parts], is_deleted=False)
        .select_related("check_item__assessment")
        .order_by("check_item__assessment__system", "check_item__order_index", "uploaded_at")
    )
    title = TYPE_TITLES[report_type]
    front = front_context(project, report_type, parts, team)
    files = _enterprise_files(project, package=package)
    package = files["package"]
    overview = files["overview"]
    wbst = files["wbst"] if wbst is None else wbst
    support = files["support"] if support is None else support
    merge_mode = package is not None or overview is not None

    apps_bytes = None
    if merge_mode:
        if package:
            from apps.reports import docx_merge

            payload = _read_enterprise_file_bytes(package)
            chapters_bytes, apps_bytes = docx_merge.split_package_docx(payload) if payload else (None, None)
            head = docx_preview.extract_blocks(chapters_bytes or payload) if (chapters_bytes or payload) else []
        else:
            head = docx_preview.extract_blocks(overview.file)
        if not head:
            head = [{"kind": "p", "text": "（企业材料无法预览，生成报告时将按上传 Word 合并。）", "muted": True}]
    else:
        head = _placeholder_front_blocks() + front_blocks(front)

    tail = _tail_blocks(
        project, report_type, parts, evidence_files, start=4, wbst=wbst, merge_appendices=merge_mode
    )
    appendix_chapter = appendix_chapter_number(len(parts))
    appendix_trailer = _appendix_trailer_blocks(evidence_files, merge_mode=merge_mode)
    if merge_mode and package:
        appendix_preview = _preview_appendix_blocks_from_package(
            package, evidence_files, appendix_chapter, apps_bytes=apps_bytes
        )
    elif merge_mode:
        appendix_preview = _preview_appendix_blocks(wbst, support, evidence_files, appendix_chapter)
    else:
        appendix_preview = []
    blocks = head + tail + appendix_preview

    return {
        "project": project,
        "report_type": report_type,
        "title": title,
        "parts": parts,
        "team": team,
        "evidence_files": evidence_files,
        "front": front,
        "front_blocks": head,
        "tail_blocks": tail,
        "appendix_trailer_blocks": appendix_trailer,
        "appendix_preview_blocks": appendix_preview,
        "appendix_chapter": appendix_chapter,
        "merge_mode": merge_mode,
        "enterprise_package": package or overview,
        "enterprise_overview": package or overview,
        "enterprise_support": support,
        "enterprise_wbst": wbst,
        "blocks": blocks,
    }


def _basis(parts):
    seen, rows = set(), []
    for part in parts:
        for code, name in part["sys"].basis:
            if name not in seen:
                seen.add(name)
                rows.append([code, name])
    return rows


def _levels_text(parts):
    return "、".join(f"{p['sys'].name} {p['achieved']} 级（{p['sys'].abbr} {p['achieved']}）" for p in parts)


def _criteria_blocks(part, chapter, table_no):
    """评价准则：等级定义表 + 已达成各级的条件表。返回 (blocks, 下一个表号)。"""
    sys = part["sys"]
    blocks = [
        {"kind": "caption", "text": f"表 {chapter}-{table_no} {sys.name}等级定义"},
        {
            "kind": "table",
            "headers": ["等级", f"{sys.name}等级定义"],
            "rows": [[level, part["level_defs"].get(level, "")] for level in sys.levels],
            "widths": [1.5, 14.5],
        },
    ]
    table_no += 1
    for section in part["sections"]:
        blocks.append({"kind": "caption", "text": f"表 {chapter}-{table_no} {sys.name} {section['level']} 级条件"})
        if part["has_general"]:
            headers = ["序号", f"第{section['level']}级：{section['definition']}", "评价支撑信息"]
            rows = [[item.seq, item.name, item.evidence] for item in section["items"]]
            widths = [1.2, 10.8, 4.0]
        else:
            headers = ["序号", "适用", f"第{section['level']}级：{section['definition']}", "评价支撑信息"]
            rows = [[item.seq, item.get_track_display(), item.name, item.evidence] for item in section["items"]]
            widths = [1.2, 1.4, 9.4, 4.0]
        blocks.append({"kind": "table", "headers": headers, "rows": rows, "widths": widths})
        table_no += 1
    return blocks, table_no


def _support_text(item):
    if item.support_info:
        return item.support_info
    return "、".join(f"《{e.original_filename}》" for e in item.evidence_files.all() if not e.is_deleted)


def _analysis_blocks(project, part, chapter):
    sys = part["sys"]
    assessment = part["assessment"]
    achieved = part["achieved"]
    blocks = [
        {"kind": "h2", "text": f"{chapter}.1 综述"},
    ]
    if assessment.summary_text:
        blocks.append({"kind": "p", "text": assessment.summary_text})
    blocks.append(
        {
            "kind": "p",
            "text": f"经评价组综合评估，“{project.name}”达到了{sys.standard}规定的{sys.name} {achieved} 级（{sys.abbr} {achieved}）的条件。",
        }
    )
    blocks.append({"kind": "h2", "text": f"{chapter}.2 确定等级的分析说明"})
    if not part["sections"]:
        blocks.append({"kind": "p", "text": "尚无审核通过的级别，逐级分析说明将在各级审核通过后生成。"})
        return blocks
    blocks.append({"kind": "p", "text": f"{sys.name}等级分析及确定见表 {chapter}-1 至表 {chapter}-{achieved}。"})
    for section in part["sections"]:
        review = section["review"]
        passed = f" · {timezone.localtime(review.reviewed_at):%Y-%m-%d} {_pass_word(review)}" if review and review.reviewed_at else ""
        blocks.append(
            {"kind": "caption", "text": f"表 {chapter}-{section['level']} {sys.name}等级结论的分析说明（{section['level']} 级）{passed}"}
        )
        if part["has_general"]:
            headers = ["序号", "是否满足", "审核依据", "满足情况说明", "评价支撑信息"]
            rows = [
                [item.seq, RESULT_LABELS[item.result], item.name, item.statement, _support_text(item)]
                for item in section["items"]
            ]
            widths = [1.0, 1.5, 5.0, 5.5, 3.0]
        else:
            headers = ["序号", "适用", "是否满足", "审核依据", "满足情况说明", "评价支撑信息"]
            rows = [
                [item.seq, item.get_track_display(), RESULT_LABELS[item.result], item.name, item.statement, _support_text(item)]
                for item in section["items"]
            ]
            widths = [1.0, 1.2, 1.5, 4.3, 5.0, 3.0]
        blocks.append({"kind": "table", "headers": headers, "rows": rows, "widths": widths})
    return blocks


def _paras(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def get_front_matter(project):
    """前置信息记录；首次使用时以项目概况预填 1.2.1。"""
    front_matter, created = ReportFrontMatter.objects.get_or_create(project=project)
    if created and project.overview_text:
        front_matter.s1_2_1 = project.overview_text
        front_matter.save(update_fields=["s1_2_1"])
    return front_matter


def _default_sections(project, report_type, parts):
    names = "和".join(part["sys"].name for part in parts)
    targets = "；".join(f"{p['sys'].name} {p['target']} 级（{p['sys'].abbr} {p['target']}）" for p in parts)
    standards = "、".join(p["sys"].standard for p in parts)
    scope = "；".join(f"{p['sys'].abbr} 1 级至 {p['sys'].abbr} {p['achieved']} 级" for p in parts)
    rounds = sum(p["assessment"].review_rounds.exclude(outcome="cancelled").count() for p in parts)
    if rounds:
        process = (
            "评价工作按照“受理申请—企业自评与佐证提交—评价机构审核或指派专家组逐条评审—终审—整改复评—报告编制”的流程实施。"
            f"委托单位依据{standards}对各级评价细则逐条自评并提交佐证材料；评价机构对提交的级别按技术领域从专家库选取 3 位专家组成第三方评价组，"
            f"专家独立逐条评审，评价机构对意见不一致的条目作出终审，共开展 {rounds} 轮专家评审。"
            "全过程填写、提交、评审、终审记录与佐证材料均在系统中留痕可溯。"
        )
    else:
        process = (
            "评价工作按照“受理申请—企业自评与佐证提交—逐级审核—报告编制—审核批准”的流程实施。"
            f"委托单位依据{standards}对各级评价细则逐条自评并提交佐证材料，评价组逐级核查佐证材料并作出审核结论，"
            "每一级经审核通过后方纳入达成等级。全过程填写、提交、审核记录与佐证材料均在系统中留痕可溯。"
        )
    return {
        "s1_2_1": project.overview_text,
        "s2_1": f"对{project.applicant}研制的“{project.name}”开展{names}评价，验证其是否满足{targets}的要求。"
        f"评价范围覆盖{scope}的全部评价细则条目。",
        "s2_3": f"{project.applicant}委托{project.issuer}开展{names}评价，成立了自评估工作组和第三方评价组，各组成员信息见表 2-1。",
        "s2_4": process,
    }


def front_context(project, report_type, parts, team):
    """前置信息：封面、扉页与第 1～3 章的内容。未填写的 2.1 / 2.3 / 2.4 / 1.2.1 用系统生成的默认文字。"""
    front_matter = get_front_matter(project)
    defaults = _default_sections(project, report_type, parts)
    sections = {}
    for field in ReportFrontMatter.SECTION_FIELDS:
        sections[field] = _paras(getattr(front_matter, field) or defaults.get(field, ""))
    basis = [{"code": code, "name": name} for code, name in _basis(parts)]
    if front_matter.standard_version and len(parts) == 1:
        for row in basis:
            if row["code"] == "—":
                row["name"] = front_matter.standard_version
    issue_date = front_matter.issue_date or timezone.localdate()
    members = {label: rows for label, rows in team}
    labels = dict(EvaluationTeamMember.GROUP_CHOICES)

    def rows(group):
        return [
            {"role": m.role, "name": m.name, "title": m.title, "specialty": m.specialty, "org": m.org}
            for m in members.get(labels[group], [])
        ]

    return {
        "report_title": TYPE_TITLES[report_type],
        "header_suffix": HEADER_SUFFIX[report_type],
        "project_name": project.name,
        "applicant": project.applicant,
        "issuer": project.issuer,
        "specialty": project.specialty.name if project.specialty else "",
        "application_date": timezone.localtime(project.created_at).date(),
        "issue_date": issue_date,
        "issue_date_cn": _cn_year_month(issue_date),
        "compiled_by": front_matter.compiled_by,
        "checked_by": front_matter.checked_by,
        "approved_by": front_matter.approved_by,
        "sections": sections,
        "basis": basis,
        "team_caption": "表 2-1 评价组成员组成",
        "self_rows": rows(EvaluationTeamMember.GROUP_SELF),
        "third_rows": rows(EvaluationTeamMember.GROUP_THIRD),
        "modules": [
            {"name": m.name, "design": _paras(m.design), "trial": _paras(m.trial), "verification": _paras(m.verification)}
            for m in front_matter.modules.all()
        ],
    }


def front_blocks(front):
    """前置信息的预览块，章节结构与 Word 模板一致。"""
    sections = front["sections"]
    blocks = [{"kind": "h1", "text": "1 概况"}]
    for title, field in CHAPTER_ONE:
        blocks.append({"kind": "h2" if title.count(".") == 1 else "h3", "text": title})
        if field:
            blocks += [{"kind": "p", "text": text} for text in sections[field]]
    blocks.append({"kind": "h1", "text": "2 评价实施情况"})
    blocks.append({"kind": "h2", "text": "2.1 评价目的和范围"})
    blocks += [{"kind": "p", "text": text} for text in sections["s2_1"]]
    blocks.append({"kind": "h2", "text": "2.2 评价依据"})
    blocks.append({"kind": "table", "headers": ["标准编号", "标准名称"], "rows": [[b["code"], b["name"]] for b in front["basis"]]})
    blocks.append({"kind": "h2", "text": "2.3 评价人员"})
    blocks += [{"kind": "p", "text": text} for text in sections["s2_3"]]
    blocks.append({"kind": "caption", "text": front["team_caption"]})
    blocks.append(
        {
            "kind": "team",
            "headers": ["角色", "姓名", "职称", "专业及特长", "工作单位"],
            "groups": [
                (label, [[r["role"], r["name"], r["title"], r["specialty"], r["org"]] for r in rows] or [["—", "（待填写）", "", "", ""]])
                for label, rows in (("自评估工作组", front["self_rows"]), ("第三方评价组", front["third_rows"]))
            ],
        }
    )
    blocks.append({"kind": "h2", "text": "2.4 评价过程"})
    blocks += [{"kind": "p", "text": text} for text in sections["s2_4"]]
    blocks.append({"kind": "h1", "text": "3 评价对象研发信息"})
    blocks.append({"kind": "h2", "text": "3.1 产品描述"})
    blocks += [{"kind": "p", "text": text} for text in sections["s3_1"]]
    for number, title, attr in (("3.2", "设计情况", "design"), ("3.3", "试制情况", "trial"), ("3.4", "验证情况", "verification")):
        blocks.append({"kind": "h2", "text": f"{number} {title}"})
        for index, module in enumerate(front["modules"], start=1):
            blocks.append({"kind": "h3", "text": f"{number}.{index} {module['name']}{title}"})
            blocks += [{"kind": "p", "text": text} for text in module[attr]]
    if sections["s3_4_software"]:
        blocks.append({"kind": "h3", "text": f"3.4.{len(front['modules']) + 1} 软件验证情况"})
        blocks += [{"kind": "p", "text": text} for text in sections["s3_4_software"]]
    return blocks


def _tail_blocks(project, report_type, parts, evidence_files, start=4, wbst=None, merge_appendices=False):
    """系统生成的章节：评价准则、各类评价的等级分析、总结；附录在企业合并模式下单独处理。"""
    combined = report_type == Report.TYPE_COMBINED
    blocks = []

    # 评价准则
    chapter = start
    blocks.append({"kind": "h1", "text": f"{chapter} 评价准则"})
    table_no = 1
    for index, part in enumerate(parts, start=1):
        if combined:
            blocks.append({"kind": "h2", "text": f"{chapter}.{index} {part['sys'].name}评价准则"})
        part_blocks, table_no = _criteria_blocks(part, chapter, table_no)
        blocks += part_blocks

    # 等级评价
    chapter += 1
    for part in parts:
        blocks.append({"kind": "h1", "text": f"{chapter} {part['sys'].name}评价"})
        blocks += _analysis_blocks(project, part, chapter)
        chapter += 1

    # 总结
    blocks.append({"kind": "h1", "text": f"{chapter} 总结"})
    for part in parts:
        sys, counts = part["sys"], part["counts"]
        passed_dates = "；".join(
            f"{sys.abbr} {s['level']} 级于 {timezone.localtime(s['review'].reviewed_at):%Y-%m-%d} {_pass_word(s['review'])}"
            for s in part["sections"]
            if s["review"] and s["review"].reviewed_at
        )
        text = (
            f"{sys.name}评价累计核验细则条目 {counts['completed']} 条，其中自评满足 {counts['satisfied']} 条、"
            f"不满足 {counts['not_satisfied']} 条、不适用 {counts['not_applicable']} 条。"
        )
        if passed_dates:
            text += f"{passed_dates}。"
        blocks.append({"kind": "p", "text": text})
    blocks.append({"kind": "p", "text": f"综上所述，“{project.name}”的{_levels_text(parts)}。", "strong": True})

    if merge_appendices:
        return blocks

    appendix_title = "附录A WBST 评价支撑信息表" if wbst else "附录A 评价支撑信息清单"
    blocks.append({"kind": "h1", "text": appendix_title})
    if wbst:
        blocks.append(
            {
                "kind": "p",
                "text": f"企业已上传 WBST 表文件「{wbst.original_filename}」（与报告一并归档，见系统下载）。",
            }
        )
    blocks.append(_evidence_table_block(evidence_files))
    return blocks


def _pass_word(review):
    return "终审通过" if review.source == LevelReview.SOURCE_EXPERT else "审核通过"


# ---------------------------------------------------------------------------
# 生成条件
# ---------------------------------------------------------------------------


def _has_team(project, group):
    return project.team_members.filter(group=group).exists()


SELF_TEAM_MISSING = "自评估工作组成员尚未填写（由企业在“编辑报告信息”中填写）"
THIRD_TEAM_MISSING = "第三方评价组成员尚未填写（由评价机构在“编辑报告信息”中填写）"
PACKAGE_MISSING = "请下载并上传企业报告材料（含第1～3章与附录）"


def request_blockers(project, report_type):
    """企业申请导出前需满足：评价达到目标等级、自评估工作组与企业材料已上传。"""
    from apps.reports.enterprise_docs import current_enterprise_file
    from apps.reports.models import EnterpriseProjectFile

    blockers = list(report_availability(project)[report_type]["reasons"])
    if not _has_team(project, EvaluationTeamMember.GROUP_SELF):
        blockers.append(SELF_TEAM_MISSING)
    if not current_enterprise_file(project, EnterpriseProjectFile.KIND_PACKAGE):
        blockers.append(PACKAGE_MISSING)
    return blockers


def generate_blockers(project, report_type, actor=None):
    blockers = list(request_blockers(project, report_type))
    if not _has_team(project, EvaluationTeamMember.GROUP_THIRD):
        blockers.append(THIRD_TEAM_MISSING)
    if actor is not None and not can_generate_report(actor):
        blockers.append("报告由评价机构管理员生成")
    return blockers


def validate_can_generate(project, report_type, actor):
    if not can_generate_report(actor):
        raise PermissionDenied("报告由评价机构管理员生成。")
    if report_type not in TYPE_SYSTEMS or report_type not in enabled_report_types():
        raise ValidationError("报告类型无效。")
    blockers = generate_blockers(project, report_type)
    if blockers:
        raise ValidationError(f"暂不能生成{TYPE_TITLES[report_type]}：{'；'.join(blockers)}。")


# ---------------------------------------------------------------------------
# 日期
# ---------------------------------------------------------------------------


def _cn_year_month(dt):
    digits = "〇一二三四五六七八九"
    year = "".join(digits[int(ch)] for ch in str(dt.year))
    months = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]
    return f"{year}年{months[dt.month - 1]}月"


# ---------------------------------------------------------------------------
# 生成与存档
# ---------------------------------------------------------------------------


def base_report_no(project, now=None):
    now = now or timezone.localtime()
    return project.report_no or f"ITEI-{now:%Y}-{str(project.id)[:6].upper()}"


def _make_pdf(docx_bytes):
    """有转换工具时生成 PDF：返回 (Word 字节, PDF 字节或 None, 转换方式)。Word 转换会刷新目录页码。"""
    try:
        updated, pdf_bytes, converter = pdf_service.convert(docx_bytes)
    except pdf_service.PdfError as exc:
        logger.warning("报告 PDF 未生成：%s", exc)
        return docx_bytes, None, ""
    if converter == pdf_service.WORD:
        updated = docx_render.clear_update_fields(updated)
    return updated, pdf_bytes, converter


def _discard_unsaved_files(record):
    """唯一约束冲突等失败时清理已写入磁盘、尚未入库的文件。"""
    for field_name in ("file", "pdf_file"):
        field = getattr(record, field_name, None)
        if field and field.name:
            try:
                field.delete(save=False)
            except Exception:
                logger.warning("清理未入库报告文件失败：%s", field.name, exc_info=True)


def generate_report(project, report_type, actor):
    """生成新版本报告。Word 排版与 PDF 转换在数据库事务之外完成，避免长时间占用写锁。

    版本号在短事务内用项目行锁分配；若仍撞唯一约束则清理孤儿文件并重试。
    """
    validate_can_generate(project, report_type, actor)
    files = _enterprise_files(project)
    report = build_report(project, report_type, package=files["package"], overview=files["overview"])
    now = timezone.localtime()
    report_no = f"{base_report_no(project, now)}-{TYPE_SUFFIX[report_type]}"
    enterprise_files = dict(files)
    payload, pdf_bytes, converter = _make_pdf(docx_render.render(report, report_no, enterprise_files=enterprise_files))

    last_error = None
    for _attempt in range(3):
        try:
            with transaction.atomic():
                locked = EvaluationProject.objects.select_for_update().get(pk=project.pk)
                max_version = locked.reports.filter(report_type=report_type).aggregate(v=Max("version"))["v"] or 0
                version = max_version + 1
                pkg = files["package"]
                return _store_report(
                    locked,
                    report_type,
                    actor,
                    report,
                    now,
                    version,
                    report_no,
                    payload,
                    pdf_bytes,
                    converter,
                    wbst=files["wbst"] if not pkg else None,
                    overview=pkg or files["overview"],
                    support=files["support"] if not pkg else None,
                )
        except IntegrityError as exc:
            last_error = exc
            logger.warning("报告版本冲突，准备重试：project=%s type=%s", project.id, report_type)
    raise ValidationError("报告版本号冲突，请重试生成。") from last_error


def _store_report(
    project,
    report_type,
    actor,
    report,
    now,
    version,
    report_no,
    payload,
    pdf_bytes,
    converter,
    wbst=None,
    overview=None,
    support=None,
):
    sha256 = hashlib.sha256(payload).hexdigest()
    filename = f"{report['title']}_{project.name}_V{version}.docx"
    levels = {part["sys"].code: part["achieved"] for part in report["parts"]}

    record = Report(
        project=project,
        report_type=report_type,
        version=version,
        report_no=report_no,
        filename=filename,
        sha256=sha256,
        trl_level=levels.get(TRL),
        mrl_level=levels.get(MRL),
        generated_by=actor,
        wbst_file=wbst,
        overview_file=overview,
        support_file=support,
    )
    try:
        record.file.save(filename, ContentFile(payload), save=False)
        if pdf_bytes:
            record.pdf_filename = filename[:-5] + ".pdf"
            record.pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            record.pdf_converter = converter
            record.pdf_file.save(record.pdf_filename, ContentFile(pdf_bytes), save=False)
        record.save()
    except Exception:
        _discard_unsaved_files(record)
        raise

    if not project.report_no:
        project.report_no = base_report_no(project, now)
        project.save(update_fields=["report_no", "updated_at"])
    for part in report["parts"]:
        assessment = part["assessment"]
        if assessment.status != Assessment.STATUS_REPORTED:
            assessment.status = Assessment.STATUS_REPORTED
            assessment.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        actor=actor,
        action="report.generate",
        model_name="Report",
        object_id=str(record.id),
        object_repr=filename,
        project=project,
        after={
            "report_type": report_type,
            "version": version,
            "sha256": sha256,
            "levels": levels,
            "pdf_sha256": record.pdf_sha256,
            "pdf_converter": converter,
        },
    )
    # 生成即视为同意该类型报告的待审核导出申请
    for pending in project.report_requests.filter(report_type=report_type, status=ReportRequest.STATUS_PENDING):
        _mark_request_approved(pending, actor, record)
    notify(
        project.owner,
        "report.generate",
        f"{record.get_report_type_display()} V{version} 已出具，可以下载",
        body=f"项目：{project.name}\n报告编号：{report_no}",
        link=f"{report_link(project)}?type={report_type}",
        project=project,
        email=True,
    )
    return record


def make_report_pdf(record, actor):
    """为已生成、但当时没有转换工具的报告版本补生成 PDF（Word 文件保持不变）。"""
    if not can_generate_report(actor):
        raise PermissionDenied("报告由评价机构管理员生成。")
    if record.pdf_file:
        raise ValidationError("该版本已有 PDF。")
    with record.file.open("rb") as handle:
        docx_bytes = handle.read()
    try:
        _, pdf_bytes, converter = pdf_service.convert(docx_bytes)
    except pdf_service.PdfError as exc:
        raise ValidationError(str(exc)) from exc
    with transaction.atomic():
        record.pdf_filename = record.filename[:-5] + ".pdf"
        record.pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        record.pdf_converter = converter
        record.pdf_file.save(record.pdf_filename, ContentFile(pdf_bytes), save=False)
        record.save(update_fields=["pdf_file", "pdf_filename", "pdf_sha256", "pdf_converter"])
        AuditLog.objects.create(
            actor=actor,
            action="report.pdf",
            model_name="Report",
            object_id=str(record.id),
            object_repr=record.pdf_filename,
            project=record.project,
            after={"pdf_sha256": record.pdf_sha256, "pdf_converter": converter},
        )
    return record


# ---------------------------------------------------------------------------
# 报告导出申请：企业申请 → 管理员审核通过即生成报告
# ---------------------------------------------------------------------------


def report_link(project):
    return reverse("reports:report_preview", args=[project.id])


def get_pending_request(project, report_type):
    return (
        project.report_requests.filter(report_type=report_type, status=ReportRequest.STATUS_PENDING)
        .select_related("requested_by__enterprise")
        .first()
    )


def get_last_request(project, report_type):
    return project.report_requests.filter(report_type=report_type).select_related("reviewed_by").first()


def _log_request(actor, action, request, after=None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name="ReportRequest",
        object_id=str(request.id),
        object_repr=request.get_report_type_display(),
        project=request.project,
        after=after,
    )


@transaction.atomic
def request_export(project, report_type, actor):
    if not is_project_owner(actor, project):
        raise PermissionDenied("报告导出由申报企业申请。")
    if report_type not in enabled_report_types():
        raise ValidationError("报告类型无效。")
    blockers = request_blockers(project, report_type)
    if blockers:
        raise ValidationError(f"暂不能申请导出{TYPE_TITLES[report_type]}：{'；'.join(blockers)}。")
    if get_pending_request(project, report_type):
        raise ValidationError("该报告已有导出申请正在审核中，请等待审核结果。")
    request = ReportRequest.objects.create(project=project, report_type=report_type, requested_by=actor)
    _log_request(actor, "report.request", request, after={"report_type": report_type})
    notify_admins(
        "report.request",
        f"{project.applicant}申请导出{TYPE_TITLES[report_type]}，待审批",
        body=f"项目：{project.name}",
        link=f"{report_link(project)}?type={report_type}",
        project=project,
        category=CATEGORY_TODO,
    )
    return request


def approve_request(request, actor):
    if not can_generate_report(actor):
        raise PermissionDenied("导出申请由评价机构管理员审核。")
    if request.status != ReportRequest.STATUS_PENDING:
        raise ValidationError("该申请已处理，不能重复审核。")
    record = generate_report(request.project, request.report_type, actor)
    request.refresh_from_db()
    return record


def _mark_request_approved(request, actor, record):
    request.status = ReportRequest.STATUS_APPROVED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.report = record
    request.save(update_fields=["status", "reviewed_by", "reviewed_at", "report"])
    _log_request(actor, "report.request_approve", request, after={"version": record.version, "report_no": record.report_no})


@transaction.atomic
def reject_request(request, actor, reason):
    if not can_generate_report(actor):
        raise PermissionDenied("导出申请由评价机构管理员审核。")
    if request.status != ReportRequest.STATUS_PENDING:
        raise ValidationError("该申请已处理，不能重复审核。")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("驳回时必须填写原因，企业会看到该原因。")
    request.status = ReportRequest.STATUS_REJECTED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.reject_reason = reason
    request.save(update_fields=["status", "reviewed_by", "reviewed_at", "reject_reason"])
    _log_request(actor, "report.request_reject", request, after={"reason": reason})
    notify(
        request.project.owner,
        "report.request_reject",
        f"{request.get_report_type_display()}导出申请被驳回",
        body=f"项目：{request.project.name}\n驳回原因：{reason}",
        link=f"{report_link(request.project)}?type={request.report_type}",
        project=request.project,
    )
    return request
