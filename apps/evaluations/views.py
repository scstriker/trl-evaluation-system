from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import (
    can_create_project,
    can_review,
    get_visible_project_or_404,
    is_admin,
    is_project_owner,
    visible_projects,
)
from apps.audit.models import AuditLog
from apps.evaluations import services
from apps.evaluations.forms import ProjectForm
from apps.evaluations.models import Assessment, CheckItem, EvaluationProject, LevelReview
from apps.rules.models import LevelDefinition
from apps.rules.systems import MRL, TRL, get_system


def _errors(exc):
    return "；".join(getattr(exc, "messages", [str(exc)]))


def _assessment_url(project_id, system, level=None, item=None):
    url = reverse("evaluations:assessment_detail", args=[project_id, system])
    params = []
    if level:
        params.append(f"level={level}")
    if item:
        params.append(f"item={item}")
    return f"{url}?{'&'.join(params)}" if params else url


def _get_assessment_or_404(request, project_id, system):
    project = get_visible_project_or_404(request.user, project_id)
    assessment = project.assessments.filter(system=system).first()
    if assessment is None:
        raise Http404("该项目未开展此类评价。")
    return project, assessment


# ---------------------------------------------------------------------------
# TRL评价 / MRL评价 列表
# ---------------------------------------------------------------------------


@login_required
def assessment_list(request, system):
    sys = get_system(system)
    admin = is_admin(request.user)
    projects = visible_projects(request.user).select_related("owner__enterprise").prefetch_related("assessments")

    rows, without = [], []
    counters = {"total": 0, "draft": 0, "submitted": 0, "returned": 0, "ready": 0, "reported": 0}
    for project in projects:
        assessment = project.get_assessment(system)
        if assessment is None:
            without.append(project)
            continue
        reviews = services.get_reviews(assessment)
        achieved = services.get_achieved_level(assessment, reviews)
        submitted = services.get_submitted_levels(assessment, reviews)
        returned = services.get_returned_levels(assessment, reviews)
        step_text, step_tone = services.next_step(assessment, admin)
        counters["total"] += 1
        if assessment.status == Assessment.STATUS_REPORTED:
            bucket = "reported"
        elif achieved >= assessment.target_level:
            bucket = "ready"
        elif submitted:
            bucket = "submitted"
        elif returned:
            bucket = "returned"
        else:
            bucket = "draft"
        counters[bucket] += 1
        rows.append(
            {
                "project": project,
                "assessment": assessment,
                "stats": services.get_scope_stats(assessment),
                "achieved": achieved,
                "bucket": bucket,
                "step_text": step_text,
                "step_tone": step_tone,
            }
        )

    # 需要当前用户处理的排在最前：管理员看待审核，企业看被退回
    first_bucket = "submitted" if admin else "returned"
    rows.sort(key=lambda row: (row["bucket"] != first_bucket, row["bucket"] == "reported"))

    return render(
        request,
        "evaluations/assessment_list.html",
        {
            "sys": sys,
            "rows": rows,
            "without": without,
            "counters": counters,
            "can_create": can_create_project(request.user),
            "rules_ready": services.rules_available(system),
            "active_nav": system,
        },
    )


# ---------------------------------------------------------------------------
# 申报项目
# ---------------------------------------------------------------------------


@login_required
def project_create(request):
    if not can_create_project(request.user):
        raise PermissionDenied("评价项目由企业用户申报。")
    profile = request.user.enterprise
    preset = request.GET.get("system", TRL)
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.applicant = profile.company_name
            try:
                services.create_project(project, request.user, form.targets())
            except ValidationError as exc:
                form.add_error(None, _errors(exc))
            else:
                first = TRL if TRL in form.targets() else MRL
                messages.success(request, "评价申请已提交。请逐条填写自评结论并上传佐证材料，填写完成后提交审核。")
                return redirect("evaluations:assessment_detail", project_id=project.id, system=first)
    else:
        form = ProjectForm(
            initial={
                "tech_type": EvaluationProject.TECH_EQUIPMENT,
                "include_trl": preset != MRL,
                "include_mrl": preset == MRL,
                "trl_target": 4,
                "mrl_target": 4,
            }
        )
    return render(
        request,
        "evaluations/project_form.html",
        {
            "form": form,
            "applicant": profile.company_name,
            "trl_defs": LevelDefinition.objects.filter(system=TRL),
            "mrl_defs": LevelDefinition.objects.filter(system=MRL),
            "active_nav": MRL if preset == MRL else TRL,
        },
    )


@login_required
def project_home(request, project_id):
    project = get_visible_project_or_404(request.user, project_id)
    first = project.assessments.first()
    return redirect("evaluations:assessment_detail", project_id=project.id, system=first.system if first else TRL)


