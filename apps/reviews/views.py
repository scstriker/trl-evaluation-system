from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.models import SystemConfig
from apps.accounts.permissions import admin_required, get_visible_project_or_404, is_admin, is_expert, is_project_owner
from apps.evaluations.models import Assessment
from apps.evaluations.services import system_enabled
from apps.evidence.models import EvidenceFile
from apps.reviews import services
from apps.reviews.models import ExpertAssignment, ReviewRound, RoundItem
from apps.rules.models import Specialty


def _errors(exc):
    return "；".join(getattr(exc, "messages", [str(exc)]))


def _assessment_url(assessment, anchor="expert-panel"):
    url = reverse("evaluations:assessment_detail", args=[assessment.project_id, assessment.system])
    return f"{url}#{anchor}" if anchor else url


def _get_assessment_or_404(request, assessment_id):
    assessment = get_object_or_404(Assessment.objects.select_related("project"), pk=assessment_id)
    if not system_enabled(assessment.system):
        raise Http404("该评价尚未开放。")
    get_visible_project_or_404(request.user, assessment.project_id)
    return assessment


def _get_round_or_404(round_id):
    review_round = get_object_or_404(ReviewRound.objects.select_related("assessment__project"), pk=round_id)
    if review_round.assessment.project.is_deleted:
        raise Http404("评审轮次不存在。")
    return review_round


# ---------------------------------------------------------------------------
# 管理员：对企业已提交的级别指派专家评审；评审记录
# ---------------------------------------------------------------------------


