"""企业账号的注册与状态管理（账号状态与 User.is_active 同步，停用后已有会话立即失效）。"""

import uuid

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import EnterpriseProfile
from apps.accounts.permissions import can_manage_users
from apps.audit.models import AuditLog
from apps.notifications.models import CATEGORY_TODO
from apps.notifications.services import notify, notify_admins


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
    notify_admins(
        "user.register",
        f"新企业注册待审核：{profile.company_name}",
        body=f"联系人：{profile.contact_name}（{profile.phone}）",
        link=f"{reverse('accounts:user_list')}?status={EnterpriseProfile.STATUS_PENDING}",
        category=CATEGORY_TODO,
    )
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
    if target == EnterpriseProfile.STATUS_ACTIVE:
        notify(
            profile.user,
            f"user.{action}",
            "账号已启用",
            body=f"「{profile.company_name}」的账号已由评价机构审核启用，可以申报评价项目。",
            link=reverse("evaluations:trl_list"),
        )
    return profile


@transaction.atomic
def reset_password(profile, actor, new_password):
    if not can_manage_users(actor):
        raise PermissionDenied("只有管理员可以重置密码。")
    profile.user.set_password(new_password)
    profile.user.save(update_fields=["password"])
    _log(actor, "user.reset_password", profile)
    return profile


@transaction.atomic
def save_system_config(form, actor, seal_upload=None, clear_seal=False):
    """保存系统设置；印章图片只接受 PNG，替换或移除时删除旧文件。"""
    config = form.instance
    before = {
        "expert_review_days": config.expert_review_days,
        "rectify_days": config.rectify_days,
        "expert_fee_default": str(config.expert_fee_default),
        "seal": bool(config.seal_image),
    }
    config = form.save(commit=False)
    old_seal = config.seal_image.name if config.seal_image else ""
    if seal_upload:
        config.seal_image.save(f"seal_{uuid.uuid4().hex[:8]}.png", seal_upload, save=False)
    elif clear_seal:
        config.seal_image = ""
    config.updated_by = actor
    config.save()
    if old_seal and old_seal != (config.seal_image.name if config.seal_image else ""):
        transaction.on_commit(lambda: default_storage.delete(old_seal))
    AuditLog.objects.create(
        actor=actor,
        action="config.update",
        model_name="SystemConfig",
        object_id=str(config.pk),
        object_repr="系统设置",
        before=before,
        after={
            "expert_review_days": config.expert_review_days,
            "rectify_days": config.rectify_days,
            "expert_fee_default": str(config.expert_fee_default),
            "seal": bool(config.seal_image),
        },
    )
    return config
