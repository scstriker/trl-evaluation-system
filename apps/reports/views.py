from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import (
    admin_required,
    can_edit_project_info,
    can_generate_report,
    can_view_project,
    get_visible_project_or_404,
    is_admin,
    is_expert,
    is_project_owner,
    visible_projects,
)
from apps.evaluations.forms import ProjectInfoForm, save_team_formset, team_formset
from apps.evaluations.models import Assessment, EvaluationTeamMember
from apps.evaluations.services import ready_for_report, system_enabled
from apps.accounts.models import SystemConfig
from apps.audit.models import AuditLog
from apps.reports import services
from apps.reports.pdf import LABELS as PDF_LABELS
from apps.reports.pdf import detect_converter
from apps.reports.models import Report, ReportRequest


def _errors(exc):
    return "；".join(getattr(exc, "messages", [str(exc)]))


def _preview_url(project_id, report_type):
    return f"{reverse('reports:report_preview', args=[project_id])}?type={report_type}"


def _default_type(availability, enabled):
    if Report.TYPE_COMBINED in enabled and availability[Report.TYPE_COMBINED]["available"] and not availability[Report.TYPE_COMBINED]["reasons"]:
        return Report.TYPE_COMBINED
    for report_type in (Report.TYPE_TRL, Report.TYPE_MRL):
        if report_type in enabled and availability[report_type]["available"]:
            return report_type
    return Report.TYPE_TRL


@login_required
def report_preview(request, project_id):
    project = get_visible_project_or_404(request.user, project_id)
    admin = is_admin(request.user)
    owner = is_project_owner(request.user, project)
    assessments = list(project.assessments.all())
    enabled = services.enabled_report_types()
    availability = services.report_availability(project)
    report_type = request.GET.get("type") or _default_type(availability, enabled)
    if report_type not in enabled or not availability[report_type]["available"]:
        report_type = _default_type(availability, enabled)
    report = services.build_report(project, report_type)
    tabs = [{"type": key, "label": label, **availability[key]} for key, label in Report.TYPE_CHOICES if key in enabled]
    reports = project.reports.filter(report_type=report_type).select_related("generated_by__enterprise")
    pending_request = services.get_pending_request(project, report_type)
    last_request = services.get_last_request(project, report_type)

    # 管理员看完整生成条件；企业看申请导出的条件（第三方评价组属于评价机构待办）
    generate_blockers = services.generate_blockers(project, report_type)
    request_blockers = services.request_blockers(project, report_type)
    # 企业只有在评价机构审核通过（已生成该类报告）后才能打印 / 下载
    print_allowed = admin or reports.exists()

    info_form = ProjectInfoForm(instance=project, admin=admin)
    return render(
        request,
        "reports/report_preview.html",
        {
            "project": project,
            "report": report,
            "report_type": report_type,
            "tabs": tabs,
            "reports": reports,
            "next_version": (reports.first().version + 1) if reports else 1,
            "report_no_preview": f"{services.base_report_no(project)}-{services.TYPE_SUFFIX[report_type]}",
            "blockers": generate_blockers if admin else request_blockers,
            "generate_blockers": generate_blockers,
            "request_blockers": request_blockers,
            "can_generate": can_generate_report(request.user) and not generate_blockers,
            "can_request": owner and not request_blockers and not pending_request,
            "pending_request": pending_request,
            "last_request": last_request,
            "print_allowed": print_allowed,
            "is_admin": admin,
            "is_owner": owner,
            "can_edit_info": can_edit_project_info(request.user, project),
            "info_form": info_form,
            "team_self": team_formset(project, EvaluationTeamMember.GROUP_SELF),
            "team_third": team_formset(project, EvaluationTeamMember.GROUP_THIRD) if admin else None,
            "third_members": project.team_members.filter(group=EvaluationTeamMember.GROUP_THIRD),
            "summaries": [part["assessment"] for part in report["parts"]],
            "active_nav": "reports",
            "assessment_tabs": {a.system for a in assessments},
            "pdf_tool": PDF_LABELS.get(detect_converter()) if admin else "",
            "seal_ready": bool(SystemConfig.get().seal_image),
        },
    )