@admin_required
@require_POST
def start_review(request, assessment_id):
    assessment = _get_assessment_or_404(request, assessment_id)
    reuse = request.POST.get("reuse") == "1"
    try:
        review_round = services.start_expert_review(
            assessment, request.user, reuse_experts=reuse, review_days=request.POST.get("review_days")
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
        return redirect(_assessment_url(assessment, anchor=""))
    if review_round.status == ReviewRound.STATUS_ASSIGNING:
        messages.success(request, f"已建立第 {review_round.number} 轮专家评审，请选择评审专家。")
        return redirect("reviews:assign", round_id=review_round.id)
    messages.success(request, f"已由原专家组开展第 {review_round.number} 轮复评，专家会收到站内私信。")
    return redirect("reviews:round_detail", round_id=review_round.id)


@login_required
def history(request, assessment_id):
    assessment = _get_assessment_or_404(request, assessment_id)
    admin = is_admin(request.user)
    rounds = []
    for review_round in services.rounds_of(assessment):
        items = list(review_round.items.select_related("check_item").order_by("check_item__order_index"))
        rounds.append(
            {
                "round": review_round,
                "items": items,
                "pass": sum(1 for i in items if i.final_result == RoundItem.RESULT_PASS),
                "fail": sum(1 for i in items if i.final_result == RoundItem.RESULT_FAIL),
                "assignments": list(review_round.assignments.select_related("expert__expert_profile")) if admin else [],
            }
        )
    project = assessment.project
    return render(
        request,
        "reviews/history.html",
        {
            "assessment": assessment,
            "project": project,
            "sys": assessment.sys,
            "rounds": list(reversed(rounds)),
            "is_admin": admin,
            "is_owner": is_project_owner(request.user, project),
            "tabs": {a.system for a in project.assessments.all()},
            "active_nav": assessment.system,
        },
    )


# ---------------------------------------------------------------------------
# 管理员：评审看板、轮次详情、分配 / 更换 / 催办 / 延期、终审
# ---------------------------------------------------------------------------


@admin_required
def board(request):
    rounds = (
        ReviewRound.objects.filter(assessment__project__deleted_at__isnull=True)
        .select_related("assessment__project__specialty")
        .prefetch_related("assignments__expert__expert_profile")
        .order_by("-submitted_at")
    )
    groups = {status: [] for status, _ in ReviewRound.STATUS_CHOICES}
    latest_ids = {}
    for review_round in rounds:
        latest_ids.setdefault(review_round.assessment_id, review_round.id)
    for review_round in rounds:
        if latest_ids[review_round.assessment_id] != review_round.id:
            continue  # 每个评价只显示最新一轮
        active = [a for a in review_round.assignments.all() if a.status in ExpertAssignment.ACTIVE_STATUSES]
        review_round.active_list = sorted(active, key=lambda a: a.slot)
        review_round.overdue = sum(1 for a in active if a.is_overdue)
        review_round.done = sum(1 for a in active if a.status == ExpertAssignment.STATUS_SUBMITTED)
        groups[review_round.status].append(review_round)
    sections = [(status, label, groups[status]) for status, label in ReviewRound.STATUS_CHOICES]
    return render(request, "reviews/board.html", {"sections": sections, "active_nav": "reviews"})


@admin_required
def round_detail(request, round_id):
    review_round = _get_round_or_404(round_id)
    assessment = review_round.assessment
    project = assessment.project
    show = request.GET.get("show", "")
    submitted, rows = services.opinion_matrix(review_round)
    for row in rows:
        # 整改要求草稿：取判“不通过”专家的建议与点评，管理员整理后发给企业
        texts = []
        for cell in row["cells"]:
            if cell and cell.verdict == RoundItem.RESULT_FAIL:
                texts += [t for t in (cell.suggestion, cell.comment) if t and t not in texts]
        row["draft"] = row["item"].feedback or "\n".join(texts)
    stats = {
        "total": len(rows),
        "split": sum(1 for r in rows if r["split"]),
        "unruled": sum(1 for r in rows if not r["item"].final_result),
        "fail": sum(1 for r in rows if r["item"].final_result == RoundItem.RESULT_FAIL),
        "unanimous_pending": sum(1 for r in rows if r["consensus"] and not r["item"].final_result),
        "missing_feedback": sum(1 for r in rows if r["item"].final_result == RoundItem.RESULT_FAIL and not r["item"].feedback),
    }
    if show == "split":
        rows = [r for r in rows if r["split"]]
    elif show == "unruled":
        rows = [r for r in rows if not r["item"].final_result]
    elif show == "fail":
        rows = [r for r in rows if r["item"].final_result == RoundItem.RESULT_FAIL]
    levels = []
    for row in rows:
        level = row["item"].check_item.level
        if not levels or levels[-1]["level"] != level:
            levels.append({"level": level, "rows": []})
        levels[-1]["rows"].append(row)
    slots = {a.slot: a for a in services.active_assignments(review_round)}
    history = list(
        review_round.assignments.exclude(status__in=ExpertAssignment.ACTIVE_STATUSES)
        .select_related("expert__expert_profile")
        .order_by("-assigned_at")
    )
    return render(
        request,
        "reviews/round_detail.html",
        {
            "round": review_round,
            "assessment": assessment,
            "project": project,
            "sys": assessment.sys,
            "slots": [(slot, slots.get(slot)) for slot in services.SLOTS],
            "free_count": len(services.free_slots(review_round)),
            "history": history,
            "submitted": submitted,
            "levels": levels,
            "stats": stats,
            "show": show,
            "ruling": review_round.status == ReviewRound.STATUS_RULING,
            "config": SystemConfig.get(),
            "rounds": services.rounds_of(assessment),
            "active_nav": "reviews",
        },
    )


def _expert_choices(request, review_round):
    project = review_round.assessment.project
    code = request.GET.get("specialty")
    if code is None:
        specialty = project.specialty
    else:
        specialty = Specialty.objects.filter(code=code).first()
    q = request.GET.get("q", "").strip()
    experts = services.eligible_experts(review_round, specialty, q)
    loads = services.expert_loads(experts)
    for expert in experts:
        expert.conflict = services.conflict_hint(expert, project)
        expert.load = loads.get(expert.user_id, 0)
    # 回避提示的排后，任务少的排前
    experts.sort(key=lambda e: (e.conflict, e.load, e.name))
    return experts, specialty, q


@admin_required
def assign(request, round_id):
    review_round = _get_round_or_404(round_id)
    if request.method == "POST":
        try:
            services.assign_experts(
                review_round, request.user, request.POST.getlist("experts"), request.POST.get("review_days")
            )
            messages.success(request, "已分配专家，专家会收到站内私信；三位专家都接受后进入评审。")
            return redirect("reviews:round_detail", round_id=review_round.id)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _errors(exc))
    experts, specialty, q = _expert_choices(request, review_round)
    return render(
        request,
        "reviews/assign.html",
        {
            "round": review_round,
            "project": review_round.assessment.project,
            "sys": review_round.assessment.sys,
            "experts": experts,
            "specialty": specialty,
            "specialties": Specialty.objects.all(),
            "q": q,
            "free_slots": services.free_slots(review_round),
            "mode": "assign",
            "active_nav": "reviews",
        },
    )


