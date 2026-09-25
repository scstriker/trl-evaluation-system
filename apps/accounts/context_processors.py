from apps.accounts.permissions import display_name, is_admin, role_label

SYSTEM_NAME = "技术就绪度与制造成熟度评价系统"
ISSUER_NAME = "机械工业仪器仪表综合技术经济研究所"


def current_role(request):
    user = getattr(request, "user", None)
    context = {
        "system_name": SYSTEM_NAME,
        "issuer_name": ISSUER_NAME,
        "current_role_label": role_label(user),
        "current_is_admin": is_admin(user),
        "current_display_name": display_name(user) if user and user.is_authenticated else "",
        "pending_users": 0,
        "pending_reviews": {},
        "pending_report_requests": 0,
        "mrl_enabled": False,
    }
    if user and user.is_authenticated:
        from apps.evaluations.services import mrl_enabled

        context["mrl_enabled"] = mrl_enabled()
    if is_admin(user):
        from apps.accounts.models import EnterpriseProfile
        from apps.evaluations.models import LevelReview, TargetChangeRequest
        from apps.reports.models import ReportRequest

        context["pending_users"] = EnterpriseProfile.objects.filter(status=EnterpriseProfile.STATUS_PENDING).count()
        # 待办 = 有已提交级别的评价 + 有待审核目标调整申请的评价（按评价计数，不重复）
        pending = {}
        for system, assessment_id in LevelReview.objects.filter(status=LevelReview.STATUS_SUBMITTED).values_list(
            "assessment__system", "assessment_id"
        ):
            pending.setdefault(system, set()).add(assessment_id)
        for system, assessment_id in TargetChangeRequest.objects.filter(
            status=TargetChangeRequest.STATUS_PENDING
        ).values_list("assessment__system", "assessment_id"):
            pending.setdefault(system, set()).add(assessment_id)
        context["pending_reviews"] = {system: len(ids) for system, ids in pending.items()}
        context["pending_report_requests"] = ReportRequest.objects.filter(status=ReportRequest.STATUS_PENDING).count()
    return context
