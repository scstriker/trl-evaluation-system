import hashlib
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.audit.models import AuditLog
from apps.evaluations.models import EvaluationProject
from apps.evidence.models import EvidenceFile

ALLOWED_EXTENSIONS = {".doc", ".docx", ".pdf", ".jpg", ".jpeg", ".png", ".webp"}


def _ensure_project_is_mutable(project, purpose):
    if project.status == EvaluationProject.STATUS_ARCHIVED:
        raise ValidationError(f"当前项目已归档，不能{purpose}。")
    if project.status == EvaluationProject.STATUS_LOCKED:
        raise ValidationError(f"当前项目已锁定，不能{purpose}。")
    if project.status == EvaluationProject.STATUS_REPORTED:
        raise ValidationError(f"当前项目已生成报告，不能{purpose}。")


def _uploaded_file_sha256(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def _validate_uploaded_file(uploaded_file):
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("文件格式不支持。仅允许 doc、docx、pdf、jpg、jpeg、png、webp。")
    max_size = settings.MAX_EVIDENCE_FILE_SIZE_MB * 1024 * 1024
    if uploaded_file.size > max_size:
        raise ValidationError(f"文件超过大小限制（{settings.MAX_EVIDENCE_FILE_SIZE_MB}MB）。")


@transaction.atomic
def upload_evidence_file(check_item, actor, uploaded_file, description=""):
    _ensure_project_is_mutable(check_item.project, "上传佐证材料")
    _validate_uploaded_file(uploaded_file)

    original_filename = uploaded_file.name
    stored_filename = f"{uuid.uuid4().hex}_{get_valid_filename(original_filename)}"
    sha256 = _uploaded_file_sha256(uploaded_file)

    evidence = EvidenceFile.objects.create(
        check_item=check_item,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file=uploaded_file,
        mime_type=getattr(uploaded_file, "content_type", "") or "",
        size_bytes=uploaded_file.size,
        sha256=sha256,
        description=description,
        uploaded_by=actor,
    )
    AuditLog.objects.create(
        actor=actor,
        action="evidence.upload",
        model_name="EvidenceFile",
        object_id=str(evidence.id),
        object_repr=evidence.original_filename,
        project=check_item.project,
        before=None,
        after={"sha256": evidence.sha256, "original_filename": evidence.original_filename},
    )
    return evidence


@transaction.atomic
def soft_delete_evidence(evidence, actor):
    _ensure_project_is_mutable(evidence.check_item.project, "删除佐证材料")
    if evidence.is_deleted:
        raise ValidationError("佐证材料已删除，不能重复删除。")
    before = {"is_deleted": evidence.is_deleted}
    evidence.is_deleted = True
    evidence.deleted_by = actor
    evidence.deleted_at = timezone.now()
    evidence.save(update_fields=["is_deleted", "deleted_by", "deleted_at"])
    AuditLog.objects.create(
        actor=actor,
        action="evidence.delete",
        model_name="EvidenceFile",
        object_id=str(evidence.id),
        object_repr=evidence.original_filename,
        project=evidence.check_item.project,
        before=before,
        after={"is_deleted": True},
    )
    return evidence
