import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.rules.models import LevelDefinition, RuleItem
from apps.rules.systems import MRL, TRL, get_system

SEED_FILES = {
    TRL: "trl_rules_seed.csv",
    MRL: "mrl_rules_seed.csv",
}


class Command(BaseCommand):
    help = "从 data/seed/ 导入技术就绪度与制造成熟度的等级定义和评价细则（幂等，可重复执行）"

    @transaction.atomic
    def handle(self, *args, **options):
        seed_dir = Path(settings.BASE_DIR) / "data" / "seed"
        for system, filename in SEED_FILES.items():
            sys = get_system(system)
            path = seed_dir / filename
            if not path.exists():
                self.stdout.write(self.style.WARNING(f"未找到 {filename}，跳过{sys.name}。"))
                continue
            level_count = item_count = 0
            with open(path, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    level = int(row["level"])
                    if row["row_type"] == "level":
                        LevelDefinition.objects.update_or_create(
                            system=system,
                            level=level,
                            defaults={"name": row["name"], "desc": row.get("desc") or ""},
                        )
                        level_count += 1
                    else:
                        RuleItem.objects.update_or_create(
                            system=system,
                            level=level,
                            track=row["track"] or RuleItem.TRACK_GENERAL,
                            seq=int(row["seq"]),
                            defaults={"name": row["name"], "evidence": row["evidence"]},
                        )
                        item_count += 1
            message = f"{sys.name}：等级定义 {level_count} 个，评价细则 {item_count} 条"
            if item_count:
                self.stdout.write(self.style.SUCCESS(message))
            else:
                self.stdout.write(self.style.WARNING(f"{message}（细则尚未导入，暂不能开展{sys.name}评价）"))
