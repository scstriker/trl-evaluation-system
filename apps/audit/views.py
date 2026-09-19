from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render

from apps.accounts.permissions import can_view_audit
from apps.audit.models import AuditLog


@login_required
def audit_list(request):
    if not can_view_audit(request.user):
        raise PermissionDenied("当前用户不能查看审计日志。")
    logs = AuditLog.objects.select_related("actor", "project").all()
    action = request.GET.get("action", "").strip()
    if action:
        logs = logs.filter(action__icontains=action)
    paginator = Paginator(logs, 30)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "audit/audit_list.html", {"page_obj": page_obj, "action_query": action})
