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


class ReportRequest(models.Model):
    """报告导出申请：企业申请，评价机构审核通过时生成报告，企业随后才能下载和打印。"""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "待审核"),
        (STATUS_APPROVED, "已通过"),
        (STATUS_REJECTED, "已驳回"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("evaluations.EvaluationProject", on_delete=models.CASCADE, related_name="report_requests")
    report_type = models.CharField("报告类型", max_length=16, choices=Report.TYPE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="report_requests"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_report_requests"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField("驳回原因", max_length=255, blank=True)
    report = models.ForeignKey(Report, null=True, blank=True, on_delete=models.SET_NULL, related_name="requests")

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.project.name} {self.get_report_type_display()} {self.get_status_display()}"
