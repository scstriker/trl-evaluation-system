"""执行一次期限检查：python manage.py check_deadlines"""

from django.core.management.base import BaseCommand

from apps.reviews.deadlines import check_deadlines


class Command(BaseCommand):
    help = "检查专家评审逾期、整改即将到期和整改逾期，按需发送站内私信（同一事项不重复提醒）"

    def handle(self, *args, **options):
        sent = check_deadlines()
        self.stdout.write(
            self.style.SUCCESS(
                f"专家逾期提醒 {sent['assign_overdue']} 条，整改即将到期提醒 {sent['rectify_soon']} 条，"
                f"整改逾期提醒 {sent['rectify_overdue']} 条"
            )
        )
