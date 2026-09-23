from django.db import models

from apps.rules.systems import SYSTEM_CHOICES


class LevelDefinition(models.Model):
    """等级定义：技术就绪度 1~9 级、制造成熟度 1~10 级。"""

    system = models.CharField(max_length=8, choices=SYSTEM_CHOICES)
    level = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=255, help_text="等级定义")
    desc = models.TextField(blank=True, help_text="等级说明")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["system", "level"], name="unique_level_definition"),
        ]
        ordering = ["system", "level"]

    def __str__(self):
        return f"{self.system.upper()} {self.level} {self.name}"


class RuleItem(models.Model):
    """评价细则条目：技术就绪度按硬件/软件划分，制造成熟度为通用条目。"""

    TRACK_HARDWARE = "hw"
    TRACK_SOFTWARE = "sw"
    TRACK_GENERAL = "gen"
    TRACK_CHOICES = [
        (TRACK_HARDWARE, "硬件"),
        (TRACK_SOFTWARE, "软件"),
        (TRACK_GENERAL, "通用"),
    ]

    system = models.CharField(max_length=8, choices=SYSTEM_CHOICES)
    level = models.PositiveSmallIntegerField()
    track = models.CharField(max_length=8, choices=TRACK_CHOICES)
    seq = models.PositiveSmallIntegerField()
    name = models.TextField(help_text="具体化等级条件")
    evidence = models.CharField(max_length=255, help_text="评价支撑信息（佐证材料类型）")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["system", "level", "track", "seq"], name="unique_rule_item"),
        ]
        ordering = ["system", "level", "track", "seq"]

    def __str__(self):
        return f"{self.system.upper()}{self.level}-{self.get_track_display()}-{self.seq}"
