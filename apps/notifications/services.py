"""站内私信：在业务服务函数里与审计留痕同一处调用，和业务数据处于同一事务。

发给企业的消息不得包含专家姓名或专家原话（单盲）。
"""

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.notifications.models import CATEGORY_RESULT, Notification, SentReminder


def admin_users():
    return User.objects.filter(Q(is_staff=True) | Q(is_superuser=True), is_active=True)


def notify(recipients, kind, title, body="", link="", project=None, category=CATEGORY_RESULT, email=False):
    """给一个或多个用户发站内私信。recipients 可以是单个用户、用户列表或 QuerySet；None 会被跳过。"""
    if recipients is None:
        return []
    if isinstance(recipients, User):
        recipients = [recipients]
    seen, rows = set(), []
    for user in recipients:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        rows.append(
            Notification(
                recipient=user,
                kind=kind,
                category=category,
                title=title[:255],
                body=body,
                link=link,
                project=project,
                email_pending=email,
            )
        )
    return Notification.objects.bulk_create(rows)


def notify_admins(kind, title, body="", link="", project=None, category=CATEGORY_RESULT, email=False):
    return notify(list(admin_users()), kind, title, body, link, project, category, email)


def unread_count(user):
    if not user or not user.is_authenticated:
        return 0
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


def mark_read(notification):
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return notification


def mark_all_read(user):
    return Notification.objects.filter(recipient=user, read_at__isnull=True).update(read_at=timezone.now())


def remind_once(key, kind):
    """自动提醒去重：首次登记成功返回 True，已提醒过返回 False。须在调用方的事务内使用。"""
    try:
        with transaction.atomic():
            SentReminder.objects.create(key=key[:200], kind=kind)
    except IntegrityError:
        return False
    return True