@login_required
@require_POST
def add_assessment_view(request, project_id, system):
    project = get_visible_project_or_404(request.user, project_id)
    if not is_project_owner(request.user, project):
        raise PermissionDenied("只有申报企业可以追加评价。")
    try:
        services.add_assessment(project, system, request.POST.get("target_level"), request.user)
        messages.success(request, f"已追加{get_system(system).name}评价，请逐条填写自评。")
    except ValidationError as exc:
        messages.error(request, _errors(exc))
    return redirect("evaluations:assessment_detail", project_id=project.id, system=system)


# ---------------------------------------------------------------------------
# 项目评价页
# ---------------------------------------------------------------------------


def _default_level(assessment, states, admin):
    if admin:
        for state in states:
            if state["state"] == LevelReview.STATUS_SUBMITTED:
                return state["level"]
    else:
        for wanted in (LevelReview.STATUS_RETURNED, services.STATE_DRAFT, services.STATE_READY):
            for state in states:
                if state["state"] == wanted:
                    return state["level"]
    return max(1, min(assessment.target_level, services.get_achieved_level(assessment) or 1))


@login_required
def assessment_detail(request, project_id, system):
    project = get_visible_project_or_404(request.user, project_id)
    sys = get_system(system)
    admin = is_admin(request.user)
    owner = is_project_owner(request.user, project)
    assessment = project.get_assessment(system)
    tabs = {a.system for a in project.assessments.all()}

    base = {"project": project, "sys": sys, "tabs": tabs, "active_nav": system, "is_owner": owner}
    if assessment is None:
        return render(
            request,
            "evaluations/assessment_absent.html",
            {**base, "rules_ready": services.rules_available(system), "levels": LevelDefinition.objects.filter(system=system)},
        )

    reviews = services.get_reviews(assessment)
    counts = services._level_counts(assessment)
    states = services.get_level_states(assessment, reviews, counts)
    achieved = services.get_achieved_level(assessment, reviews)

    try:
        view_level = int(request.GET.get("level") or _default_level(assessment, states, admin))
    except (TypeError, ValueError):
        view_level = 1
    view_level = max(1, min(assessment.target_level, view_level))
    current_state = states[view_level - 1]

    items = list(
        assessment.items.filter(level=view_level)
        .prefetch_related("evidence_files__uploaded_by__enterprise")
        .select_related("evaluated_by__enterprise")
        .order_by("track", "seq")
    )
    logs_by_item = {}
    for log in AuditLog.objects.filter(
        model_name="CheckItem", object_id__in=[str(item.id) for item in items]
    ).select_related("actor__enterprise")[:300]:
        logs_by_item.setdefault(log.object_id, []).append(log)
    for item in items:
        item.history = logs_by_item.get(str(item.id), [])

    groups = []
    for track, label in CheckItem.TRACK_CHOICES:
        group_items = [item for item in items if item.track == track]
        if group_items:
            title = f"{label}细则（{len(group_items)} 条）" if track != CheckItem.TRACK_GENERAL else f"本级细则（{len(group_items)} 条）"
            groups.append({"title": title, "items": group_items})

    submitted_levels = services.get_submitted_levels(assessment, reviews)
    returned_groups = {}
    for level in services.get_returned_levels(assessment, reviews):
        review = reviews[level]
        key = (review.comment, review.reviewed_at)
        returned_groups.setdefault(key, {"review": review, "levels": []})["levels"].append(level)
    returned_reviews = [
        {**group, "levels_text": services.format_levels(group["levels"]), "first": group["levels"][0]}
        for group in returned_groups.values()
    ]
    submit_plan = services.get_submit_plan(assessment, reviews, counts) if owner else []
    pass_plan = services.get_pass_plan(assessment, reviews) if admin else []
    open_levels = sorted({level for option in submit_plan for level in option["levels"]})
    submit_attention = services.get_attention_items(assessment, open_levels) if submit_plan else None
    review_attention = services.get_attention_items(assessment, submitted_levels) if admin and submitted_levels else None
    ready_options = [option for option in submit_plan if option["ready"]]
    returnable = []
    if admin and assessment.status == Assessment.STATUS_IN_PROGRESS:
        returnable = [
            {"level": level, "label": services.STATE_LABELS[reviews[level].status]}
            for level in services.get_returnable_levels(assessment, reviews)
        ]

    item_editable = owner and services.is_level_editable(assessment, view_level, reviews)
    can_comment = (
        admin
        and assessment.status == Assessment.STATUS_IN_PROGRESS
        and current_state["state"] in (LevelReview.STATUS_SUBMITTED, LevelReview.STATUS_RETURNED)
    )

    return render(
        request,
        "evaluations/assessment_detail.html",
        {
            **base,
            "assessment": assessment,
            "states": states,
            "achieved": achieved,
            "scope": services.get_scope_stats(assessment),
            "view_level": view_level,
            "current_state": current_state,
            "level_def": LevelDefinition.objects.filter(system=system, level=view_level).first(),
            "groups": groups,
            "item_editable": item_editable,
            "can_comment": can_comment,
            "is_admin": admin,
            "submitted_levels": submitted_levels,
            "submitted_text": services.format_levels(submitted_levels),
            "submitted_review": reviews.get(submitted_levels[0]) if submitted_levels else None,
            "returned_reviews": returned_reviews,
            "submit_plan": submit_plan,
            "default_submit": ready_options[-1]["up_to"] if ready_options else None,
            "submit_attention": submit_attention,
            "pass_plan": pass_plan,
            "pass_plan_text": services.format_levels(pass_plan),
            "review_attention": review_attention,
            "returnable": returnable,
            "resubmitted_reviews": [reviews[level] for level in submitted_levels if reviews[level].comment],
            "ready_for_report": achieved >= assessment.target_level,
            "reopen_item": request.GET.get("item", ""),
        },
    )


