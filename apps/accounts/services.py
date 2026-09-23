"""企业账号的注册与状态管理（账号状态与 User.is_active 同步，停用后已有会话立即失效）。"""

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import EnterpriseProfile
from apps.accounts.permissions import can_manage_users
from apps.audit.models import AuditLog


def _log(actor, action, profile, before=None, after=None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name="EnterpriseProfile",
        object_id=str(profile.pk),
        object_repr=f"{profile.company_name}（{profile.user.username}）",
        before=before,
        after=after,
    )


@transaction.atomic
def register_enterprise(data):
    user = User.objects.create_user(username=data["username"], password=data["password1"], is_active=False)
    user.first_name = data["contact_name"]
    user.save(update_fields=["first_name"])
    profile = EnterpriseProfile.objects.create(
        user=user,
        company_name=data["company_name"],
        credit_code=data["credit_code"],
        contact_name=data["contact_name"],
        phone=data["phone"],
    )
    _log(user, "user.register", profile, after={"status": profile.status})
    return profile


ALLOWED_TRANSITIONS = {
    "approve": ({EnterpriseProfile.STATUS_PENDING, EnterpriseProfile.STATUS_REJECTED}, EnterpriseProfile.STATUS_ACTIVE),
    "reject": ({EnterpriseProfile.STATUS_PENDING}, EnterpriseProfile.STATUS_REJECTED),
    "disable": ({EnterpriseProfile.STATUS_ACTIVE}, EnterpriseProfile.STATUS_DISABLED),
    "enable": ({EnterpriseProfile.STATUS_DISABLED}, EnterpriseProfile.STATUS_ACTIVE),
}

ACTION_LABELS = {"approve": "审核通过", "reject": "驳回", "disable": "停用", "enable": "启用"}


@transaction.atomic
def change_status(profile, actor, action, reason=""):
    if not can_manage_users(actor):
        raise PermissionDenied("只有管理员可以管理企业账号。")
    if action not in ALLOWED_TRANSITIONS:
        raise ValidationError("未知操作。")
    allowed_from, target = ALLOWED_TRANSITIONS[action]
    if profile.status not in allowed_from:
        raise ValidationError(f"当前状态为「{profile.get_status_display()}」，不能执行「{ACTION_LABELS[action]}」。")
    reason = (reason or "").strip()
    if action == "reject" and not reason:
        raise ValidationError("驳回时必须填写原因，该原因会在企业登录时显示。")

    before = {"status": profile.status}
    profile.status = target
    profile.reject_reason = reason if action == "reject" else ""
    profile.reviewed_by = actor
    profile.reviewed_at = timezone.now()
    profile.save(update_fields=["status", "reject_reason", "reviewed_by", "reviewed_at"])
    profile.user.is_active = target == EnterpriseProfile.STATUS_ACTIVE
    profile.user.save(update_fields=["is_active"])
    _log(actor, f"user.{action}", profile, before, {"status": target, "reason": profile.reject_reason})
    return profile


@transaction.atomic
def reset_password(profile, actor, new_password):
    if not can_manage_users(actor):
        raise PermissionDenied("只有管理员可以重置密码。")
    profile.user.set_password(new_password)
    profile.user.save(update_fields=["password"])
    _log(actor, "user.reset_password", profile)
    return profile
