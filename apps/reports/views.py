from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import (
    can_edit_project_info,
    can_generate_report,
    can_view_project,
    get_visible_project_or_404,
    is_admin,
    visible_projects,
)
from apps.evaluations.forms import ProjectInfoForm, TeamMemberFormSet
from apps.evaluations.models import Assessment
from apps.evaluations.services import get_achieved_level
from apps.reports import services
from apps.reports.models import Report


def _preview_url(project_id, report_type):
    return f"{reverse('reports:report_preview', args=[project_id])}?type={report_type}"


def _default_type(availability, assessments):
    if availability[Report.TYPE_COMBINED]["available"] and not availability[Report.TYPE_COMBINED]["reasons"]:
        return Report.TYPE_COMBINED
    for report_type in (Report.TYPE_TRL, Report.TYPE_MRL):
        if availability[report_type]["available"]:
            return report_type
    return Report.TYPE_TRL


@login_required
def report_preview(request, project_id):
    project = get_visible_project_or_404(request.user, project_id)
    admin = is_admin(request.user)
    assessments = list(project.assessments.all())
    availability = services.report_availability(project)
    report_type = request.GET.get("type") or _default_type(availability, assessments)
    if report_type not in availability or not availability[report_type]["available"]:
        report_type = _default_type(availability, assessments)
    report = services.build_report(project, report_type)
    # 企业只需知道评价进度原因；“评价组成员未填写”等属于评价机构的待办
    blockers = services.generate_blockers(project, report_type) if admin else availability[report_type]["reasons"]
    tabs = [
        {"type": key, "label": label, **availability[key]}
        for key, label in Report.TYPE_CHOICES
    ]
    info_form = ProjectInfoForm(instance=project, admin=admin)
    team_formset = TeamMemberFormSet(instance=project, prefix="team") if admin else None
    reports = project.reports.filter(report_type=report_type).select_related("generated_by__enterprise")
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
            "blockers": blockers,
            "can_generate": can_generate_report(request.user) and not blockers,
            "is_admin": admin,
            "can_edit_info": can_edit_project_info(request.user, project),
            "info_form": info_form,
            "team_formset": team_formset,
            "summaries": [part["assessment"] for part in report["parts"]],
            "active_nav": "reports",
            "assessment_tabs": {a.system for a in assessments},
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
    formset = TeamMemberFormSet(request.POST, instance=project, prefix="team") if admin else None
    if not form.is_valid() or (formset is not None and not formset.is_valid()):
        messages.error(request, "报告信息保存失败：评价组成员的角色和姓名为必填项，请检查后重试。")
        return redirect(_preview_url(project.id, report_type))
    with transaction.atomic():
        form.save()
        if admin:
            formset.save()
            for order, member in enumerate(project.team_members.all(), start=1):
                if member.order != order:
                    member.order = order
                    member.save(update_fields=["order"])
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
        messages.error(request, "；".join(getattr(exc, "messages", [str(exc)])))
    return redirect(_preview_url(project.id, report_type))


@login_required
def download_report(request, report_id):
    report = get_object_or_404(Report.objects.select_related("project"), pk=report_id)
    if not can_view_project(request.user, report.project):
        raise Http404("报告不存在。")
    return FileResponse(report.file.open("rb"), as_attachment=True, filename=report.filename)


@login_required
def report_list(request):
    admin = is_admin(request.user)
    reports = Report.objects.filter(project__in=visible_projects(request.user)).select_related(
        "project", "generated_by__enterprise"
    )
    # 已全部审核通过、尚待出具报告的评价（管理员待办 / 企业知情）
    waiting = []
    for assessment in Assessment.objects.filter(
        project__in=visible_projects(request.user), status=Assessment.STATUS_IN_PROGRESS
    ).select_related("project"):
        if get_achieved_level(assessment) >= assessment.target_level:
            waiting.append(assessment)
    return render(
        request,
        "reports/report_list.html",
        {"reports": reports, "waiting": waiting, "is_admin": admin, "active_nav": "reports"},
    )