# ---------------------------------------------------------------------------
# 条目：企业自评 / 管理员逐条意见
# ---------------------------------------------------------------------------


def _get_item_or_404(request, item_id):
    item = get_object_or_404(CheckItem.objects.select_related("assessment__project"), pk=item_id)
    project = item.assessment.project
    if not (is_admin(request.user) or is_project_owner(request.user, project)):
        raise Http404("条目不存在。")
    return item


@login_required
@require_POST
def item_evaluate(request, item_id):
    item = _get_item_or_404(request, item_id)
    try:
        services.evaluate_check_item(item, request.user, request.POST)
        messages.success(request, f"条目 {item.code} 的自评已保存。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
        return redirect(_assessment_url(item.assessment.project_id, item.assessment.system, item.level, item.id))
    return redirect(_assessment_url(item.assessment.project_id, item.assessment.system, item.level))


@login_required
@require_POST
def item_review_comment(request, item_id):
    item = _get_item_or_404(request, item_id)
    try:
        services.set_review_comment(item, request.user, request.POST.get("review_comment", ""))
        messages.success(request, f"条目 {item.code} 的审核意见已保存，退回后企业可在该条目看到。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_assessment_url(item.assessment.project_id, item.assessment.system, item.level))


# ---------------------------------------------------------------------------
# 提交审核 / 审核通过 / 退回 / 重新开放
# ---------------------------------------------------------------------------


@login_required
@require_POST
def submit_view(request, project_id, system):
    project, assessment = _get_assessment_or_404(request, project_id, system)
    try:
        if request.POST.get("confirm") != "1":
            raise ValidationError("请先勾选确认填写内容真实、准确。")
        levels = services.submit_levels(assessment, request.user, request.POST.get("up_to"))
        messages.success(
            request,
            f"第 {services.format_levels(levels)} 级已提交审核。审核期间这些级别不能修改，如被退回会在页面顶部提示。",
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_assessment_url(project.id, system))


@login_required
@require_POST
def pass_view(request, project_id, system):
    project, assessment = _get_assessment_or_404(request, project_id, system)
    try:
        if request.POST.get("confirm") != "1":
            raise ValidationError("请先勾选确认已核查所提交的内容。")
        levels = services.pass_levels(assessment, request.user, request.POST.get("up_to"))
        achieved = services.get_achieved_level(assessment)
        text = f"第 {services.format_levels(levels)} 级审核通过。"
        if achieved >= assessment.target_level:
            text += f"已达到目标等级 {assessment.sys.abbr} {assessment.target_level}，可以生成报告。"
        messages.success(request, text)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_assessment_url(project.id, system))


@login_required
@require_POST
def return_view(request, project_id, system):
    project, assessment = _get_assessment_or_404(request, project_id, system)
    try:
        levels = services.return_levels(assessment, request.user, request.POST.getlist("levels"), request.POST.get("comment"))
        messages.success(request, f"已退回第 {services.format_levels(levels)} 级，企业登录后可看到审核意见。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_assessment_url(project.id, system))


@login_required
@require_POST
def reopen_view(request, project_id, system):
    project, assessment = _get_assessment_or_404(request, project_id, system)
    if not can_review(request.user):
        raise PermissionDenied("只有管理员可以重新开放评价。")
    try:
        services.reopen_assessment(assessment, request.user)
        messages.success(request, "评价已重新开放。如需企业修改，请在对应级别执行退回。")
    except ValidationError as exc:
        messages.error(request, _errors(exc))
    return redirect(_assessment_url(project.id, system))
