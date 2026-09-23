import uuid

from django.conf import settings
from django.db import models


def report_upload_path(instance, filename):
    return f"reports/{instance.project_id}/{filename}"


class Report(models.Model):
    TYPE_TRL = "trl"
    TYPE_MRL = "mrl"
    TYPE_COMBINED = "combined"

    TYPE_CHOICES = [
        (TYPE_TRL, "技术就绪度评价报告"),
        (TYPE_MRL, "制造成熟度评价报告"),
        (TYPE_COMBINED, "技术就绪度与制造成熟度综合评价报告"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("evaluations.EvaluationProject", on_delete=models.CASCADE, related_name="reports")
    report_type = models.CharField("报告类型", max_length=16, choices=TYPE_CHOICES)
    version = models.PositiveIntegerField()
    report_no = models.CharField("报告编号", max_length=80)
    file = models.FileField(upload_to=report_upload_path)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    trl_level = models.PositiveSmallIntegerField(null=True, blank=True)
    mrl_level = models.PositiveSmallIntegerField(null=True, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generated_reports")
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "report_type", "version"], name="unique_report_version"),
        ]
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.project.name} {self.get_report_type_display()} V{self.version}"

    @property
    def level_display(self):
        parts = []
        if self.trl_level is not None:
            parts.append(f"TRL {self.trl_level}")
        if self.mrl_level is not None:
            parts.append(f"MRL {self.mrl_level}")
        return " / ".join(parts)
