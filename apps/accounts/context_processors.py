from apps.accounts.permissions import ROLE_LABELS, can_view_audit


def current_role(request):
    user = getattr(request, "user", None)
    role_label = ""
    if user and user.is_authenticated:
        if user.is_superuser:
            role_label = ROLE_LABELS["SystemAdmin"]
        else:
            group = user.groups.order_by("name").first()
            role_label = ROLE_LABELS.get(group.name, group.name) if group else ""
    return {"current_role_label": role_label, "current_can_view_audit": can_view_audit(user)}
