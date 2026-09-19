from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import (
    can_create_project,
    can_generate_report,
    can_lock_or_unlock_project,
    can_modify_project,
    can_view_project,
)
from apps.audit.models import AuditLog
from apps.evaluations import services
from apps.evaluations.forms import EvaluationProjectForm
from apps.evaluations.models import CheckItem, EvaluationProject
from apps.rules.models import MaturityLevel


def _get_project_or_403(request, project_id):
    project = get_object_or_404(EvaluationProject, pk=project_id)
    if not can_view_project(request.user, project):
        raise PermissionDenied("当前用户不能查看该评价项目。")
    return project


@login_required
def project_list(request):
    projects = EvaluationProject.objects.select_related("created_by").all()
    rows = []
    counters = {"total": 0, "in_progress": 0, "ready_for_report": 0, "reported": 0}
    for project in projects:
        stats = services.get_target_scope_stats(project)
        achieved = services.get_achieved_level(project)
        ready = project.status == EvaluationProject.STATUS_IN_PROGRESS and achieved >= project.target_level
        counters["total"] += 1
        if project.status == EvaluationProject.STATUS_REPORTED:
            counters["reported"] += 1
        elif ready:
            counters["ready_for_report"] += 1
        elif project.status == EvaluationProject.STATUS_IN_PROGRESS:
            counters["in_progress"] += 1
        rows.append({"project": project, "stats": stats, "achieved": achieved, "ready": ready})
    return render(
        request,
        "evaluations/project_list.html",
        {
            "rows": rows,
            "counters": counters,
            "can_create": can_create_project(request.user),
        },
    )


@login_required
def project_create(request):
    if not can_create_project(request.user):
        raise PermissionDenied("当前用户不能新建评价项目。")
    level_defs = {lv.level: lv.name for lv in MaturityLevel.objects.all()}
    if request.method == "POST":
        form = EvaluationProjectForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                project = form.save(commit=False)
                project.created_by = request.user
                project.save()
                count = services.generate_check_items(project)
            messages.success(request, f"评价项目已创建，系统按评价轨道物化了 {count} 条核验细则。")
            return redirect("evaluations:project_detail", project_id=project.id)
    else:
        form = EvaluationProjectForm()
    track_counts = {
        "equipment": 77,
        "hardware": 44,
        "software": 33,
    }
    return render(
        request,
        "evaluations/project_form.html",
        {"form": form, "level_defs": level_defs, "track_counts": track_counts},
    )


@login_required
def project_detail(request, project_id):
    project = _get_project_or_403(request, project_id)
    level_states = services.get_level_states(project)
    stats = services.get_project_stats(project)
    target_stats = services.get_target_scope_stats(project)
    achieved = services.get_achieved_level(project)

    try:
        view_level = int(request.GET.get("level", project.unlocked_level))
    except (TypeError, ValueError):
        view_level = project.unlocked_level
    view_level = max(1, min(9, view_level))
    if view_level > project.unlocked_level:
        view_level = project.unlocked_level

    level_def = MaturityLevel.objects.filter(level=view_level).first()
    items = list(
        project.items.filter(level=view_level)
        .prefetch_related("evidence_files")
        .select_related("evaluated_by")
        .order_by("track", "seq")
    )
    logs_by_item = {}
    for log in AuditLog.objects.filter(
        project=project, model_name="CheckItem", object_id__in=[str(item.id) for item in items]
    ).select_related("actor")[:200]:
        logs_by_item.setdefault(log.object_id, []).append(log)
    for item in items:
        item.history = logs_by_item.get(str(item.id), [])
    hw_items = [item for item in items if item.track == CheckItem.TRACK_HARDWARE]
    sw_items = [item for item in items if item.track == CheckItem.TRACK_SOFTWARE]

    current_state = next(state for state in level_states if state["level"] == view_level)
    mutable = can_modify_project(request.user, project)
    level_passed = current_state["state"] == "passed"
    can_pass_level = (
        mutable
        and not level_passed
        and view_level == project.unlocked_level
        and current_state["total"] > 0
        and current_state["unevaluated"] == 0
    )
    ready_for_report = achieved >= project.target_level

    # 佐证上传后回跳并自动重开条目弹窗
    reopen_item = request.GET.get("item", "")

    return render(
        request,
        "evaluations/project_detail.html",
        {
            "project": project,
            "level_states": level_states,
            "stats": stats,
            "target_stats": target_stats,
            "achieved": achieved,
            "view_level": view_level,
            "level_def": level_def,
            "hw_items": hw_items,
            "sw_items": sw_items,
            "current_state": current_state,
            "mutable": mutable,
            "level_passed": level_passed,
            "can_pass_level": can_pass_level,
            "ready_for_report": ready_for_report,
            "can_lock": can_lock_or_unlock_project(request.user),
            "can_report": can_generate_report(request.user),
            "reopen_item": reopen_item,
        },
    )


@login_required
@require_POST
def item_evaluate(request, project_id, item_id):
    project = _get_project_or_403(request, project_id)
    item = get_object_or_404(CheckItem, pk=item_id, project=project)
    if not can_modify_project(request.user, project):
        messages.error(request, "当前项目不可编辑或您没有判定权限。")
        return redirect(f"{reverse('evaluations:project_detail', args=[project.id])}?level={item.level}")
    try:
        services.evaluate_check_item(item, request.user, request.POST)
        messages.success(request, f"细则 {item.code} 判定结论已保存。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect(f"{reverse('evaluations:project_detail', args=[project.id])}?level={item.level}")


@login_required
@require_POST
def pass_level_view(request, project_id):
    project = _get_project_or_403(request, project_id)
    if not can_modify_project(request.user, project):
        messages.error(request, "当前项目不可编辑或您没有审核权限。")
        return redirect("evaluations:project_detail", project_id=project.id)
    try:
        level = int(request.POST.get("level", project.unlocked_level))
        services.pass_level(project, level, request.user)
        achieved = services.get_achieved_level(project)
        if achieved >= project.target_level:
            messages.success(request, f"TRL {level} 级审核通过。项目已达到目标等级 TRL {project.target_level}，可进入报告编制环节。")
        else:
            messages.success(request, f"TRL {level} 级审核通过，TRL {level + 1} 级核验已解锁。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect("evaluations:project_detail", project_id=project.id)


@login_required
@require_POST
def lock_view(request, project_id):
    project = _get_project_or_403(request, project_id)
    try:
        services.lock_project(project, request.user)
        messages.success(request, "项目已锁定，判定结论与佐证材料进入冻结状态。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "；".join(getattr(exc, "messages", [str(exc)])))
    return redirect("evaluations:project_detail", project_id=project.id)


@login_required
@require_POST
def unlock_view(request, project_id):
    project = _get_project_or_403(request, project_id)
    try:
        services.unlock_project(project, request.user)
        messages.success(request, "项目已解锁，可继续复核修改。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "；".join(getattr(exc, "messages", [str(exc)])))
    return redirect("evaluations:project_detail", project_id=project.id)
