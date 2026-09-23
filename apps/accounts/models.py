from django.conf import settings
from django.db import models


class EnterpriseProfile(models.Model):
    """企业用户档案：企业自助注册，评价机构管理员审核启用后方可登录。"""

    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_REJECTED = "rejected"
    STATUS_DISABLED = "disabled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "待审核"),
        (STATUS_ACTIVE, "已启用"),
        (STATUS_REJECTED, "已驳回"),
        (STATUS_DISABLED, "已停用"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enterprise")
    company_name = models.CharField("企业中文全称", max_length=255)
    credit_code = models.CharField("统一社会信用代码", max_length=18, unique=True)
    contact_name = models.CharField("联系人姓名", max_length=64)
    phone = models.CharField("手机号码", max_length=11)
    status = models.CharField("账号状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reject_reason = models.CharField("驳回原因", max_length=255, blank=True)
    created_at = models.DateTimeField("注册时间", auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_enterprises",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.company_name
