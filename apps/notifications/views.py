from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.notifications import services
from apps.notifications.models import Notification


@login_required
def inbox(request):
    show = request.GET.get("show", "")
    notifications = Notification.objects.filter(recipient=request.user).select_related("project")
    unread = notifications.filter(read_at__isnull=True).count()
    if show == "unread":
        notifications = notifications.filter(read_at__isnull=True)
    page_obj = Paginator(notifications, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "notifications/inbox.html",
        {
            "page_obj": page_obj,
            "show": show,
            "unread": unread,
            "total": Notification.objects.filter(recipient=request.user).count(),
            "active_nav": "inbox",
        },
    )


@login_required
@require_POST
def open_notification(request, notification_id):
    notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
    services.mark_read(notification)
    link = notification.link
    # 只跳转站内相对链接
    if link and link.startswith("/") and url_has_allowed_host_and_scheme(link, allowed_hosts={request.get_host()}):
        return redirect(link)
    return redirect("notifications:inbox")


@login_required
@require_POST
def read_all(request):
    services.mark_all_read(request.user)
    return redirect("notifications:inbox")
