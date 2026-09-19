from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.accounts.permissions import ROLE_AUDITOR, ROLE_EVALUATOR, ROLE_SYSTEM_ADMIN, ROLE_VIEWER


class Command(BaseCommand):
    help = "初始化系统角色组（幂等）"

    def handle(self, *args, **options):
        for role in [ROLE_SYSTEM_ADMIN, ROLE_EVALUATOR, ROLE_AUDITOR, ROLE_VIEWER]:
            group, created = Group.objects.get_or_create(name=role)
            self.stdout.write(f"{'创建' if created else '已存在'}：{group.name}")
