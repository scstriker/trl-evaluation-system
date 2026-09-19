from django.db import models


class MaturityLevel(models.Model):
    """技术就绪度（TRL）等级定义，1~9 级。"""

    level = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=255, help_text="等级定义")

    class Meta:
        ordering = ["level"]

    def __str__(self):
        return f"TRL {self.level} {self.name}"


class RuleItem(models.Model):
    """《评价细则》条目：每级按硬件/软件两轨划分。"""

    TRACK_HARDWARE = "hw"
    TRACK_SOFTWARE = "sw"
    TRACK_CHOICES = [
        (TRACK_HARDWARE, "硬件"),
        (TRACK_SOFTWARE, "软件"),
    ]

    level = models.PositiveSmallIntegerField()
    track = models.CharField(max_length=8, choices=TRACK_CHOICES)
    seq = models.PositiveSmallIntegerField()
    name = models.TextField(help_text="具体化等级条件")
    evidence = models.CharField(max_length=255, help_text="评价支撑信息（佐证材料类型）")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["level", "track", "seq"], name="unique_rule_item"),
        ]
        ordering = ["level", "track", "seq"]

    def __str__(self):
        return f"TRL{self.level}-{self.get_track_display()}-{self.seq}"


class MrlLevel(models.Model):
    """制造成熟度（MRL）评级框架，1~10 级，预留扩展模块。"""

    level = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=128)
    desc = models.TextField()

    class Meta:
        ordering = ["level"]

    def __str__(self):
        return f"MRL {self.level} {self.name}"
