"""企业填写用 Word 模板：下载空白模板、上传已填文件。"""

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils.text import get_valid_filename

from apps.evidence.services import ALLOWED_EXTENSIONS, _file_extension, _validate_uploaded_file
from apps.reports.docx_merge import validate_overview_docx

BASE_DIR = Path(settings.BASE_DIR)
TEMPLATE_DIR = BASE_DIR / "data" / "templates" / "enterprise"

TEMPLATE_FILES = {
    "support": "评价支撑信息-模板.docx",
    "overview": "项目概况-模板.docx",
    "wbst": "附录A-WBST-模板.docx",
}

KIND_LABELS = {
    "support": "评价支撑信息",
    "overview": "项目概况",
    "wbst": "附录A WBST 表",
}


def template_path(kind):
    if kind not in TEMPLATE_FILES:
        raise ValidationError("未知模板类型。")
    path = TEMPLATE_DIR / TEMPLATE_FILES[kind]
    if not path.is_file():
        raise ValidationError(f"模板文件尚未部署：{TEMPLATE_FILES[kind]}")
    return path


def validate_enterprise_upload(uploaded_file: UploadedFile):
    _validate_uploaded_file(uploaded_file)


def current_enterprise_file(project, kind):
    from apps.reports.models import EnterpriseProjectFile

    return (
        EnterpriseProjectFile.objects.filter(project=project, kind=kind)
        .order_by("-uploaded_at")
        .first()
    )


def latest_enterprise_files_by_kind(project):
    """每种 kind 取最新一条上传（允许多版本并存）。"""
    latest = {}
    for row in project.enterprise_files.order_by("-uploaded_at"):
        latest.setdefault(row.kind, row)
    return latest


@transaction.atomic
def store_upload(project, kind, uploaded_file, actor):
    from apps.reports.models import EnterpriseProjectFile

    if kind not in TEMPLATE_FILES:
        raise ValidationError("未知上传类型。")
    validate_enterprise_upload(uploaded_file)
    if kind == EnterpriseProjectFile.KIND_OVERVIEW:
        uploaded_file.seek(0)
        payload = uploaded_file.read()
        uploaded_file.seek(0)
        try:
            validate_overview_docx(payload)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
    ext = _file_extension(uploaded_file)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError("文件格式不支持。")
    original = (uploaded_file.name or "file").strip()
    stored_name = get_valid_filename(original)
    obj = EnterpriseProjectFile(
        project=project,
        kind=kind,
        original_filename=original,
        uploaded_by=actor,
    )
    obj.file.save(stored_name, uploaded_file, save=True)
    return obj