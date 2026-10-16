from django.core.paginator import Paginator
from django.shortcuts import render

from apps.accounts.permissions import admin_required
from apps.audit.models import AuditLog

ACTION_LABELS = {
    "project.create": ("申报项目", "info"),
    "project.delete": ("删除项目", "warning"),
    "project.restore": ("恢复项目", "success"),
    "project.purge": ("彻底删除项目", "warning"),
    "config.update": ("修改系统设置", "neutral"),
    "expert.create": ("开通专家账号", "info"),
    "expert.update": ("修改专家档案", "neutral"),
    "review.mode": ("切换评审方式", "neutral"),
    "review.apply": ("申报专家评审", "info"),
    "review.assign": ("分配专家", "info"),
    "review.accept": ("专家接受任务", "success"),
    "review.decline": ("专家拒绝任务", "warning"),
    "review.reassign": ("更换专家", "warning"),
    "review.remind": ("催办专家", "neutral"),
    "review.extend": ("延长评审期限", "neutral"),
    "review.submit": ("专家提交意见", "info"),
    "review.confirm_unanimous": ("按一致意见确认", "neutral"),
    "review.rule": ("逐条终审", "neutral"),
    "review.finalize": ("提交终审结果", "brand"),
    "review.rectify_extend": ("延长整改期限", "neutral"),
    "review.resubmit": ("提交整改", "info"),
    "review.overdue": ("专家评审逾期", "warning"),
    "fee.settle": ("劳务结算", "success"),
    "fee.update": ("修改劳务金额", "neutral"),
    "assessment.create": ("开展评价", "info"),
    "item.evaluate": ("自评填写", "neutral"),
    "item.review": ("逐条审核意见", "neutral"),
    "item.admin_edit": ("管理员修改说明", "warning"),
    "level.submit": ("提交审核", "info"),
    "level.pass": ("审核通过", "success"),
    "level.return": ("退回修改", "warning"),
    "assessment.reopen": ("重新开放", "warning"),
    "evidence.upload": ("佐证上传", "neutral"),
    "evidence.delete": ("佐证删除", "warning"),
    "report.generate": ("生成报告", "brand"),
    "report.request": ("申请报告导出", "info"),
    "report.request_approve": ("导出申请通过", "success"),
    "report.request_reject": ("导出申请驳回", "warning"),
    "target.request": ("申请调整目标等级", "info"),
    "target.approve": ("目标等级调整通过", "success"),
    "target.reject": ("目标等级调整驳回", "warning"),
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
    ("report.", "报告生成与导出申请"),
    ("target.", "目标等级调整"),
    ("user.", "账号管理"),
    ("project.", "项目申报与删除"),
    ("review.", "专家评审"),
    ("expert.", "专家库"),
    ("fee.", "专家劳务"),
    ("config.", "系统设置"),
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
