"""本地一次性：用本次企业模板合并流程生成对比用 Word（纯文字 docx 上传，无佐证附件）。

用法（trl-evaluation-system 目录）：
    python manage.py seed_sample
    python scripts/export_comparison_report.py

输出到：261115-TRLDemo-第四次修改/发陈实-技术就绪度软件相关/（软件生成-本次本地）技术就绪度评价报告.docx
"""

from __future__ import annotations

import os
import shutil
import sys
from io import BytesIO
from pathlib import Path

import django

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from docx import Document

from apps.evaluations import services as eval_services
from apps.evaluations.models import EvaluationProject, Assessment
from apps.reports import enterprise_docs
from apps.reports.models import EnterpriseProjectFile, Report
from apps.reports.services import generate_report, get_front_matter, report_availability
from apps.rules.systems import TRL
sys.path.insert(0, str(BASE / "scripts"))
from comparison_content import build_overview_docx, build_support_docx

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

OUT_DIR = Path(r"C:\Users\61973\Desktop\Project\261115-TRLDemo-第四次修改\发陈实-技术就绪度软件相关\发陈实-技术就绪度软件相关")
OUT_FILE = OUT_DIR / "（软件生成-本次本地）技术就绪度评价报告_大功率波长色散X荧光光谱仪.docx"
OUT_FILE_ALT = OUT_DIR / "（软件生成-本次本地）技术就绪度评价报告_大功率波长色散X荧光光谱仪_V5.docx"


def _upload_bytes(project, kind, filename, payload: bytes):
    return enterprise_docs.store_upload(
        project,
        kind,
        SimpleUploadedFile(filename, payload, content_type=_DOCX),
        project.owner,
    )


def _wbst_docx() -> bytes:
    tpl = BASE / "data" / "templates" / "enterprise" / "附录A-WBST-模板.docx"
    if not tpl.is_file():
        from tests.report_enterprise_fixtures import minimal_docx

        return minimal_docx("附录A WBST", "（未找到官方模板）")
    doc = Document(str(tpl))
    note = doc.add_paragraph(
        "【本地对比稿】下表为表 c.1 WBST 模板；交付时由企业按项目交付物填写，此处保留版式。"
    )
    note.runs[0].italic = True
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _find_project(enterprise):
    for name in ("高性能 XRF 光学系统", "大功率波长色散X荧光光谱仪"):
        project = EvaluationProject.objects.filter(owner=enterprise, name=name, deleted_at__isnull=True).first()
        if project:
            return project
    raise SystemExit("请先运行 python manage.py seed_sample，或确保 hexin 名下存在可出报告的样例项目。")


def _front_matter_admin(project):
    fm = get_front_matter(project)
    fm.compiled_by, fm.checked_by, fm.approved_by = "张编制", "李审核", "王批准"
    fm.s1_1_1 = "（封面壳层预留；正文见企业上传概况 Word。）"
    fm.save()


def main():
    admin = User.objects.get(username="admin")
    enterprise = User.objects.get(username="hexin")

    project = _find_project(enterprise)
    if report_availability(project)[Report.TYPE_TRL]["reasons"]:
        raise SystemExit(f"项目未达出具条件：{report_availability(project)[Report.TYPE_TRL]['reasons']}")

    project.name = "大功率波长色散X荧光光谱仪"
    project.applicant = "钢研纳克检测技术股份有限公司"
    project.overview_text = "CNX-838 大功率波长色散 X 荧光光谱仪工程样机研制与验证。"
    project.save(update_fields=["name", "applicant", "overview_text", "updated_at"])
    _front_matter_admin(project)

    def _build_package_docx() -> bytes:
        doc = Document()
        for payload in (build_overview_docx(), _wbst_docx(), build_support_docx()):
            src = Document(BytesIO(payload))
            for paragraph in src.paragraphs:
                text = paragraph.text.strip()
                if text:
                    doc.add_paragraph(text)
        buf = BytesIO()
        doc.save(buf)
        return buf.getvalue()

    _upload_bytes(project, EnterpriseProjectFile.KIND_PACKAGE, "企业报告材料-自动生成.docx", _build_package_docx())

    assessment = project.get_assessment(TRL)
    if assessment.status == Assessment.STATUS_REPORTED:
        eval_services.reopen_assessment(assessment, admin)

    with transaction.atomic():
        record = generate_report(project, Report.TYPE_TRL, admin)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with record.file.open("rb") as src:
        payload = src.read()
    for target in (OUT_FILE, OUT_FILE_ALT):
        try:
            target.write_bytes(payload)
        except PermissionError:
            continue
        print(f"已生成：{target}")
    if not OUT_FILE.exists() and OUT_FILE_ALT.exists():
        print("（原文件名被 Word 占用，请关闭后重跑导出或使用 _V5 文件）")
    print(f"报告编号：{record.report_no}  版本：V{record.version}  SHA256：{record.sha256[:16]}…")
    print(f"项目：{project.name}  达成：TRL {record.trl_level}")


if __name__ == "__main__":
    main()