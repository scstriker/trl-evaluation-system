from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import can_view_project, is_project_owner
from apps.evaluations.models import CheckItem
from apps.evidence import services
from apps.evidence.models import EvidenceFile


def _redirect_to_item(item):
    url = reverse("evaluations:assessment_detail", args=[item.assessment.project_id, item.assessment.system])
    return redirect(f"{url}?level={item.level}&item={item.id}")


def _owned_item_or_404(request, item):
    project = item.assessment.project
    if not can_view_project(request.user, project):
        raise Http404("条目不存在。")
    return is_project_owner(request.user, project)


@login_required
@require_POST
def upload_evidence(request, item_id):
    item = get_object_or_404(CheckItem.objects.select_related("assessment__project"), pk=item_id)
    if not _owned_item_or_404(request, item):
        messages.error(request, "佐证材料由申报企业上传。")
        return _redirect_to_item(item)
    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        messages.error(request, "请选择要上传的佐证材料文件。")
        return _redirect_to_item(item)
    try:
        evidence = services.upload_evidence_file(item, request.user, uploaded_file, request.POST.get("description", ""))
        messages.success(request, f"佐证材料「{evidence.original_filename}」上传成功。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return _redirect_to_item(item)


@login_required
@require_POST
def delete_evidence(request, evidence_id):
    evidence = get_object_or_404(EvidenceFile.objects.select_related("check_item__assessment__project"), pk=evidence_id)
    item = evidence.check_item
    if not _owned_item_or_404(request, item):
        messages.error(request, "佐证材料只能由申报企业删除。")
        return _redirect_to_item(item)
    try:
        services.soft_delete_evidence(evidence, request.user)
        messages.success(request, f"佐证材料「{evidence.original_filename}」已删除（保留删除记录）。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return _redirect_to_item(item)


@login_required
def download_evidence(request, evidence_id):
    evidence = get_object_or_404(EvidenceFile.objects.select_related("check_item__assessment__project"), pk=evidence_id)
    if evidence.is_deleted or not can_view_project(request.user, evidence.check_item.assessment.project):
        raise Http404("佐证材料不存在。")
    return FileResponse(evidence.file.open("rb"), as_attachment=True, filename=evidence.original_filename)
