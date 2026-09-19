import uuid

from django.conf import settings
from django.db import models


def report_upload_path(instance, filename):
    return f"reports/{instance.project_id}/{filename}"


class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("evaluations.EvaluationProject", on_delete=models.CASCADE, related_name="reports")
    version = models.PositiveIntegerField()
    file = models.FileField(upload_to=report_upload_path)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    achieved_level = models.PositiveSmallIntegerField()
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generated_reports")
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "version"], name="unique_report_version"),
        ]
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.project.name} V{self.version}"
