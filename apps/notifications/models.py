from django.conf import settings
from django.db import models

# 消息类别：(名称, 标签色)。管理员收到的多为待办 / 预警，企业多为结果，专家多为任务。
# 品牌红每屏至多一处，列表里的预警统一用警示色。
CATEGORY_TODO = "todo"
CATEGORY_ALERT = "alert"
CATEGORY_RESULT = "result"
CATEGORY_TASK = "task"
CATEGORIES = {
    CATEGORY_TODO: ("待办", "warning"),
    CATEGORY_ALERT: ("预警", "warning"),
    CATEGORY_RESULT: ("结果", "neutral"),
    CATEGORY_TASK: ("任务", "info"),
}


class Notification(models.Model):
    """站内私信：系统在流程节点上发给管理员、企业、专家的消息。

    email_pending 标记“邮件预留节点”：接入邮件服务后只对这些消息发邮件，本系统暂不实际发送。
    """

    CHANNEL_SITE = "site"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField("事件", max_length=48)
    category = models.CharField("类别", max_length=16, default=CATEGORY_RESULT)
    title = models.CharField("标题", max_length=255)
    body = models.TextField("内容", blank=True)
    link = models.CharField("跳转链接", max_length=500, blank=True)
    project = models.ForeignKey(
        "evaluations.EvaluationProject", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    channel = models.CharField(max_length=16, default=CHANNEL_SITE)
    email_pending = models.BooleanField("邮件节点", default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "read_at"], name="notif_recipient_read")]

    def __str__(self):
        return self.title

    @property
    def category_label(self):
        return CATEGORIES.get(self.category, ("消息", "neutral"))[0]

    @property
    def category_tone(self):
        return CATEGORIES.get(self.category, ("消息", "neutral"))[1]


class SentReminder(models.Model):
    """自动提醒的发送记录：同一事项、同一截止日只提醒一次（key 唯一）。"""

    key = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=48)
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.key
