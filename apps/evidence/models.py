import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils.text import get_valid_filename


def evidence_upload_path(instance, filename):
    safe_name = get_valid_filename(instance.stored_filename or filename)
    return f"evidence/{instance.check_item.assessment.project_id}/{instance.check_item_id}/{safe_name}"


class EvidenceFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    check_item = models.ForeignKey("evaluations.CheckItem", on_delete=models.CASCADE, related_name="evidence_files")
    original_filename = models.CharField(max_length=255)
    stored_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=evidence_upload_path)
    mime_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_evidence_files")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_evidence_files",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.original_filename

    @property
    def size_display(self):
        size = self.size_bytes
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
            size /= 1024

    @property
    def preview_kind(self):
        extension = Path(self.original_filename).suffix.lower()
        if extension in {".jpg", ".jpeg", ".png", ".webp"}:
            return "image"
        if extension == ".pdf":
            return "pdf"
        return "other"