def _get_assignment_or_404(assignment_id):
    assignment = get_object_or_404(
        ExpertAssignment.objects.select_related("round__assessment__project", "expert__expert_profile"), pk=assignment_id
    )
    if assignment.round.assessment.project.is_deleted:
        raise Http404("评审任务不存在。")
    return assignment


@admin_required
def reassign(request, assignment_id):
    assignment = _get_assignment_or_404(assignment_id)
    review_round = assignment.round
    if request.method == "POST":
        try:
            services.reassign_expert(assignment, request.user, request.POST.get("expert"), request.POST.get("reason"))
            messages.success(request, f"席位 {assignment.slot} 已更换专家，新专家会收到站内私信。")
            return redirect("reviews:round_detail", round_id=review_round.id)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _errors(exc))
    experts, specialty, q = _expert_choices(request, review_round)
    return render(
        request,
        "reviews/assign.html",
        {
            "round": review_round,
            "project": review_round.assessment.project,
            "sys": review_round.assessment.sys,
            "experts": experts,
            "specialty": specialty,
            "specialties": Specialty.objects.all(),
            "q": q,
            "assignment": assignment,
            "mode": "reassign",
            "active_nav": "reviews",
        },
    )


@admin_required
@require_POST
def remind(request, assignment_id):
    assignment = _get_assignment_or_404(assignment_id)
    try:
        services.remind_expert(assignment, request.user)
        messages.success(request, f"已向{services.expert_name(assignment.expert)}发送催办私信。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect("reviews:round_detail", round_id=assignment.round_id)


@admin_required
@require_POST
def extend(request, assignment_id):
    assignment = _get_assignment_or_404(assignment_id)
    try:
        services.extend_assignment(assignment, request.user, request.POST.get("days"))
        messages.success(request, f"已延长{services.expert_name(assignment.expert)}的评审期限。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect("reviews:round_detail", round_id=assignment.round_id)


@admin_required
@require_POST
def confirm_unanimous(request, round_id):
    review_round = _get_round_or_404(round_id)
    try:
        count = services.confirm_unanimous(review_round, request.user)
        messages.success(request, f"已按专家一致意见确认 {count} 条。不通过的条目还需逐条填写整改要求。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect("reviews:round_detail", round_id=review_round.id)


@admin_required
@require_POST
def rule(request, round_item_id):
    round_item = get_object_or_404(RoundItem.objects.select_related("round__assessment__project", "check_item"), pk=round_item_id)
    try:
        services.rule_item(
            round_item, request.user, request.POST.get("result"), request.POST.get("reason"), request.POST.get("feedback")
        )
        messages.success(request, f"条目 {round_item.check_item.code} 已终审。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    back = request.POST.get("show", "")
    url = reverse("reviews:round_detail", args=[round_item.round_id])
    return redirect(f"{url}?show={back}#ri-{round_item.id}" if back else f"{url}#ri-{round_item.id}")


@admin_required
@require_POST
def finalize(request, round_id):
    review_round = _get_round_or_404(round_id)
    try:
        if request.POST.get("confirm") != "1":
            raise ValidationError("请先勾选确认终审结果。")
        services.finalize_round(
            review_round, request.user, request.POST.get("rectify_days"), request.POST.get("summary")
        )
        if review_round.status == ReviewRound.STATUS_RECTIFYING:
            messages.success(request, "终审结果已发给企业：全部通过的级别已审核通过，有不通过条目的级别已退回整改。")
        else:
            messages.success(request, "终审完成，已提交的级别审核通过。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect("reviews:round_detail", round_id=review_round.id)


@admin_required
@require_POST
def extend_rectify(request, round_id):
    review_round = _get_round_or_404(round_id)
    try:
        services.extend_rectification(review_round, request.user, request.POST.get("days"))
        messages.success(request, "已延长整改期限，企业会收到通知。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect("reviews:round_detail", round_id=review_round.id)


# ---------------------------------------------------------------------------
# 专家：评审任务
# ---------------------------------------------------------------------------


def _expert_required(request):
    if not is_expert(request.user):
        raise PermissionDenied("评审任务仅对评审专家开放。")


def _get_task_or_404(request, assignment_id):
    _expert_required(request)
    assignment = get_object_or_404(
        ExpertAssignment.objects.select_related("round__assessment__project__specialty"),
        pk=assignment_id,
        expert=request.user,
    )
    if assignment.status == ExpertAssignment.STATUS_CANCELLED or assignment.round.assessment.project.is_deleted:
        raise Http404("评审任务不存在。")
    return assignment


@login_required
def task_list(request):
    _expert_required(request)
    assignments = list(
        ExpertAssignment.objects.filter(expert=request.user, round__assessment__project__deleted_at__isnull=True)
        .select_related("round__assessment__project")
        .order_by("-assigned_at")
    )
    order = {
        ExpertAssignment.STATUS_INVITED: 0,
        ExpertAssignment.STATUS_ACCEPTED: 1,
        ExpertAssignment.STATUS_SUBMITTED: 2,
        ExpertAssignment.STATUS_DECLINED: 3,
        ExpertAssignment.STATUS_CANCELLED: 4,
    }
    assignments.sort(key=lambda a: order[a.status])
    for assignment in assignments:
        assignment.item_count = assignment.round.items.count()
    pending = [a for a in assignments if a.status in ExpertAssignment.PENDING_STATUSES]
    return render(
        request,
        "reviews/task_list.html",
        {"assignments": assignments, "pending": pending, "pledge": services.PLEDGE_CLAUSES, "active_nav": "tasks"},
    )


@login_required
def task_detail(request, assignment_id):
    assignment = _get_task_or_404(request, assignment_id)
    review_round = assignment.round
    assessment = review_round.assessment
    project = assessment.project
    items = services.task_items(assignment)
    opinions = services.opinions_by_item(assignment)
    previous = {}
    if review_round.number > 1:
        prior = assessment.review_rounds.filter(number=review_round.number - 1).first()
        if prior:
            previous = {ri.check_item_id: ri for ri in prior.items.all()}
    levels = sorted({ri.check_item.level for ri in items})
    try:
        level = int(request.GET.get("level") or 0)
    except (TypeError, ValueError):
        level = 0
    if level not in levels:
        # 默认打开第一个还有未填结论的级别
        level = next((ri.check_item.level for ri in items if not getattr(opinions.get(ri.id), "verdict", "")), levels[0] if levels else 1)
    level_items = [ri for ri in items if ri.check_item.level == level]
    evidence_ids = {e["id"] for ri in level_items for e in ri.snap_evidence}
    evidence_files = {str(e.id): e for e in EvidenceFile.objects.filter(id__in=evidence_ids)}
    for ri in level_items:
        ri.opinion = opinions.get(ri.id)
        ri.previous = previous.get(ri.check_item_id)
        ri.evidence = [dict(entry, file=evidence_files.get(entry["id"])) for entry in ri.snap_evidence]
    level_tabs = []
    for lv in levels:
        lv_items = [ri for ri in items if ri.check_item.level == lv]
        filled = sum(1 for ri in lv_items if getattr(opinions.get(ri.id), "verdict", ""))
        level_tabs.append({"level": lv, "total": len(lv_items), "filled": filled})
    missing, unexplained = services.opinion_gaps(assignment) if assignment.status == ExpertAssignment.STATUS_ACCEPTED else ([], [])
    return render(
        request,
        "reviews/task_detail.html",
        {
            "assignment": assignment,
            "round": review_round,
            "assessment": assessment,
            "project": project,
            "sys": assessment.sys,
            "level": level,
            "next_level": next((lv for lv in levels if lv > level), None),
            "level_tabs": level_tabs,
            "level_items": level_items,
            "total": len(items),
            "filled": sum(1 for ri in items if getattr(opinions.get(ri.id), "verdict", "")),
            "missing": missing,
            "unexplained": unexplained,
            "editable": assignment.status == ExpertAssignment.STATUS_ACCEPTED,
            "pledge": services.PLEDGE_CLAUSES,
            "active_nav": "tasks",
        },
    )


@login_required
@require_POST
def task_respond(request, assignment_id):
    assignment = _get_task_or_404(request, assignment_id)
    accept = request.POST.get("action") == "accept"
    try:
        services.respond_assignment(
            assignment, request.user, accept, pledge=request.POST.get("pledge") == "1", reason=request.POST.get("reason")
        )
        if accept:
            messages.success(request, "已接受评审任务，可以开始逐条评审。")
            return redirect("reviews:task_detail", assignment_id=assignment.id)
        messages.success(request, "已拒绝该任务，评价机构会另行安排专家。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect("reviews:task_list")


def _opinion_data(request):
    data = {}
    for key, value in request.POST.items():
        field, _, round_item_id = key.partition("-")
        if field in ("verdict", "suggestion", "comment") and round_item_id.isdigit():
            data.setdefault(int(round_item_id), {})[field] = value
    return data


@login_required
@require_POST
def task_save(request, assignment_id):
    assignment = _get_task_or_404(request, assignment_id)
    level = request.POST.get("level", "")
    try:
        services.save_opinions(assignment, request.user, _opinion_data(request))
        messages.success(request, f"第 {level} 级的评审意见已保存。")
        next_level = request.POST.get("next_level")
        if next_level:
            level = next_level
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(f"{reverse('reviews:task_detail', args=[assignment.id])}?level={level}")


@login_required
@require_POST
def task_submit(request, assignment_id):
    assignment = _get_task_or_404(request, assignment_id)
    try:
        if request.POST.get("confirm") != "1":
            raise ValidationError("请先勾选确认评审意见独立、客观。")
        services.submit_opinions(assignment, request.user)
        messages.success(request, "评审意见已提交。提交后不能修改，评价机构会汇总三位专家的意见后终审。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect("reviews:task_detail", assignment_id=assignment.id)


@login_required
def task_evidence(request, assignment_id, evidence_id):
    assignment = _get_task_or_404(request, assignment_id)
    if assignment.status not in (ExpertAssignment.STATUS_ACCEPTED, ExpertAssignment.STATUS_SUBMITTED):
        raise Http404("接受任务后才能查看佐证材料。")
    if not services.expert_can_download(assignment, evidence_id):
        raise Http404("佐证材料不存在。")
    evidence = get_object_or_404(EvidenceFile, pk=evidence_id)
    return FileResponse(evidence.file.open("rb"), as_attachment=True, filename=evidence.original_filename)
