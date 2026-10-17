"""期限检查：专家评审逾期、整改即将到期、整改逾期。

是否逾期按截止时间实时计算；提醒用 SentReminder 去重，同一事项、同一截止时间只提醒一次，
延期后截止时间变了会再提醒。可以反复执行：用户访问页面时顺带检查（每 10 分钟最多一次），
也可以用 python manage.py check_deadlines 手动执行，exe 启动时也会执行一次。
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.notifications.models import CATEGORY_ALERT, CATEGORY_TODO
from apps.notifications.services import notify, notify_admins, remind_once
from apps.reviews.models import ExpertAssignment, ReviewRound
from apps.reviews.services import enterprise_link, expert_name, round_link

RECTIFY_SOON = timedelta(days=2)


def _stamp(dt):
    return timezone.localtime(dt).strftime("%Y%m%d%H%M")


@transaction.atomic
def check_deadlines(now=None):
    now = now or timezone.now()
    sent = {"assign_overdue": 0, "rectify_soon": 0, "rectify_overdue": 0}

    overdue = ExpertAssignment.objects.filter(
        status__in=ExpertAssignment.PENDING_STATUSES,
        due_at__lt=now,
        round__assessment__project__deleted_at__isnull=True,
    ).select_related("round__assessment__project", "expert__expert_profile")
    for assignment in overdue:
        if not remind_once(f"assign_overdue:{assignment.id}:{_stamp(assignment.due_at)}", "review.overdue"):
            continue
        review_round = assignment.round
        project = review_round.assessment.project
        days = max(1, (now - assignment.due_at).days)
        state = "尚未接受任务" if assignment.status == ExpertAssignment.STATUS_INVITED else "尚未提交意见"
        notify_admins(
            "review.overdue",
            f"{expert_name(assignment.expert)}评审「{project.name}」已逾期 {days} 天",
            body=f"{review_round.assessment.sys.name}第 {review_round.number} 轮，席位 {assignment.slot}，{state}。"
            "可在评审管理中催办、延期或更换专家。",
            link=round_link(review_round),
            project=project,
            category=CATEGORY_ALERT,
        )
        AuditLog.objects.create(
            actor=None,
            action="review.overdue",
            model_name="ExpertAssignment",
            object_id=str(assignment.id),
            object_repr=f"席位 {assignment.slot} · 第 {review_round.number} 轮",
            project=project,
            after={"due_at": assignment.due_at.isoformat(), "days": days},
        )
        sent["assign_overdue"] += 1

    rectifying = ReviewRound.objects.filter(
        status=ReviewRound.STATUS_RECTIFYING,
        rectify_due_at__isnull=False,
        assessment__project__deleted_at__isnull=True,
    ).select_related("assessment__project")
    for review_round in rectifying:
        assessment = review_round.assessment
        project = assessment.project
        due = review_round.rectify_due_at
        local_due = timezone.localtime(due)
        if due < now:
            if remind_once(f"rectify_overdue:{review_round.id}:{_stamp(due)}", "review.rectify_overdue"):
                notify_admins(
                    "review.rectify_overdue",
                    f"「{project.name}」整改已逾期（截止 {local_due:%Y-%m-%d}）",
                    body=f"{project.applicant}尚未提交第 {review_round.number} 轮整改，可延长整改期限。",
                    link=round_link(review_round),
                    project=project,
                    category=CATEGORY_ALERT,
                )
                notify(
                    project.owner,
                    "review.rectify_overdue",
                    f"{assessment.sys.name}专家评审整改已逾期（截止 {local_due:%Y-%m-%d}）",
                    body=f"项目：{project.name}。请尽快完成整改并提交，如需延期请联系评价机构。",
                    link=enterprise_link(assessment),
                    project=project,
                    category=CATEGORY_ALERT,
                )
                sent["rectify_overdue"] += 1
        elif due - now <= RECTIFY_SOON:
            if remind_once(f"rectify_soon:{review_round.id}:{_stamp(due)}", "review.rectify_soon"):
                notify(
                    project.owner,
                    "review.rectify_soon",
                    f"{assessment.sys.name}专家评审整改将于 {local_due:%m-%d %H:%M} 到期",
                    body=f"项目：{project.name}。请在截止前修改未通过的条目，并重新提交审核。",
                    link=enterprise_link(assessment),
                    project=project,
                    category=CATEGORY_TODO,
                )
                sent["rectify_soon"] += 1
    return sent
