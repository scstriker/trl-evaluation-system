from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import can_generate_report, can_modify_project_when_mutable, can_view_project
from apps.evaluations.forms import ProjectReportInfoForm
from apps.evaluations.models import EvaluationProject
from apps.evaluations.services import get_achieved_level
from apps.reports import services
from apps.reports.models import Report


def _get_project_or_403(request, project_id):
    project = get_object_or_404(EvaluationProject, pk=project_id)
    if not can_view_project(request.user, project):
        raise PermissionDenied("当前用户不能查看该评价项目。")
    return project


@login_required
def report_preview(request, project_id):
    project = _get_project_or_403(request, project_id)
    context = services.build_report_context(project)
    achieved = context["achieved"]
    can_generate = can_generate_report(request.user)
    generate_blockers = []
    if achieved < project.target_level:
        generate_blockers.append(
            f"目标等级 TRL {project.target_level} 尚未全部审核通过（当前达成 TRL {achieved}）"
        )
    if project.status == EvaluationProject.STATUS_ARCHIVED:
        generate_blockers.append("项目已归档")
    if not can_generate:
        generate_blockers.append("当前角色没有生成报告权限（需审核员或系统管理员）")

    info_form = ProjectReportInfoForm(instance=project)
    can_edit_info = can_modify_project_when_mutable(request.user, project) and project.status not in {
        EvaluationProject.STATUS_ARCHIVED,
    }

    context.update(
        {
            "reports": project.reports.select_related("generated_by"),
            "can_generate": can_generate and not generate_blockers,
            "generate_blockers": generate_blockers,
            "info_form": info_form,
            "can_edit_info": can_edit_info,
        }
    )
    return render(request, "reports/report_preview.html", context)


@login_required
@require_POST
def update_report_info(request, project_id):
    project = _get_project_or_403(request, project_id)
    if not can_modify_project_when_mutable(request.user, project):
        messages.error(request, "当前用户不能编辑报告信息。")
        return redirect("reports:report_preview", project_id=project.id)
    form = ProjectReportInfoForm(request.POST, instance=project)
    if form.is_valid():
        form.save()
        messages.success(request, "报告编制信息已保存。")
    else:
        messages.error(request, "报告编制信息保存失败，请检查填写内容。")
    return redirect("reports:report_preview", project_id=project.id)


@login_required
@require_POST
def generate_report(request, project_id):
    project = _get_project_or_403(request, project_id)
    try:
        report = services.generate_report(project, request.user)
        messages.success(request, f"评价报告 V{report.version} 生成成功，可在报告版本列表中下载。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "；".join(getattr(exc, "messages", [str(exc)])))
    return redirect("reports:report_preview", project_id=project.id)


@login_required
def download_report(request, report_id):
    report = get_object_or_404(Report.objects.select_related("project"), pk=report_id)
    if not can_view_project(request.user, report.project):
        raise PermissionDenied("当前用户不能下载该报告。")
    return FileResponse(report.file.open("rb"), as_attachment=True, filename=report.filename)


@login_required
def global_report_list(request):
    reports = Report.objects.select_related("project", "generated_by").all()
    visible = [report for report in reports if can_view_project(request.user, report.project)]
    return render(request, "reports/global_report_list.html", {"reports": visible})
