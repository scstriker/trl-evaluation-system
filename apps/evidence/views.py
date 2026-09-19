from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import can_modify_project, can_view_project
from apps.evaluations.models import CheckItem
from apps.evidence import services
from apps.evidence.models import EvidenceFile


def _redirect_to_item(item):
    url = reverse("evaluations:project_detail", args=[item.project_id])
    return redirect(f"{url}?level={item.level}&item={item.id}")


@login_required
@require_POST
def upload_evidence(request, item_id):
    item = get_object_or_404(CheckItem.objects.select_related("project"), pk=item_id)
    if not can_modify_project(request.user, item.project):
        messages.error(request, "当前项目不可编辑或您没有上传权限。")
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
    evidence = get_object_or_404(EvidenceFile.objects.select_related("check_item__project"), pk=evidence_id)
    item = evidence.check_item
    if not can_modify_project(request.user, item.project):
        messages.error(request, "当前项目不可编辑或您没有删除权限。")
        return _redirect_to_item(item)
    try:
        services.soft_delete_evidence(evidence, request.user)
        messages.success(request, f"佐证材料「{evidence.original_filename}」已删除（软删除留痕）。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return _redirect_to_item(item)


@login_required
def download_evidence(request, evidence_id):
    evidence = get_object_or_404(EvidenceFile.objects.select_related("check_item__project"), pk=evidence_id)
    if not can_view_project(request.user, evidence.check_item.project):
        raise PermissionDenied("当前用户不能下载该佐证材料。")
    return FileResponse(evidence.file.open("rb"), as_attachment=True, filename=evidence.original_filename)
