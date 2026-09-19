import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.rules.models import MaturityLevel, MrlLevel, RuleItem


class Command(BaseCommand):
    help = "从 data/seed/ 导入 TRL 等级定义、评价细则与 MRL 框架（幂等）"

    @transaction.atomic
    def handle(self, *args, **options):
        seed_dir = Path(settings.BASE_DIR) / "data" / "seed"

        rules_path = seed_dir / "trl_rules_seed.csv"
        level_count = item_count = 0
        with open(rules_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                level = int(row["level"])
                if row["row_type"] == "level":
                    MaturityLevel.objects.update_or_create(level=level, defaults={"name": row["name"]})
                    level_count += 1
                else:
                    RuleItem.objects.update_or_create(
                        level=level,
                        track=row["track"],
                        seq=int(row["seq"]),
                        defaults={"name": row["name"], "evidence": row["evidence"]},
                    )
                    item_count += 1

        mrl_path = seed_dir / "mrl_framework_seed.csv"
        mrl_count = 0
        with open(mrl_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                MrlLevel.objects.update_or_create(
                    level=int(row["level"]),
                    defaults={"name": row["name"], "desc": row["desc"]},
                )
                mrl_count += 1

        self.stdout.write(self.style.SUCCESS(f"导入完成：TRL 等级 {level_count} 个，细则条目 {item_count} 条，MRL 等级 {mrl_count} 个"))
