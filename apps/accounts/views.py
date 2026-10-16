from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts import captcha, services
from apps.accounts.forms import LoginForm, RegisterForm, ResetPasswordForm, SystemConfigForm, validate_password_strength
from apps.accounts.models import EnterpriseProfile, SystemConfig
from apps.accounts.permissions import admin_required, is_expert


class SystemLoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


def register(request):
    if request.user.is_authenticated:
        return redirect("evaluations:trl_list")
    if request.method == "POST":
        form = RegisterForm(request.POST, request=request)
        if form.is_valid():
            profile = services.register_enterprise(form.cleaned_data)
            request.session["registered_company"] = profile.company_name
            return redirect("accounts:register_done")
    else:
        form = RegisterForm(request=request)
    return render(request, "accounts/register.html", {"form": form})


def register_done(request):
    company = request.session.pop("registered_company", "")
    if not company:
        return redirect("accounts:login")
    return render(request, "accounts/register_done.html", {"company": company})


@never_cache
def captcha_image(request):
    answer = captcha.new_challenge(request)
    return HttpResponse(captcha.render_svg(answer), content_type="image/svg+xml")


class StrongPasswordChangeForm(PasswordChangeForm):
    def clean_new_password1(self):
        password = self.cleaned_data["new_password1"]
        validate_password_strength(password)
        return password


@login_required
def account(request):
    if request.method == "POST":
        form = StrongPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "密码已修改，下次登录请使用新密码。")
            return redirect("accounts:account")
    else:
        form = StrongPasswordChangeForm(request.user)
    return render(
        request,
        "accounts/account.html",
        {"form": form, "profile": getattr(request.user, "enterprise", None)},
    )


@admin_required
def user_list(request):
    status = request.GET.get("status", "")
    profiles = EnterpriseProfile.objects.select_related("user", "reviewed_by")
    counts = dict(profiles.values_list("status").annotate(n=Count("id")))
    if status in dict(EnterpriseProfile.STATUS_CHOICES):
        profiles = profiles.filter(status=status)
    # 待审核排在最前
    rows = sorted(profiles, key=lambda p: (p.status != EnterpriseProfile.STATUS_PENDING, -p.created_at.timestamp()))
    return render(
        request,
        "accounts/user_list.html",
        {
            "profiles": rows,
            "status_filter": status,
            "status_choices": EnterpriseProfile.STATUS_CHOICES,
            "counts": counts,
            "total": sum(counts.values()),
            "active_nav": "users",
        },
    )


@admin_required
@require_POST
def user_action(request, profile_id, action):
    profile = get_object_or_404(EnterpriseProfile.objects.select_related("user"), pk=profile_id)
    try:
        if action == "reset-password":
            form = ResetPasswordForm(request.POST)
            if not form.is_valid():
                raise ValidationError(list(form.errors["new_password"]))
            services.reset_password(profile, request.user, form.cleaned_data["new_password"])
            messages.success(request, f"已重置「{profile.company_name}」的登录密码，请将新密码告知企业联系人。")
        else:
            services.change_status(profile, request.user, action, request.POST.get("reason", ""))
            messages.success(request, f"已{services.ACTION_LABELS[action]}「{profile.company_name}」的账号。")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "；".join(getattr(exc, "messages", [str(exc)])))
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("accounts:user_list")


@admin_required
def system_settings(request):
    config = SystemConfig.get()
    if request.method == "POST":
        form = SystemConfigForm(request.POST, request.FILES, instance=config)
        if form.is_valid():
            services.save_system_config(
                form, request.user, form.cleaned_data.get("seal_upload"), form.cleaned_data.get("clear_seal")
            )
            messages.success(request, "系统设置已保存。新的期限只影响此后发起的评审和整改。")
            return redirect("accounts:settings")
    else:
        form = SystemConfigForm(instance=config)
    return render(request, "accounts/settings.html", {"form": form, "config": config, "active_nav": "settings"})


@admin_required
@never_cache
def seal_image(request):
    config = SystemConfig.get()
    if not config.seal_image:
        raise Http404("尚未上传印章图片。")
    return FileResponse(config.seal_image.open("rb"), content_type="image/png")


def home(request):
    """按角色进入首页：专家进入自己的评审任务，管理员和企业进入技术就绪度评价列表。"""
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if is_expert(request.user):
        return redirect("reviews:task_list")
    return redirect("evaluations:trl_list")
