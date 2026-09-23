from django.core.paginator import Paginator
from django.shortcuts import render

from apps.accounts.permissions import admin_required
from apps.audit.models import AuditLog

ACTION_LABELS = {
    "project.create": ("申报项目", "info"),
    "assessment.create": ("开展评价", "info"),
    "item.evaluate": ("自评填写", "neutral"),
    "item.review": ("逐条审核意见", "neutral"),
    "level.submit": ("提交审核", "info"),
    "level.pass": ("审核通过", "success"),
    "level.return": ("退回修改", "warning"),
    "assessment.reopen": ("重新开放", "warning"),
    "evidence.upload": ("佐证上传", "neutral"),
    "evidence.delete": ("佐证删除", "warning"),
    "report.generate": ("生成报告", "brand"),
    "user.register": ("企业注册", "info"),
    "user.approve": ("账号审核通过", "success"),
    "user.reject": ("账号驳回", "warning"),
    "user.disable": ("账号停用", "warning"),
    "user.enable": ("账号启用", "success"),
    "user.reset_password": ("重置密码", "neutral"),
}

FILTERS = [
    ("", "全部操作类型"),
    ("item.", "自评与逐条意见"),
    ("level.", "提交 / 审核 / 退回"),
    ("evidence.", "佐证材料"),
    ("report.", "报告生成"),
    ("user.", "账号管理"),
    ("project.", "项目申报"),
]


@admin_required
def audit_list(request):
    logs = AuditLog.objects.select_related("actor__enterprise", "project").all()
    action = request.GET.get("action", "").strip()
    if action:
        logs = logs.filter(action__startswith=action)
    paginator = Paginator(logs, 30)
    page_obj = paginator.get_page(request.GET.get("page"))
    for log in page_obj:
        log.label, log.tone = ACTION_LABELS.get(log.action, (log.action, "neutral"))
    return render(
        request,
        "audit/audit_list.html",
        {"page_obj": page_obj, "action_query": action, "filters": FILTERS, "active_nav": "audit"},
    )
