import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

DEFAULT_ISSUER = "机械工业仪器仪表综合技术经济研究所"


class EvaluationProject(models.Model):
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_LOCKED = "locked"
    STATUS_REPORTED = "reported"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, "评价中"),
        (STATUS_LOCKED, "已锁定"),
        (STATUS_REPORTED, "已出报告"),
        (STATUS_ARCHIVED, "已归档"),
    ]

    TRACK_EQUIPMENT = "equipment"
    TRACK_HARDWARE = "hardware"
    TRACK_SOFTWARE = "software"

    TRACK_CHOICES = [
        (TRACK_EQUIPMENT, "设备技术（包括软件和硬件）"),
        (TRACK_HARDWARE, "一般硬件产品技术"),
        (TRACK_SOFTWARE, "计算机软件技术"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("技术项目名称", max_length=255)
    applicant = models.CharField("委托（受评）单位", max_length=255)
    issuer = models.CharField("评价出具单位", max_length=255, default=DEFAULT_ISSUER)
    domain = models.CharField("应用领域", max_length=255, blank=True)
    track = models.CharField("评价轨道", max_length=32, choices=TRACK_CHOICES)
    target_level = models.PositiveSmallIntegerField("目标就绪度等级")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    unlocked_level = models.PositiveSmallIntegerField("当前核验级别", default=1)
    report_no = models.CharField("报告编号", max_length=64, blank=True)
    overview_text = models.TextField("项目概况", blank=True)
    summary_text = models.TextField("评价综述", blank=True)
    remark = models.TextField("备注", blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_projects")
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="locked_projects",
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return self.name

    def clean(self):
        if not 1 <= int(self.target_level) <= 9:
            raise ValidationError({"target_level": "目标就绪度等级必须是 TRL 1~9。"})


class CheckItem(models.Model):
    """评价条目快照：项目创建时从规则库物化，与规则库解耦。"""

    RESULT_UNEVALUATED = "unevaluated"
    RESULT_SATISFIED = "satisfied"
    RESULT_NOT_SATISFIED = "not_satisfied"
    RESULT_NOT_APPLICABLE = "not_applicable"

    RESULT_CHOICES = [
        (RESULT_UNEVALUATED, "未判定"),
        (RESULT_SATISFIED, "满足"),
        (RESULT_NOT_SATISFIED, "不满足"),
        (RESULT_NOT_APPLICABLE, "不适用"),
    ]

    TRACK_HARDWARE = "hw"
    TRACK_SOFTWARE = "sw"
    TRACK_CHOICES = [
        (TRACK_HARDWARE, "硬件"),
        (TRACK_SOFTWARE, "软件"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(EvaluationProject, on_delete=models.CASCADE, related_name="items")
    order_index = models.PositiveIntegerField()

    level = models.PositiveSmallIntegerField()
    track = models.CharField(max_length=8, choices=TRACK_CHOICES)
    seq = models.PositiveSmallIntegerField()
    name = models.TextField("具体化等级条件")
    evidence = models.CharField("评价支撑信息要求", max_length=255)

    result = models.CharField(max_length=32, choices=RESULT_CHOICES, default=RESULT_UNEVALUATED)
    statement = models.TextField("满足情况说明", blank=True)
    support_info = models.TextField("评价支撑信息", blank=True)

    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="evaluated_check_items",
    )
    evaluated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "level", "track", "seq"], name="unique_check_item"),
        ]
        ordering = ["project", "order_index"]

    def __str__(self):
        return f"{self.project.name} TRL{self.level}-{self.get_track_display()}-{self.seq}"

    @property
    def code(self):
        return f"TRL{self.level}-{'HW' if self.track == self.TRACK_HARDWARE else 'SW'}-{self.seq}"

    @property
    def active_evidence_count(self):
        return self.evidence_files.filter(is_deleted=False).count()


class LevelGate(models.Model):
    """级别审核通过记录：本级全部条目判定完成后由评审人确认通过。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(EvaluationProject, on_delete=models.CASCADE, related_name="level_gates")
    level = models.PositiveSmallIntegerField()
    passed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="passed_level_gates")
    passed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "level"], name="unique_level_gate"),
        ]
        ordering = ["project", "level"]

    def __str__(self):
        return f"{self.project.name} TRL{self.level} 通过"
