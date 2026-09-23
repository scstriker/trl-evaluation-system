from django.db.models import Count

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
    }
    if is_admin(user):
        from apps.accounts.models import EnterpriseProfile
        from apps.evaluations.models import LevelReview

        context["pending_users"] = EnterpriseProfile.objects.filter(status=EnterpriseProfile.STATUS_PENDING).count()
        rows = (
            LevelReview.objects.filter(status=LevelReview.STATUS_SUBMITTED)
            .values("assessment__system")
            .annotate(n=Count("assessment", distinct=True))
        )
        context["pending_reviews"] = {row["assessment__system"]: row["n"] for row in rows}
    return context
