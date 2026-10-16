from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import admin_required, can_view_expert, is_admin
from apps.experts import services
from apps.experts.forms import ExpertForm, ExpertImportForm
from apps.experts.models import ExpertProfile
from apps.rules.models import Specialty


def _errors(exc):
    return "；".join(getattr(exc, "messages", [str(exc)]))


@admin_required
def expert_list(request):
    experts = ExpertProfile.objects.select_related("user", "specialty")
    specialty_code = request.GET.get("specialty", "")
    q = request.GET.get("q", "").strip()
    if specialty_code:
        experts = experts.filter(specialty__code=specialty_code)
    if q:
        # 专家标识存为 JSON（中文转义存储），关键词在内存中匹配；专家库规模在数百人以内
        experts = [
            e for e in experts if any(q in text for text in [e.name, e.organization, e.title, e.position, *(e.tags or [])])
        ]
    # 分组统计不沿用 Meta.ordering，显式排序
    specialties = Specialty.objects.annotate(n=Count("experts")).order_by("order", "id")
    for expert in experts:
        expert.phone_masked = services.mask_phone(expert.phone)
    return render(
        request,
        "experts/expert_list.html",
        {
            "experts": experts,
            "specialties": specialties,
            "specialty_code": specialty_code,
            "q": q,
            "total": ExpertProfile.objects.count(),
            "sample_count": ExpertProfile.objects.filter(is_sample=True).count(),
            "import_form": ExpertImportForm(),
            "active_nav": "experts",
        },
    )


@admin_required
def expert_create(request):
    form = ExpertForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            profile = services.create_expert(form.profile_data(), request.user, username=form.cleaned_data["username"])
        except ValidationError as exc:
            form.add_error(None, _errors(exc))
        else:
            messages.success(
                request, f"已开通评审专家账号 {profile.user.username}（初始密码 {services.INITIAL_PASSWORD}），请告知专家登录后修改密码。"
            )
            return redirect("experts:expert_detail", pk=profile.pk)
    return render(request, "experts/expert_form.html", {"form": form, "active_nav": "experts"})


@admin_required
def expert_edit(request, pk):
    profile = get_object_or_404(ExpertProfile.objects.select_related("user"), pk=pk)
    form = ExpertForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_expert(profile, form.profile_data(), request.user, is_active=form.cleaned_data["is_active"])
        except ValidationError as exc:
            form.add_error(None, _errors(exc))
        else:
            messages.success(request, f"专家「{profile.name}」的档案已保存。")
            return redirect("experts:expert_detail", pk=profile.pk)
    return render(request, "experts/expert_form.html", {"form": form, "profile": profile, "active_nav": "experts"})


@login_required
def expert_detail(request, pk):
    profile = get_object_or_404(ExpertProfile.objects.select_related("user", "specialty"), pk=pk)
    if not can_view_expert(request.user, profile):
        raise Http404("专家不存在。")
    admin = is_admin(request.user)
    from apps.reviews.models import ExpertAssignment, RemunerationRecord

    assignments = (
        ExpertAssignment.objects.filter(expert=profile.user, round__assessment__project__deleted_at__isnull=True)
        .select_related("round__assessment__project")
        .order_by("-assigned_at")
    )
    return render(
        request,
        "experts/expert_detail.html",
        {
            "assignments": assignments,
            "fees": RemunerationRecord.objects.filter(expert=profile.user),
            "profile": profile,
            "is_self": profile.user_id == request.user.id,
            "is_admin": admin,
            "active_nav": "experts" if admin else "profile",
        },
    )


@admin_required
@require_POST
def expert_import(request):
    form = ExpertImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, _errors(form.errors["file"]) if "file" in form.errors else "请选择要导入的文件。")
        return redirect("experts:expert_list")
    try:
        created, updated, errors = services.import_experts(form.cleaned_data["file"], request.user)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
        return redirect("experts:expert_list")
    text = f"导入完成：新增 {created} 位专家，更新 {updated} 位。"
    if errors:
        messages.error(request, text + f"另有 {len(errors)} 行未导入：" + "；".join(errors[:5]) + ("……" if len(errors) > 5 else ""))
    else:
        messages.success(request, text + f"新开通的账号初始密码为 {services.INITIAL_PASSWORD}。")
    return redirect("experts:expert_list")


# ---------------------------------------------------------------------------
# 专家劳务记录
# ---------------------------------------------------------------------------


def _fee_records(request):
    from apps.reviews.models import RemunerationRecord

    records = RemunerationRecord.objects.select_related("expert__expert_profile", "settled_by")
    status = request.GET.get("status", "")
    if status == "unsettled":
        records = records.filter(is_settled=False)
    elif status == "settled":
        records = records.filter(is_settled=True)
    expert_id = request.GET.get("expert", "")
    if expert_id.isdigit():
        records = records.filter(expert_id=int(expert_id))
    return records, status, expert_id


@admin_required
def fee_list(request):
    from django.db.models import Sum

    from apps.reviews.models import RemunerationRecord

    records, status, expert_id = _fee_records(request)
    all_records = RemunerationRecord.objects.all()
    totals = {
        "count": all_records.count(),
        "unsettled_count": all_records.filter(is_settled=False).count(),
        "unsettled_amount": all_records.filter(is_settled=False).aggregate(s=Sum("amount"))["s"] or 0,
        "settled_amount": all_records.filter(is_settled=True).aggregate(s=Sum("amount"))["s"] or 0,
    }
    experts = ExpertProfile.objects.filter(user__remunerations__isnull=False).distinct().order_by("name")
    return render(
        request,
        "experts/fee_list.html",
        {
            "records": records,
            "status": status,
            "expert_id": expert_id,
            "experts": experts,
            "totals": totals,
            "query": request.GET.urlencode(),
            "active_nav": "experts",
        },
    )


@admin_required
@require_POST
def fee_settle(request):
    from apps.reviews import fees

    try:
        count = fees.settle_records([int(i) for i in request.POST.getlist("ids") if i.isdigit()], request.user)
        messages.success(request, f"已将 {count} 条劳务记录标记为已结算，专家会收到站内私信。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(f"{reverse('experts:fee_list')}?{request.POST.get('query', '')}")


@admin_required
@require_POST
def fee_edit(request, record_id):
    from apps.reviews import fees
    from apps.reviews.models import RemunerationRecord

    record = get_object_or_404(RemunerationRecord, pk=record_id)
    try:
        fees.update_record(record, request.user, request.POST.get("amount"), request.POST.get("note"))
        messages.success(request, "劳务记录已更新。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _errors(exc))
    return redirect(f"{reverse('experts:fee_list')}?{request.POST.get('query', '')}")


@admin_required
def fee_export(request, fmt):
    from django.http import HttpResponse

    from apps.reviews import fees

    records, _, _ = _fee_records(request)
    stamp = timezone.localtime().strftime("%Y%m%d")
    if fmt == "xlsx":
        response = HttpResponse(
            fees.export_xlsx(records), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        filename = f"专家劳务记录_{stamp}.xlsx"
    else:
        response = HttpResponse(fees.export_csv(records), content_type="text/csv; charset=utf-8")
        filename = f"专家劳务记录_{stamp}.csv"
    from urllib.parse import quote

    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return response
