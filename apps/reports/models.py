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
    # 同一版本的 PDF（本机有 Word 或 LibreOffice 时生成），记录转换方式与哈希
    pdf_file = models.FileField(upload_to=report_upload_path, blank=True)
    pdf_filename = models.CharField(max_length=255, blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    pdf_converter = models.CharField(max_length=16, blank=True)
    wbst_file = models.ForeignKey(
        "reports.EnterpriseProjectFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bound_reports",
        verbose_name="归档 WBST",
    )
    overview_file = models.ForeignKey(
        "reports.EnterpriseProjectFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bound_report_overviews",
        verbose_name="归档项目概况",
    )
    support_file = models.ForeignKey(
        "reports.EnterpriseProjectFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bound_report_supports",
        verbose_name="归档评价支撑信息",
    )

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


class ReportFrontMatter(models.Model):
    """报告前置信息：由评价机构按固定模板逐节填写，生成报告时填入模板，其后的等级评价章节由系统自动拼接。"""

    project = models.OneToOneField("evaluations.EvaluationProject", on_delete=models.CASCADE, related_name="front_matter")
    compiled_by = models.CharField("编制", max_length=64, blank=True)
    checked_by = models.CharField("审核", max_length=64, blank=True)
    approved_by = models.CharField("批准", max_length=64, blank=True)
    issue_date = models.DateField("报告日期", null=True, blank=True)
    standard_version = models.CharField("评价依据的标准版本", max_length=255, blank=True)
    s1_1_1 = models.TextField("1.1.1 任务来源（研发背景）", blank=True)
    s1_1_2 = models.TextField("1.1.2 研究目标", blank=True)
    s1_1_3 = models.TextField("1.1.3 主要技术指标", blank=True)
    s1_1_4 = models.TextField("1.1.4 成果形式", blank=True)
    s1_1_5 = models.TextField("1.1.5 产品介绍及主要功能（技术简介）", blank=True)
    s1_2_1 = models.TextField("1.2.1 项目总体进展概述", blank=True)
    s1_2_2 = models.TextField("1.2.2 技术指标完成情况", blank=True)
    s1_2_3 = models.TextField("1.2.3 关键部件研制", blank=True)
    s1_2_4 = models.TextField("1.2.4 软件开发", blank=True)
    s2_1 = models.TextField("2.1 评价目的和范围", blank=True)
    s2_3 = models.TextField("2.3 评价人员（表前说明）", blank=True)
    s2_4 = models.TextField("2.4 评价过程", blank=True)
    s3_1 = models.TextField("3.1 产品描述", blank=True)
    s3_4_software = models.TextField("3.4 软件验证情况", blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    SECTION_FIELDS = [
        "s1_1_1", "s1_1_2", "s1_1_3", "s1_1_4", "s1_1_5",
        "s1_2_1", "s1_2_2", "s1_2_3", "s1_2_4",
        "s2_1", "s2_3", "s2_4", "s3_1", "s3_4_software",
    ]

    def __str__(self):
        return f"{self.project.name} 报告前置信息"


def enterprise_file_path(instance, filename):
    return f"enterprise/{instance.project_id}/{instance.kind}/{filename}"


class EnterpriseProjectFile(models.Model):
    """企业按模板填写后上传的材料（单文件 package 或历史分卷类型）。"""

    KIND_PACKAGE = "package"
    KIND_SUPPORT = "support"
    KIND_OVERVIEW = "overview"
    KIND_WBST = "wbst"
    KIND_CHOICES = [
        (KIND_PACKAGE, "企业报告材料"),
        (KIND_SUPPORT, "评价支撑信息"),
        (KIND_OVERVIEW, "项目概况"),
        (KIND_WBST, "附录A WBST 表"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("evaluations.EvaluationProject", on_delete=models.CASCADE, related_name="enterprise_files")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    file = models.FileField(upload_to=enterprise_file_path)
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="enterprise_uploads")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.project.name} {self.get_kind_display()}"


class ReportModule(models.Model):
    """3.2～3.4 按关键技术 / 模块逐个写设计、试制、验证情况。"""

    front_matter = models.ForeignKey(ReportFrontMatter, on_delete=models.CASCADE, related_name="modules")
    order = models.PositiveSmallIntegerField(default=0)
    name = models.CharField("关键技术 / 模块名称", max_length=128)
    design = models.TextField("设计情况", blank=True)
    trial = models.TextField("试制情况", blank=True)
    verification = models.TextField("验证情况", blank=True)

    class Meta:
        ordering = ["front_matter", "order", "id"]

    def __str__(self):
        return self.name
