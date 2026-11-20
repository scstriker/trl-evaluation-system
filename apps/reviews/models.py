"""专家评审：按轮次留痕。

一轮 = 管理员对企业已提交的级别指派专家 → 三位专家独立逐条评审 → 管理员终审。
- 评审范围是已提交级别中尚未通过专家评审的条目，因此复评只针对上一轮终审为“不通过”的条目。
- 每轮提交时把条目的自评内容和佐证引用存为“提交留档”，之后企业再修改也不影响已留档内容。
- 改派专家时撤销原分配、新建一条分配；是否逾期按截止时间实时计算，不存成状态。
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ReviewRound(models.Model):
    STATUS_ASSIGNING = "assigning"
    STATUS_REVIEWING = "reviewing"
    STATUS_RULING = "ruling"
    STATUS_RECTIFYING = "rectifying"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_ASSIGNING, "待组建专家组"),
        (STATUS_REVIEWING, "专家评审中"),
        (STATUS_RULING, "待终审"),
        (STATUS_RECTIFYING, "整改中"),
        (STATUS_CLOSED, "已结束"),
    ]
    OPEN_STATUSES = (STATUS_ASSIGNING, STATUS_REVIEWING, STATUS_RULING, STATUS_RECTIFYING)
    LOCKED_STATUSES = (STATUS_ASSIGNING, STATUS_REVIEWING, STATUS_RULING)

    OUTCOME_RECTIFIED = "rectified"
    OUTCOME_PASSED = "passed"
    OUTCOME_FAILED = "closed_with_failures"
    OUTCOME_CANCELLED = "cancelled"
    OUTCOME_CHOICES = [
        (OUTCOME_RECTIFIED, "已按整改要求重新提交"),
        (OUTCOME_PASSED, "全部通过"),
        (OUTCOME_FAILED, "评审结束，部分条目未通过"),
        (OUTCOME_CANCELLED, "流程调整，已撤销"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey("evaluations.Assessment", on_delete=models.CASCADE, related_name="review_rounds")
    number = models.PositiveSmallIntegerField("轮次")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ASSIGNING)
    outcome = models.CharField(max_length=24, choices=OUTCOME_CHOICES, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField("提交时间", default=timezone.now)
    review_days = models.PositiveSmallIntegerField("专家评审期限（天）", default=7)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    finalized_at = models.DateTimeField("终审时间", null=True, blank=True)
    feedback_summary = models.TextField("终审意见总述（企业可见）", blank=True)
    rectify_due_at = models.DateTimeField("整改截止", null=True, blank=True)
    rectified_at = models.DateTimeField("提交整改时间", null=True, blank=True)

    class Meta:
        ordering = ["assessment", "number"]
        constraints = [
            models.UniqueConstraint(fields=["assessment", "number"], name="unique_review_round"),
            models.UniqueConstraint(
                fields=["assessment"], condition=~Q(status="closed"), name="one_open_review_round"
            ),
        ]

    def __str__(self):
        return f"{self.assessment} 第 {self.number} 轮专家评审"

    @property
    def is_open(self):
        return self.status != self.STATUS_CLOSED

    @property
    def rectify_overdue(self):
        return self.status == self.STATUS_RECTIFYING and self.rectify_due_at and self.rectify_due_at < timezone.now()


class RoundItem(models.Model):
    """一轮评审范围内的一个条目：提交留档 + 管理员终审结论。"""

    RESULT_PASS = "pass"
    RESULT_FAIL = "fail"
    RESULT_CHOICES = [(RESULT_PASS, "通过"), (RESULT_FAIL, "不通过")]
    BASIS_UNANIMOUS = "unanimous"
    BASIS_RULING = "ruling"
    BASIS_CHOICES = [(BASIS_UNANIMOUS, "专家意见一致"), (BASIS_RULING, "管理员裁定")]

    round = models.ForeignKey(ReviewRound, on_delete=models.CASCADE, related_name="items")
    check_item = models.ForeignKey("evaluations.CheckItem", on_delete=models.CASCADE, related_name="round_items")
    # 提交留档
    snap_result = models.CharField(max_length=32)
    snap_statement = models.TextField(blank=True)
    snap_support_info = models.TextField(blank=True)
    snap_evidence = models.JSONField(default=list, blank=True)
    # 终审
    final_result = models.CharField(max_length=8, choices=RESULT_CHOICES, blank=True)
    final_basis = models.CharField(max_length=12, choices=BASIS_CHOICES, blank=True)
    ruling_reason = models.TextField("裁定理由（仅评价机构可见）", blank=True)
    feedback = models.TextField("整改要求（企业可见）", blank=True)
    ruled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ruled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["round", "check_item__order_index"]
        constraints = [models.UniqueConstraint(fields=["round", "check_item"], name="unique_round_item")]

    def __str__(self):
        return f"{self.round} {self.check_item.code}"


class ExpertAssignment(models.Model):
    STATUS_INVITED = "invited"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_SUBMITTED = "submitted"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_INVITED, "待接受"),
        (STATUS_ACCEPTED, "评审中"),
        (STATUS_DECLINED, "已拒绝"),
        (STATUS_SUBMITTED, "已提交"),
        (STATUS_CANCELLED, "已撤销"),
    ]
    ACTIVE_STATUSES = (STATUS_INVITED, STATUS_ACCEPTED, STATUS_SUBMITTED)
    PENDING_STATUSES = (STATUS_INVITED, STATUS_ACCEPTED)

    round = models.ForeignKey(ReviewRound, on_delete=models.CASCADE, related_name="assignments")
    expert = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="review_assignments")
    slot = models.PositiveSmallIntegerField("席位")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_INVITED)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField("评审截止")
    responded_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.CharField("拒绝理由", max_length=255, blank=True)
    pledge_version = models.CharField(max_length=16, blank=True)
    pledged_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField("撤销原因", max_length=255, blank=True)
    replaces = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replaced_by")
    remind_count = models.PositiveSmallIntegerField(default=0)
    last_reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["round", "slot", "assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["round", "slot"],
                condition=Q(status__in=["invited", "accepted", "submitted"]),
                name="one_active_assignment_per_slot",
            ),
            models.UniqueConstraint(
                fields=["round", "expert"],
                condition=Q(status__in=["invited", "accepted", "submitted"]),
                name="expert_once_per_round",
            ),
            models.CheckConstraint(condition=Q(slot__gte=1, slot__lte=3), name="assignment_slot_range"),
        ]

    def __str__(self):
        return f"{self.round} 席位 {self.slot} {self.get_status_display()}"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_overdue(self):
        return self.status in self.PENDING_STATUSES and self.due_at < timezone.now()

    @property
    def overdue_days(self):
        if not self.is_overdue:
            return 0
        return max(1, (timezone.now() - self.due_at).days)


class ExpertOpinion(models.Model):
    VERDICT_PASS = RoundItem.RESULT_PASS
    VERDICT_FAIL = RoundItem.RESULT_FAIL
    VERDICT_CHOICES = RoundItem.RESULT_CHOICES

    assignment = models.ForeignKey(ExpertAssignment, on_delete=models.CASCADE, related_name="opinions")
    round_item = models.ForeignKey(RoundItem, on_delete=models.CASCADE, related_name="opinions")
    verdict = models.CharField("是否通过", max_length=8, choices=VERDICT_CHOICES, blank=True)
    suggestion = models.TextField("满足情况评价", blank=True)
    comment = models.TextField("支撑材料评价", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "round_item"], name="unique_expert_opinion")]

    def __str__(self):
        return f"{self.assignment} {self.round_item.check_item.code}"


class RemunerationRecord(models.Model):
    """专家劳务记录：专家提交一轮评审意见后自动生成，管理员标记结算。项目彻底删除后仍保留。"""

    assignment = models.OneToOneField(
        ExpertAssignment, null=True, blank=True, on_delete=models.SET_NULL, related_name="remuneration"
    )
    expert = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="remunerations")
    project = models.ForeignKey(
        "evaluations.EvaluationProject", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    project_name = models.CharField(max_length=255)
    system = models.CharField(max_length=8)
    round_number = models.PositiveSmallIntegerField()
    item_count = models.PositiveSmallIntegerField(default=0)
    amount = models.DecimalField("金额（元）", max_digits=10, decimal_places=2, default=0)
    is_settled = models.BooleanField("已结算", default=False)
    settled_at = models.DateTimeField(null=True, blank=True)
    settled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField("备注", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project_name} 第 {self.round_number} 轮"
