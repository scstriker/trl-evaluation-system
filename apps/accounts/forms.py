import re

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User

from apps.accounts import captcha
from apps.accounts.models import EnterpriseProfile

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,19}$")
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

# GB 32100-2015《法人和其他组织统一社会信用代码编码规则》
CREDIT_CODE_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
CREDIT_CODE_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def credit_code_check_char(first17):
    total = sum(CREDIT_CODE_CHARS.index(ch) * weight for ch, weight in zip(first17, CREDIT_CODE_WEIGHTS))
    return CREDIT_CODE_CHARS[(31 - total % 31) % 31]


def is_valid_credit_code(code):
    if len(code) != 18 or any(ch not in CREDIT_CODE_CHARS for ch in code):
        return False
    return credit_code_check_char(code[:17]) == code[17]


def validate_password_strength(password):
    if len(password) < 8:
        raise forms.ValidationError("密码至少 8 位。")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise forms.ValidationError("密码需同时包含字母和数字。")


STATUS_LOGIN_MESSAGES = {
    EnterpriseProfile.STATUS_PENDING: "账号正在审核中，评价机构审核通过后即可登录。",
    EnterpriseProfile.STATUS_DISABLED: "账号已停用，如需恢复请联系评价机构。",
}


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "用户名或密码不正确，请重新输入。",
        "inactive": "账号当前不可用，请联系评价机构。",
    }

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username is not None and password:
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                # 密码正确但账号未启用时，明确告知状态（密码错误时不暴露账号是否存在）
                user = User.objects.filter(username=username).select_related("enterprise").first()
                if user and not user.is_active and user.check_password(password):
                    raise forms.ValidationError(self._inactive_message(user), code="inactive")
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data

    def _inactive_message(self, user):
        profile = getattr(user, "enterprise", None)
        if profile is None:
            return self.error_messages["inactive"]
        if profile.status == EnterpriseProfile.STATUS_REJECTED:
            reason = f"：{profile.reject_reason}" if profile.reject_reason else ""
            return f"注册申请未通过审核{reason}。如有疑问请联系评价机构。"
        return STATUS_LOGIN_MESSAGES.get(profile.status, self.error_messages["inactive"])


class RegisterForm(forms.Form):
    username = forms.CharField(label="用户名", max_length=20)
    contact_name = forms.CharField(label="联系人姓名", max_length=64)
    password1 = forms.CharField(label="设置登录密码", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label="确认登录密码", widget=forms.PasswordInput, strip=False)
    company_name = forms.CharField(label="企业中文全称", max_length=255)
    credit_code = forms.CharField(label="统一社会信用代码", max_length=18)
    phone = forms.CharField(label="手机号码", max_length=11)
    captcha = forms.CharField(label="验证码", max_length=8)

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not USERNAME_RE.match(username):
            raise forms.ValidationError("用户名须以英文字母开头，4~20 位字母、数字或下划线。")
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("该用户名已被注册，请更换。")
        return username

    def clean_contact_name(self):
        return self.cleaned_data["contact_name"].strip()

    def clean_company_name(self):
        return self.cleaned_data["company_name"].strip()

    def clean_credit_code(self):
        code = self.cleaned_data["credit_code"].strip().upper()
        if not is_valid_credit_code(code):
            raise forms.ValidationError("统一社会信用代码格式不正确（18 位，含校验位），请核对营业执照。")
        if EnterpriseProfile.objects.filter(credit_code=code).exists():
            raise forms.ValidationError("该统一社会信用代码已注册账号。每家企业只能注册一个账号，如需找回请联系评价机构。")
        return code

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if not PHONE_RE.match(phone):
            raise forms.ValidationError("请输入 11 位中国大陆手机号码。")
        return phone

    def clean_password1(self):
        password = self.cleaned_data["password1"]
        validate_password_strength(password)
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") and cleaned.get("password2") and cleaned["password1"] != cleaned["password2"]:
            self.add_error("password2", "两次输入的密码不一致。")
        # 验证码最后校验并立即作废：任何错误都需刷新重填
        if not captcha.verify(self.request, cleaned.get("captcha")):
            self.add_error("captcha", "验证码不正确或已过期，请重新输入。")
        return cleaned


class ResetPasswordForm(forms.Form):
    new_password = forms.CharField(label="新密码", strip=False)

    def clean_new_password(self):
        password = self.cleaned_data["new_password"]
        validate_password_strength(password)
        return password
