"""专家劳务记录：专家提交一轮评审意见后自动生成，管理员修改金额、标记结算、导出。"""

import csv
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import can_review
from apps.audit.models import AuditLog
from apps.notifications.services import notify
from apps.reviews.models import RemunerationRecord
from apps.reviews.services import expert_name
from apps.rules.systems import get_system

EXPORT_HEADERS = ["专家", "工作单位", "技术项目", "评价内容", "轮次", "评审条目", "金额（元）", "状态", "结算时间", "备注", "生成时间"]


def _ensure_admin(actor):
    if not can_review(actor):
        raise PermissionDenied("劳务记录由评价机构管理员维护。")


def _log(actor, action, record, after):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name="RemunerationRecord",
        object_id=str(record.id),
        object_repr=f"{expert_name(record.expert)} · {record.project_name} 第 {record.round_number} 轮",
        project=record.project,
        after=after,
    )


@transaction.atomic
def update_record(record, actor, amount, note=""):
    _ensure_admin(actor)
    if record.is_settled:
        raise ValidationError("已结算的记录不能修改金额。")
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("金额格式不正确。")
    if amount < 0:
        raise ValidationError("金额不能为负数。")
    before = {"amount": str(record.amount), "note": record.note}
    record.amount = amount
    record.note = (note or "").strip()[:255]
    record.save(update_fields=["amount", "note"])
    _log(actor, "fee.update", record, {"before": before, "amount": str(amount), "note": record.note})
    return record


@transaction.atomic
def settle_records(ids, actor):
    _ensure_admin(actor)
    records = list(RemunerationRecord.objects.filter(pk__in=ids, is_settled=False).select_related("expert"))
    if not records:
        raise ValidationError("请选择尚未结算的记录。")
    now = timezone.now()
    for record in records:
        record.is_settled = True
        record.settled_at = now
        record.settled_by = actor
        record.save(update_fields=["is_settled", "settled_at", "settled_by"])
        _log(actor, "fee.settle", record, {"amount": str(record.amount)})
        profile = getattr(record.expert, "expert_profile", None)
        notify(
            record.expert,
            "fee.settle",
            f"劳务已结算：{record.project_name}（第 {record.round_number} 轮），{record.amount} 元",
            link=reverse("experts:expert_detail", args=[profile.pk]) if profile else "",
            project=record.project,
            category="task",
        )
    return len(records)


def _row(record):
    profile = getattr(record.expert, "expert_profile", None)
    return [
        profile.name if profile else record.expert.username,
        profile.organization if profile else "",
        record.project_name,
        f"{get_system(record.system).name}评价",
        record.round_number,
        record.item_count,
        f"{record.amount:.2f}",
        "已结算" if record.is_settled else "未结算",
        timezone.localtime(record.settled_at).strftime("%Y-%m-%d %H:%M") if record.settled_at else "",
        record.note,
        timezone.localtime(record.created_at).strftime("%Y-%m-%d %H:%M"),
    ]


def export_csv(records):
    """UTF-8 带 BOM，Excel 直接打开不乱码。"""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADERS)
    for record in records:
        writer.writerow(_row(record))
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def export_xlsx(records):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "专家劳务"
    sheet.append(EXPORT_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for record in records:
        row = _row(record)
        row[6] = float(record.amount)
        sheet.append(row)
    for column, width in zip("ABCDEFGHIJK", [10, 24, 30, 14, 6, 8, 12, 8, 17, 20, 17]):
        sheet.column_dimensions[column].width = width
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
