"""从 Excel 批量导入评审专家：python manage.py import_experts 专家名单.xlsx

表头与专家库页面“Excel 导入”一致；操作人记为管理员账号 admin（可用 --actor 指定）。
"""

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.experts.services import import_experts


class Command(BaseCommand):
    help = "从 Excel 批量导入评审专家（按 姓名 + 工作单位 匹配，已有的更新，新的开通账号）"

    def add_arguments(self, parser):
        parser.add_argument("xlsx", help="专家名单 .xlsx 文件路径")
        parser.add_argument("--actor", default="admin", help="记入审计日志的管理员账号，默认 admin")

    def handle(self, *args, **options):
        actor = User.objects.filter(username=options["actor"], is_staff=True).first()
        if actor is None:
            raise CommandError(f"管理员账号 {options['actor']} 不存在。")
        try:
            with open(options["xlsx"], "rb") as file:
                created, updated, errors, new_passwords = import_experts(file, actor)
        except (OSError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"新增 {created} 位，更新 {updated} 位"))
        for username, password in new_passwords:
            self.stdout.write(f"  {username}  初始密码 {password}")
        for line in errors:
            self.stdout.write(self.style.WARNING(line))
