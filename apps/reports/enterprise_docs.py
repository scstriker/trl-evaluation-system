"""企业填写用 Word 模板：下载空白模板、上传已填文件。"""

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.text import get_valid_filename

from apps.evidence.services import ALLOWED_EXTENSIONS, _file_extension, _validate_uploaded_file

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


def store_upload(project, kind, uploaded_file, actor):
    from apps.reports.models import EnterpriseProjectFile

    if kind not in TEMPLATE_FILES:
        raise ValidationError("未知上传类型。")
    validate_enterprise_upload(uploaded_file)
    ext = _file_extension(uploaded_file)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError("文件格式不支持。")
    original = (uploaded_file.name or "file").strip()
    stored_name = get_valid_filename(original)
    obj, _created = EnterpriseProjectFile.objects.update_or_create(
        project=project,
        kind=kind,
        defaults={
            "original_filename": original,
            "uploaded_by": actor,
        },
    )
    obj.file.save(stored_name, uploaded_file, save=True)
    return obj