@login_required
@require_POST
def update_report_info(request, project_id):
    project = get_visible_project_or_404(request.user, project_id)
    report_type = request.POST.get("type", Report.TYPE_TRL)
    if not can_edit_project_info(request.user, project):
        raise PermissionDenied("当前用户不能编辑报告信息。")
    admin = is_admin(request.user)
    form = ProjectInfoForm(request.POST, instance=project, admin=admin)
    formsets = [(EvaluationTeamMember.GROUP_SELF, team_formset(project, EvaluationTeamMember.GROUP_SELF, request.POST))]
    if admin:
        formsets.append(
            (EvaluationTeamMember.GROUP_THIRD, team_formset(project, EvaluationTeamMember.GROUP_THIRD, request.POST))
        )
    if not form.is_valid() or not all(formset.is_valid() for _, formset in formsets):
        messages.error(request, "报告信息保存失败：评价组成员的角色和姓名为必填项，请检查后重试。")
        return redirect(_preview_url(project.id, report_type))
    with transaction.atomic():
        form.save()
        for group, formset in formsets:
            save_team_formset(formset, group)
        for order, member in enumerate(project.team_members.all(), start=1):
            if member.order != order:
                member.order = order
                member.save(update_fields=["order"])
        if admin:
            for assessment in project.assessments.all():
                key = f"summary_{assessment.system}"
                if key in request.POST:
                    assessment.summary_text = request.POST[key].strip()
                    assessment.save(update_fields=["summary_text", "updated_at"])
    messages.success(request, "报告编制信息已保存。")
    return redirect(_preview_url(project.id, report_type))


