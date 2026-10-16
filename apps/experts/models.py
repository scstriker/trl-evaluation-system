from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

MAX_TAGS = 3


class ExpertProfile(models.Model):
    """评审专家档案：字段沿用传感器专项专家库（姓名、单位、职称、职务、在职、领域、标识、联系方式），
    另加百科式主页内容（简介、研究方向、代表成果、经历、个人网站）。

    专家账号是一个普通 User，只能看到分配给自己的评审任务。
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="expert_profile")
    name = models.CharField("姓名", max_length=64)
    organization = models.CharField("工作单位", max_length=255)
    title = models.CharField("职称", max_length=64, blank=True)
    position = models.CharField("职务", max_length=64, blank=True)
    in_service = models.BooleanField("是否在职", default=True)
    specialty = models.ForeignKey(
        "rules.Specialty", on_delete=models.PROTECT, related_name="experts", verbose_name="专业领域"
    )
    tags = models.JSONField("专家标识", default=list, blank=True)
    phone = models.CharField("手机", max_length=32, blank=True)
    email = models.EmailField("邮箱", blank=True)
    bio = models.TextField("简介", blank=True)
    research = models.TextField("研究方向", blank=True)
    achievements = models.TextField("代表成果", blank=True)
    experience = models.TextField("教育与任职经历", blank=True)
    website = models.URLField("个人网站", blank=True)
    is_sample = models.BooleanField("样例数据", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["specialty__order", "name"]
        indexes = [models.Index(fields=["specialty", "in_service"], name="expert_specialty_service")]

    def __str__(self):
        return self.name

    def clean(self):
        tags = [tag for tag in (self.tags or []) if str(tag).strip()]
        if len(tags) > MAX_TAGS:
            raise ValidationError({"tags": f"专家标识最多 {MAX_TAGS} 个。"})
        self.tags = [str(tag).strip() for tag in tags]

    @property
    def has_homepage(self):
        """有简介或代表成果的专家显示百科式主页，其余只显示基本信息。"""
        return bool(self.bio or self.achievements)

    @property
    def is_available(self):
        """可被分配评审任务：账号启用且在职。"""
        return self.user.is_active and self.in_service

    @property
    def initial(self):
        return self.name[:1]

    def lines(self, field):
        return [line.strip() for line in getattr(self, field).splitlines() if line.strip()]

    @property
    def achievement_lines(self):
        return self.lines("achievements")

    @property
    def experience_lines(self):
        return self.lines("experience")
