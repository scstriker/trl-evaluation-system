import uuid

from django.conf import settings
from django.db import models

from apps.rules.models import RuleItem
from apps.rules.systems import SYSTEM_CHOICES, TRL, get_system

DEFAULT_ISSUER = "机械工业仪器仪表综合技术经济研究所"


class EvaluationProject(models.Model):
    """技术项目：企业申报的评价对象，下挂技术就绪度 / 制造成熟度两类评价。"""

    TECH_EQUIPMENT = "equipment"
    TECH_HARDWARE = "hardware"
    TECH_SOFTWARE = "software"

    TECH_TYPE_CHOICES = [
        (TECH_EQUIPMENT, "设备技术（包括软件和硬件）"),
        (TECH_HARDWARE, "一般硬件产品技术"),
        (TECH_SOFTWARE, "计算机软件技术"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("技术项目名称", max_length=255)
    applicant = models.CharField("委托（受评）单位", max_length=255)
    issuer = models.CharField("评价出具单位", max_length=255, default=DEFAULT_ISSUER)
    domain = models.CharField("应用领域", max_length=255, blank=True)
    tech_type = models.CharField("技术类型", max_length=32, choices=TECH_TYPE_CHOICES)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="projects")
    report_no = models.CharField("报告编号", max_length=64, blank=True)
    overview_text = models.TextField("项目概况", blank=True)
    remark = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return self.name

    def get_assessment(self, system):
        return next((a for a in self.assessments.all() if a.system == system), None)


class Assessment(models.Model):
    """项目下的一类评价（技术就绪度或制造成熟度）：目标等级、逐级提交与审核状态。"""

    STATUS_IN_PROGRESS = "in_progress"
    STATUS_REPORTED = "reported"

    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, "评价中"),
        (STATUS_REPORTED, "已出报告"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(EvaluationProject, on_delete=models.CASCADE, related_name="assessments")
    system = models.CharField("评价类别", max_length=8, choices=SYSTEM_CHOICES)
    target_level = models.PositiveSmallIntegerField("目标等级")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    summary_text = models.TextField("评价综述", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "system"], name="unique_project_assessment"),
        ]
        # "trl" > "mrl"：倒序使技术就绪度排在前面
        ordering = ["project", "-system"]

    def __str__(self):
        return f"{self.project.name} · {self.sys.name}评价"

    @property
    def sys(self):
        return get_system(self.system)

    @property
    def is_trl(self):
        return self.system == TRL


class CheckItem(models.Model):
    """评价条目：项目创建时从细则库复制，此后与细则库的修订互不影响。"""

    RESULT_UNEVALUATED = "unevaluated"
    RESULT_SATISFIED = "satisfied"
    RESULT_NOT_SATISFIED = "not_satisfied"
    RESULT_NOT_APPLICABLE = "not_applicable"

    RESULT_CHOICES = [
        (RESULT_UNEVALUATED, "未填写"),
        (RESULT_SATISFIED, "满足"),
        (RESULT_NOT_SATISFIED, "不满足"),
        (RESULT_NOT_APPLICABLE, "不适用"),
    ]

    TRACK_HARDWARE = RuleItem.TRACK_HARDWARE
    TRACK_SOFTWARE = RuleItem.TRACK_SOFTWARE
    TRACK_GENERAL = RuleItem.TRACK_GENERAL
    TRACK_CHOICES = RuleItem.TRACK_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="items")
    order_index = models.PositiveIntegerField()

    level = models.PositiveSmallIntegerField()
    track = models.CharField(max_length=8, choices=TRACK_CHOICES)
    seq = models.PositiveSmallIntegerField()
    name = models.TextField("具体化等级条件")
    evidence = models.CharField("评价支撑信息要求", max_length=255)

    result = models.CharField("自评结论", max_length=32, choices=RESULT_CHOICES, default=RESULT_UNEVALUATED)
    statement = models.TextField("满足情况说明", blank=True)
    support_info = models.TextField("评价支撑信息", blank=True)
    review_comment = models.TextField("审核意见", blank=True)

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
            models.UniqueConstraint(fields=["assessment", "level", "track", "seq"], name="unique_check_item"),
        ]
        ordering = ["assessment", "order_index"]

    def __str__(self):
        return self.code

    @property
    def project(self):
        return self.assessment.project

    @property
    def code(self):
        abbr = self.assessment.sys.abbr
        if self.track == self.TRACK_GENERAL:
            return f"{abbr}{self.level}-{self.seq}"
        return f"{abbr}{self.level}-{'HW' if self.track == self.TRACK_HARDWARE else 'SW'}-{self.seq}"

    @property
    def active_evidence_count(self):
        return sum(1 for evidence in self.evidence_files.all() if not evidence.is_deleted)


class LevelReview(models.Model):
    """逐级提交与审核记录：企业提交 → 管理员审核通过或退回。"""

    STATUS_SUBMITTED = "submitted"
    STATUS_PASSED = "passed"
    STATUS_RETURNED = "returned"

    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "已提交待审核"),
        (STATUS_PASSED, "审核通过"),
        (STATUS_RETURNED, "已退回"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="level_reviews")
    level = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="submitted_levels"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_levels"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField("审核意见", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assessment", "level"], name="unique_level_review"),
        ]
        ordering = ["assessment", "level"]

    def __str__(self):
        return f"{self.assessment} {self.assessment.sys.abbr}{self.level} {self.get_status_display()}"


class EvaluationTeamMember(models.Model):
    """报告“评价人员”表：由管理员按项目填写。"""

    project = models.ForeignKey(EvaluationProject, on_delete=models.CASCADE, related_name="team_members")
    order = models.PositiveSmallIntegerField(default=0)
    role = models.CharField("角色", max_length=32)
    name = models.CharField("姓名", max_length=64)
    title = models.CharField("职称", max_length=64, blank=True)
    specialty = models.CharField("专业及特长", max_length=128, blank=True)
    org = models.CharField("工作单位", max_length=255, blank=True)

    class Meta:
        ordering = ["project", "order", "id"]

    def __str__(self):
        return f"{self.role} {self.name}"
