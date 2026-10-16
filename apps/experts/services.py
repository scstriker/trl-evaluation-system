"""专家库：开通专家账号、维护档案、Excel 批量导入。"""

import re

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse

from apps.accounts.permissions import is_admin
from apps.audit.models import AuditLog
from apps.experts.models import MAX_TAGS, ExpertProfile
from apps.notifications.models import CATEGORY_TASK
from apps.notifications.services import notify
from apps.rules.models import Specialty

INITIAL_PASSWORD = "Trl@2026"
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,19}$")

PROFILE_FIELDS = [
    "name",
    "organization",
    "title",
    "position",
    "in_service",
    "specialty",
    "tags",
    "phone",
    "email",
    "bio",
    "research",
    "achievements",
    "experience",
    "website",
]

# Excel 表头 → 字段。表头沿用传感器专项专家库，另加主页字段
IMPORT_COLUMNS = {
    "姓名": "name",
    "工作单位": "organization",
    "职称": "title",
    "职务": "position",
    "是否在职": "in_service",
    "专业领域": "specialty",
    "专家标识": "tags",
    "专家标签1": "tag1",
    "专家标签2": "tag2",
    "专家标签3": "tag3",
    "手机": "phone",
    "联系方式": "phone",
    "邮箱": "email",
    "简介": "bio",
    "研究方向": "research",
    "代表成果": "achievements",
    "教育与任职经历": "experience",
    "个人网站": "website",
    "账号": "username",
}


def mask_phone(phone):
    """列表中隐藏手机号中间四位。"""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11:
        return f"{digits[:3]}****{digits[-4:]}"
    return phone


def split_tags(text):
    tags = [tag.strip() for tag in re.split(r"[,，;；、\s]+", text or "") if tag.strip()]
    if len(tags) > MAX_TAGS:
        raise ValidationError(f"专家标识最多 {MAX_TAGS} 个。")
    return tags


def _ensure_admin(actor):
    if not is_admin(actor):
        raise PermissionDenied("专家库由评价机构管理员维护。")


def _log(actor, action, profile, after=None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name="ExpertProfile",
        object_id=str(profile.pk),
        object_repr=f"{profile.name}（{profile.organization}）",
        after=after,
    )


def next_username():
    """样例与导入专家的账号按 expert01、expert02…顺延。"""
    numbers = [
        int(match.group(1))
        for match in (re.fullmatch(r"expert(\d+)", name) for name in User.objects.values_list("username", flat=True))
        if match
    ]
    return f"expert{(max(numbers, default=0) + 1):02d}"


def _validate_username(username):
    if not USERNAME_RE.match(username or ""):
        raise ValidationError("登录账号须为 4～20 位字母、数字或下划线，并以字母开头。")
    if User.objects.filter(username=username).exists():
        raise ValidationError(f"登录账号 {username} 已被使用。")


@transaction.atomic
def create_expert(data, actor, username="", password=INITIAL_PASSWORD, is_sample=False):
    """开通专家账号并建立档案。data 为档案字段字典（specialty 为 Specialty 实例）。"""
    _ensure_admin(actor)
    username = (username or "").strip() or next_username()
    _validate_username(username)
    user = User.objects.create_user(username=username, password=password, first_name=data["name"])
    profile = ExpertProfile(user=user, is_sample=is_sample, **{key: data[key] for key in PROFILE_FIELDS if key in data})
    profile.full_clean(exclude=["user"])
    profile.save()
    _log(actor, "expert.create", profile, after={"username": username, "specialty": profile.specialty.name})
    notify(
        user,
        "expert.create",
        "评审专家账号已开通，请核对个人档案",
        body=f"评价机构已为你开通评审专家账号（{username}）。收到评审任务时会在这里通知你。",
        link=reverse("experts:expert_detail", args=[profile.pk]),
        category=CATEGORY_TASK,
    )
    return profile


@transaction.atomic
def update_expert(profile, data, actor, is_active=None):
    _ensure_admin(actor)
    before = {key: str(getattr(profile, key)) for key in PROFILE_FIELDS}
    for key in PROFILE_FIELDS:
        if key in data:
            setattr(profile, key, data[key])
    profile.full_clean(exclude=["user"])
    profile.save()
    user_fields = []
    if profile.user.first_name != profile.name:
        profile.user.first_name = profile.name
        user_fields.append("first_name")
    if is_active is not None and profile.user.is_active != is_active:
        profile.user.is_active = is_active
        user_fields.append("is_active")
    if user_fields:
        profile.user.save(update_fields=user_fields)
    after = {key: str(getattr(profile, key)) for key in PROFILE_FIELDS}
    changed = {key: after[key] for key in after if after[key] != before[key]}
    if "is_active" in user_fields:
        changed["is_active"] = is_active
    if changed:
        _log(actor, "expert.update", profile, after=changed)
    return profile


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _row_to_data(row):
    specialty_name = row.get("specialty", "")
    specialty = Specialty.objects.filter(name=specialty_name).first()
    if specialty is None:
        names = "、".join(Specialty.objects.values_list("name", flat=True))
        raise ValidationError(f"专业领域“{specialty_name}”不在技术领域词表中（可选：{names}）")
    if not row.get("name") or not row.get("organization"):
        raise ValidationError("姓名和工作单位为必填项")
    tags = split_tags(row.get("tags", "")) if row.get("tags") else [row[key] for key in ("tag1", "tag2", "tag3") if row.get(key)]
    data = {key: row.get(key, "") for key in PROFILE_FIELDS if key not in ("specialty", "tags", "in_service")}
    data.update(specialty=specialty, tags=tags[:MAX_TAGS], in_service=row.get("in_service", "是") not in ("否", "离职", "退休", "0"))
    return data


def import_experts(file, actor):
    """从 Excel 导入专家：按“姓名 + 工作单位”匹配，已有的更新档案，新的开通账号。

    返回 (新增数, 更新数, 错误列表)。单行出错不影响其他行。
    """
    _ensure_admin(actor)
    from openpyxl import load_workbook

    try:
        sheet = load_workbook(file, read_only=True, data_only=True).active
    except Exception as exc:  # noqa: BLE001  任何解析失败都按格式错误提示
        raise ValidationError(f"无法读取 Excel 文件：{exc}") from exc
    rows = sheet.iter_rows(values_only=True)
    headers = [IMPORT_COLUMNS.get(_cell(value)) for value in next(rows, [])]
    if "name" not in headers or "organization" not in headers:
        raise ValidationError("表头须包含“姓名”和“工作单位”两列。")
    created, updated, errors = 0, 0, []
    for line, values in enumerate(rows, start=2):
        row = {key: _cell(value) for key, value in zip(headers, values) if key}
        if not any(row.values()):
            continue
        try:
            with transaction.atomic():
                data = _row_to_data(row)
                existing = ExpertProfile.objects.filter(name=data["name"], organization=data["organization"]).first()
                if existing:
                    update_expert(existing, data, actor)
                    updated += 1
                else:
                    create_expert(data, actor, username=row.get("username", ""))
                    created += 1
        except ValidationError as exc:
            errors.append(f"第 {line} 行：{'；'.join(exc.messages)}")
    return created, updated, errors
