ROLE_SYSTEM_ADMIN = "SystemAdmin"
ROLE_EVALUATOR = "Evaluator"
ROLE_AUDITOR = "Auditor"
ROLE_VIEWER = "Viewer"

ROLE_LABELS = {
    ROLE_SYSTEM_ADMIN: "系统管理员",
    ROLE_EVALUATOR: "评价员",
    ROLE_AUDITOR: "审核员",
    ROLE_VIEWER: "观察员",
}


def has_group(user, group_name):
    return bool(user and user.is_authenticated and user.groups.filter(name=group_name).exists())


def is_system_admin(user):
    return bool(user and user.is_authenticated and (user.is_superuser or has_group(user, ROLE_SYSTEM_ADMIN)))


def is_evaluator(user):
    return has_group(user, ROLE_EVALUATOR)


def is_auditor(user):
    return has_group(user, ROLE_AUDITOR)


def is_viewer(user):
    return has_group(user, ROLE_VIEWER)


def can_create_project(user):
    return is_system_admin(user) or is_evaluator(user)


def can_view_project(user, project):
    if not user or not user.is_authenticated:
        return False
    if is_system_admin(user) or is_auditor(user) or is_viewer(user):
        return True
    if is_evaluator(user):
        return project.created_by_id == user.id or project.items.filter(evaluated_by_id=user.id).exists()
    return False


def can_modify_project(user, project):
    if project.status in {"locked", "reported", "archived"}:
        return False
    return can_modify_project_when_mutable(user, project)


def can_modify_project_when_mutable(user, project):
    if is_system_admin(user):
        return True
    if is_evaluator(user):
        return project.created_by_id == user.id
    return False


def can_lock_or_unlock_project(user):
    return is_system_admin(user) or is_auditor(user)


def can_generate_report(user):
    return is_system_admin(user) or is_auditor(user)


def can_view_audit(user):
    return is_system_admin(user) or is_auditor(user)
