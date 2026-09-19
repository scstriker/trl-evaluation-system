from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.rules.models import MaturityLevel, MrlLevel, RuleItem


@login_required
def rule_library(request):
    """评价细则库：在 UI 中完整展示 TRL 1~9 全部等级定义与细则条目。"""
    levels = []
    items = list(RuleItem.objects.all())
    for level in MaturityLevel.objects.all():
        hw_items = [item for item in items if item.level == level.level and item.track == RuleItem.TRACK_HARDWARE]
        sw_items = [item for item in items if item.level == level.level and item.track == RuleItem.TRACK_SOFTWARE]
        levels.append(
            {
                "level": level,
                "hw_items": hw_items,
                "sw_items": sw_items,
                "item_count": len(hw_items) + len(sw_items),
            }
        )
    return render(
        request,
        "rules/rule_library.html",
        {
            "levels": levels,
            "total_items": len(items),
            "hw_total": sum(1 for item in items if item.track == RuleItem.TRACK_HARDWARE),
            "sw_total": sum(1 for item in items if item.track == RuleItem.TRACK_SOFTWARE),
        },
    )


@login_required
def process_overview(request):
    """评价流程总览：静态流程展示页。"""
    return render(request, "rules/process_overview.html")


@login_required
def mrl_framework(request):
    """制造成熟度（MRL）评级框架展示页（预留扩展模块）。"""
    return render(request, "rules/mrl_framework.html", {"mrl_levels": MrlLevel.objects.all()})