@login_required
@require_POST
def generate_report(request, project_id):
    project = get_visible_project_or_404(request.user, project_id)
    report_type = request.POST.get("type", Report.TYPE_TRL)
    try:
        record = services.generate_report(project, report_type, request.user)
        messages.success(request, f"{record.get_report_type_display()} V{record.version} 已生成，企业可在“评价报告”中下载。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_preview_url(project.id, report_type))


@login_required
@require_POST
def request_export(request, project_id):
    project = get_visible_project_or_404(request.user, project_id)
    report_type = request.POST.get("type", Report.TYPE_TRL)
    try:
        if request.POST.get("confirm") != "1":
            raise ValidationError("请先勾选确认报告信息已核对无误。")
        services.request_export(project, report_type, request.user)
        messages.success(request, "报告导出申请已提交，评价机构审核通过后即可下载和打印。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_preview_url(project.id, report_type))


def _get_request_or_404(request, request_id):
    report_request = get_object_or_404(ReportRequest.objects.select_related("project"), pk=request_id)
    if not can_view_project(request.user, report_request.project):
        raise Http404("申请不存在。")
    return report_request


@login_required
@require_POST
def approve_request(request, request_id):
    report_request = _get_request_or_404(request, request_id)
    try:
        record = services.approve_request(report_request, request.user)
        messages.success(request, f"已通过导出申请并生成{record.get_report_type_display()} V{record.version}，企业现在可以下载和打印。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_preview_url(report_request.project_id, report_request.report_type))


@login_required
@require_POST
def reject_request(request, request_id):
    report_request = _get_request_or_404(request, request_id)
    try:
        services.reject_request(report_request, request.user, request.POST.get("reason"))
        messages.success(request, "已驳回导出申请，企业会看到驳回原因。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_preview_url(report_request.project_id, report_request.report_type))


@login_required
def download_report(request, report_id):
    report = get_object_or_404(Report.objects.select_related("project"), pk=report_id)
    if not can_view_project(request.user, report.project):
        raise Http404("报告不存在。")
    return FileResponse(report.file.open("rb"), as_attachment=True, filename=report.filename)


@login_required
def report_list(request):
    if is_expert(request.user):
        return redirect("accounts:home")
    admin = is_admin(request.user)
    projects = visible_projects(request.user)
    reports = Report.objects.filter(project__in=projects).select_related("project", "generated_by__enterprise")
    requests = ReportRequest.objects.filter(project__in=projects).select_related(
        "project", "requested_by__enterprise", "reviewed_by"
    )
    pending_requests = [r for r in requests if r.status == ReportRequest.STATUS_PENDING]
    # 已全部审核通过、尚待出具报告的评价
    waiting = []
    for assessment in Assessment.objects.filter(project__in=projects, status=Assessment.STATUS_IN_PROGRESS).select_related(
        "project"
    ):
        if system_enabled(assessment.system) and ready_for_report(assessment):
            waiting.append(assessment)
    return render(
        request,
        "reports/report_list.html",
        {
            "reports": reports,
            "waiting": waiting,
            "pending_requests": pending_requests,
            "my_requests": [] if admin else list(requests[:20]),
            "is_admin": admin,
            "active_nav": "reports",
        },
    )


# ---------------------------------------------------------------------------
# 报告前置信息（评价机构按固定模板逐节填写）与 PDF
# ---------------------------------------------------------------------------


@admin_required
def front_matter_edit(request, project_id):
    from apps.reports.forms import FrontMatterForm, ModuleFormSet

    project = get_visible_project_or_404(request.user, project_id)
    front_matter = services.get_front_matter(project)
    report_type = request.GET.get("type") or request.POST.get("type") or Report.TYPE_TRL
    form = FrontMatterForm(request.POST or None, instance=front_matter)
    modules = ModuleFormSet(request.POST or None, instance=front_matter, prefix="modules")
    if request.method == "POST":
        if form.is_valid() and modules.is_valid():
            with transaction.atomic():
                front_matter = form.save(commit=False)
                front_matter.updated_by = request.user
                front_matter.save()
                saved = modules.save(commit=False)
                for order, module in enumerate(saved, start=1):
                    module.order = module.order or order
                    module.save()
                for module in modules.deleted_objects:
                    module.delete()
                for order, module in enumerate(front_matter.modules.all(), start=1):
                    if module.order != order:
                        module.order = order
                        module.save(update_fields=["order"])
                AuditLog.objects.create(
                    actor=request.user,
                    action="report.front_matter",
                    model_name="ReportFrontMatter",
                    object_id=str(front_matter.pk),
                    object_repr="报告前置信息",
                    project=project,
                    after={"changed": form.changed_data, "modules": front_matter.modules.count()},
                )
            messages.success(request, "报告前置信息已保存。预览和下一次生成的报告会使用新内容。")
            return redirect(_preview_url(project.id, report_type))
        messages.error(request, "保存失败：关键技术 / 模块的名称为必填项，日期格式为 年-月-日。")
    defaults = services._default_sections(
        project, report_type, services.build_report(project, report_type)["parts"]
    ) if project.assessments.exists() else {}
    return render(
        request,
        "reports/front_matter.html",
        {
            "project": project,
            "form": form,
            "modules": modules,
            "defaults": defaults,
            "report_type": report_type,
            "chapters": [
                ("1 概况", ["s1_1_1", "s1_1_2", "s1_1_3", "s1_1_4", "s1_1_5", "s1_2_1", "s1_2_2", "s1_2_3", "s1_2_4"]),
                ("2 评价实施情况", ["s2_1", "s2_3", "s2_4"]),
                ("3 评价对象研发信息", ["s3_1", "s3_4_software"]),
            ],
            "active_nav": "reports",
            "tabs": {a.system for a in project.assessments.all()},
        },
    )


@login_required
def download_report_pdf(request, report_id):
    report = get_object_or_404(Report.objects.select_related("project"), pk=report_id)
    if not can_view_project(request.user, report.project) or not report.pdf_file:
        raise Http404("报告不存在。")
    return FileResponse(report.pdf_file.open("rb"), as_attachment=True, filename=report.pdf_filename)


@login_required
@require_POST
def make_pdf(request, report_id):
    report = get_object_or_404(Report.objects.select_related("project"), pk=report_id)
    if not can_view_project(request.user, report.project):
        raise Http404("报告不存在。")
    try:
        services.make_report_pdf(report, request.user)
        messages.success(request, f"已为 V{report.version} 生成 PDF。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(_preview_url(report.project_id, report.report_type))
