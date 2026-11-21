from apps.accounts.permissions import display_name, is_admin, is_expert, role_label

SYSTEM_NAME = "技术就绪度与制造成熟度评价系统"
ISSUER_NAME = "机械工业仪器仪表综合技术经济研究所"


def current_role(request):
    user = getattr(request, "user", None)
    context = {
        "system_name": SYSTEM_NAME,
        "issuer_name": ISSUER_NAME,
        "current_role_label": role_label(user),
        "current_is_admin": is_admin(user),
        "current_is_expert": is_expert(user),
        "current_display_name": display_name(user) if user and user.is_authenticated else "",
        "pending_users": 0,
        "pending_reviews": {},
        "pending_report_requests": 0,
        "mrl_enabled": False,
        "unread_notifications": 0,
    }
    if user and user.is_authenticated:
        from apps.evaluations.services import mrl_enabled
        from apps.notifications.services import unread_count

        context["mrl_enabled"] = mrl_enabled()
        context["unread_notifications"] = unread_count(user)
    if is_expert(user):
        from apps.reviews.models import ExpertAssignment

        context["expert_open_tasks"] = ExpertAssignment.objects.filter(
            expert=user,
            status__in=ExpertAssignment.PENDING_STATUSES,
            round__assessment__project__deleted_at__isnull=True,
        ).count()
    if is_admin(user):
        from apps.accounts.models import EnterpriseProfile
        from apps.evaluations.models import LevelReview, TargetChangeRequest
        from apps.reports.models import ReportRequest

        context["pending_users"] = EnterpriseProfile.objects.filter(status=EnterpriseProfile.STATUS_PENDING).count()
        # 待办 = 有已提交级别的评价 + 有待审核目标调整申请的评价（按评价计数，不重复；回收站中的项目不计）
        pending = {}
        for system, assessment_id in LevelReview.objects.filter(
            status=LevelReview.STATUS_SUBMITTED, assessment__project__deleted_at__isnull=True
        ).values_list("assessment__system", "assessment_id"):
            pending.setdefault(system, set()).add(assessment_id)
        for system, assessment_id in TargetChangeRequest.objects.filter(
            status=TargetChangeRequest.STATUS_PENDING, assessment__project__deleted_at__isnull=True
        ).values_list("assessment__system", "assessment_id"):
            pending.setdefault(system, set()).add(assessment_id)
        context["pending_reviews"] = {system: len(ids) for system, ids in pending.items()}
        context["pending_report_requests"] = ReportRequest.objects.filter(
            status=ReportRequest.STATUS_PENDING, project__deleted_at__isnull=True
        ).count()
        context["review_todo"] = _review_todo()
    return context


def _review_todo():
    """专家评审待办：待分配专家、待终审的轮次，以及有专家逾期的轮次（按轮次计数）。"""
    from django.utils import timezone

    from apps.reviews.models import ExpertAssignment, ReviewRound

    alive = {"assessment__project__deleted_at__isnull": True}
    ids = set(
        ReviewRound.objects.filter(
            status__in=[ReviewRound.STATUS_ASSIGNING, ReviewRound.STATUS_RULING], **alive
        ).values_list("id", flat=True)
    )
    ids |= set(
        ExpertAssignment.objects.filter(
            status__in=ExpertAssignment.PENDING_STATUSES,
            due_at__lt=timezone.now(),
            round__assessment__project__deleted_at__isnull=True,
        ).values_list("round_id", flat=True)
    )
    return len(ids)
