"""权限口径：只有“管理员（评价机构）”与“企业用户”两类账号。

- 管理员：查看全部项目、审核提交、生成报告、管理细则库 / 用户 / 审计。
- 企业用户：只能看到并操作自己申报的项目；越权访问一律按不存在（404）处理。
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404

ROLE_ADMIN_LABEL = "管理员"
ROLE_ENTERPRISE_LABEL = "企业用户"


def is_admin(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff))


def is_enterprise(user):
    return bool(user and user.is_authenticated and not is_admin(user) and hasattr(user, "enterprise"))


def role_label(user):
    if is_admin(user):
        return ROLE_ADMIN_LABEL
    if is_enterprise(user):
        return ROLE_ENTERPRISE_LABEL
    return ""


def display_name(user):
    """页眉与留痕中显示的名称：企业用户显示企业名，管理员显示账号。"""
    if user is None:
        return "系统"
    if hasattr(user, "enterprise"):
        return user.enterprise.company_name
    return user.get_full_name() or user.username


def visible_projects(user):
    from apps.evaluations.models import EvaluationProject

    projects = EvaluationProject.objects.all()
    if is_admin(user):
        return projects
    if user and user.is_authenticated:
        return projects.filter(owner=user)
    return projects.none()


def can_view_project(user, project):
    return is_admin(user) or bool(user and user.is_authenticated and project.owner_id == user.id)


def get_visible_project_or_404(user, project_id):
    project = visible_projects(user).filter(pk=project_id).first()
    if project is None:
        raise Http404("评价项目不存在。")
    return project


def ensure_can_view_project(user, project):
    if not can_view_project(user, project):
        raise Http404("评价项目不存在。")


def can_create_project(user):
    return is_enterprise(user)


def is_project_owner(user, project):
    return bool(user and user.is_authenticated and project.owner_id == user.id)


def can_review(user):
    return is_admin(user)


def can_generate_report(user):
    return is_admin(user)


def can_edit_project_info(user, project):
    return is_admin(user) or is_project_owner(user, project)


def can_manage_users(user):
    return is_admin(user)


def can_view_audit(user):
    return is_admin(user)


def can_view_rules(user):
    return is_admin(user)


def admin_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not is_admin(request.user):
            raise PermissionDenied("该功能仅对评价机构管理员开放。")
        return view_func(request, *args, **kwargs)

    return wrapper
