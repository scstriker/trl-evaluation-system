from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.accounts.permissions import admin_required
from apps.rules.models import LevelDefinition, RuleItem
from apps.rules.systems import MRL, TRL, get_system


@admin_required
def rule_library(request, system=TRL):
    """评价细则库（仅管理员）：按等级展示全部细则条目。"""
    sys = get_system(system)
    items = list(RuleItem.objects.filter(system=system))
    levels = []
    for definition in LevelDefinition.objects.filter(system=system):
        level_items = [item for item in items if item.level == definition.level]
        levels.append(
            {
                "definition": definition,
                "items": level_items,
                "hw_count": sum(1 for item in level_items if item.track == RuleItem.TRACK_HARDWARE),
                "sw_count": sum(1 for item in level_items if item.track == RuleItem.TRACK_SOFTWARE),
            }
        )
    return render(
        request,
        "rules/rule_library.html",
        {
            "sys": sys,
            "levels": levels,
            "total_items": len(items),
            "hw_total": sum(1 for item in items if item.track == RuleItem.TRACK_HARDWARE),
            "sw_total": sum(1 for item in items if item.track == RuleItem.TRACK_SOFTWARE),
            "has_tracks": any(item.track != RuleItem.TRACK_GENERAL for item in items),
            "active_nav": "process",
            "rule_tab": system,
        },
    )


@login_required
def process_overview(request):
    """评价流程：企业与评价机构双方的办理流程，以及两类评价的等级定义。"""
    return render(
        request,
        "rules/process_overview.html",
        {
            "trl_defs": LevelDefinition.objects.filter(system=TRL),
            "mrl_defs": LevelDefinition.objects.filter(system=MRL),
            "trl_rule_count": RuleItem.objects.filter(system=TRL).count(),
            "mrl_rule_count": RuleItem.objects.filter(system=MRL).count(),
            "active_nav": "process",
            "rule_tab": "process",
        },
    )
