import re

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts import captcha
from apps.accounts.forms import credit_code_check_char, is_valid_credit_code
from apps.accounts.models import EnterpriseProfile
from apps.evaluations.models import EvaluationProject
from apps.evidence.services import upload_evidence_file
from apps.rules.systems import TRL
from tests.test_core_flow import make_project

CODE17 = "91440300MA5FUTLL0"
VALID_CODE = CODE17 + credit_code_check_char(CODE17)
WRONG_CODE = CODE17 + ("0" if VALID_CODE[-1] != "0" else "1")


def _register_payload(client, **overrides):
    client.get("/captcha.svg")
    answer = client.session[captcha.SESSION_KEY]["answer"]
    payload = {
        "username": "gamma01",
        "contact_name": "张三",
        "password1": "Abcd1234",
        "password2": "Abcd1234",
        "company_name": "丙科技有限公司",
        "credit_code": VALID_CODE,
        "phone": "13912345678",
        "captcha": answer.lower(),
    }
    payload.update(overrides)
    return payload


def test_credit_code_checksum():
    assert is_valid_credit_code(VALID_CODE)
    assert not is_valid_credit_code(WRONG_CODE)
    assert not is_valid_credit_code("123")


def test_register_then_pending_login_then_approve(client, rules, admin_user):
    response = client.post("/register/", _register_payload(client))
    assert response.status_code == 302 and response.url == "/register/done/"
    profile = EnterpriseProfile.objects.get(user__username="gamma01")
    assert profile.status == EnterpriseProfile.STATUS_PENDING
    assert profile.user.is_active is False

    response = client.post("/login/", {"username": "gamma01", "password": "Abcd1234"})
    assert "账号正在审核中" in response.content.decode()
    # 密码错误时不暴露账号状态
    response = client.post("/login/", {"username": "gamma01", "password": "wrong-pass1"})
    assert "用户名或密码不正确" in response.content.decode()

    client.force_login(admin_user)
    client.post(f"/users/{profile.pk}/approve/")
    client.logout()
    response = client.post("/login/", {"username": "gamma01", "password": "Abcd1234"})
    assert response.status_code == 302


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("username", "1abc", "英文字母开头"),
        ("phone", "12345", "手机号码"),
        ("credit_code", WRONG_CODE, "统一社会信用代码"),
        ("password2", "Other123", "不一致"),
        ("password1", "abcdefgh", "字母和数字"),
        ("captcha", "zzzz", "验证码"),
    ],
)
def test_register_validation(client, rules, field, value, message):
    payload = _register_payload(client, **{field: value})
    response = client.post("/register/", payload)
    assert response.status_code == 200
    assert message in response.content.decode()
    assert not User.objects.filter(username="gamma01").exists()


def test_duplicate_credit_code_rejected(client, enterprise):
    code = enterprise.enterprise.credit_code
    response = client.post("/register/", _register_payload(client, credit_code=code))
    assert "只能注册一个账号" in response.content.decode()


def test_reject_needs_reason_and_is_shown_at_login(client, rules, admin_user):
    client.post("/register/", _register_payload(client))
    profile = EnterpriseProfile.objects.get(user__username="gamma01")
    client.force_login(admin_user)
    client.post(f"/users/{profile.pk}/reject/", {"reason": ""})
    profile.refresh_from_db()
    assert profile.status == EnterpriseProfile.STATUS_PENDING
    client.post(f"/users/{profile.pk}/reject/", {"reason": "信用代码与企业名称不符"})
    client.logout()
    response = client.post("/login/", {"username": "gamma01", "password": "Abcd1234"})
    assert "信用代码与企业名称不符" in response.content.decode()


def test_disabled_user_session_is_dropped(client, enterprise, admin_user):
    client.force_login(enterprise)
    assert client.get("/trl/").status_code == 200
    from apps.accounts.services import change_status

    change_status(enterprise.enterprise, admin_user, "disable")
    assert client.get("/trl/").status_code == 302  # 被踢回登录页


def test_enterprise_isolation(client, enterprise, other_enterprise, admin_user):
    project = make_project(enterprise)
    assessment = project.get_assessment(TRL)
    item = assessment.items.filter(level=1).first()
    evidence = upload_evidence_file(item, enterprise, SimpleUploadedFile("a.pdf", b"x"))

    client.force_login(other_enterprise)
    assert project.name not in client.get("/trl/").content.decode()
    for url in [
        f"/projects/{project.id}/",
        f"/projects/{project.id}/trl/",
        f"/reports/projects/{project.id}/",
        f"/evidence/{evidence.id}/download/",
    ]:
        assert client.get(url).status_code == 404, url
    assert client.post(f"/items/{item.id}/evaluate/", {"result": "satisfied", "statement": "x"}).status_code == 404
    assert client.post(f"/projects/{project.id}/trl/submit/", {"up_to": 1, "confirm": "1"}).status_code == 404

    client.force_login(admin_user)
    assert client.get(f"/projects/{project.id}/trl/").status_code == 200
    assert project.name in client.get("/trl/").content.decode()


def test_admin_only_pages(client, enterprise, admin_user):
    client.force_login(enterprise)
    for url in ["/rules/trl/", "/rules/mrl/", "/users/", "/audit/"]:
        assert client.get(url).status_code == 403, url
    assert client.get("/rules/process/").status_code == 200
    client.force_login(admin_user)
    for url in ["/rules/trl/", "/rules/mrl/", "/users/", "/audit/", "/rules/process/"]:
        assert client.get(url).status_code == 200, url
    # 管理员不申报项目
    assert client.get("/projects/new/").status_code == 403


# 细则原文里有“测试用例”“交付用户试用”等正常表述，因此只查“试用版”
FORBIDDEN_WORDS = re.compile(r"Demo|demo|演示|试用版|预留|工作台|轨道|物化|快照|观察员|评价员|审核员")


def test_pages_render_without_forbidden_wording(client, enterprise, admin_user, mrl_rules):
    project = make_project(enterprise, targets={TRL: 2})
    pages_public = ["/login/", "/register/"]
    for url in pages_public:
        response = client.get(url)
        assert response.status_code == 200
        assert not FORBIDDEN_WORDS.search(response.content.decode()), url

    enterprise_pages = [
        "/trl/",
        "/mrl/",
        "/projects/new/",
        "/projects/new/?system=mrl",
        f"/projects/{project.id}/trl/",
        f"/projects/{project.id}/trl/?level=2",
        f"/projects/{project.id}/mrl/",
        "/reports/",
        f"/reports/projects/{project.id}/",
        "/rules/process/",
        "/account/",
    ]
    admin_pages = enterprise_pages[:2] + enterprise_pages[4:] + ["/rules/trl/", "/rules/mrl/", "/users/", "/audit/"]
    for user, pages in ((enterprise, enterprise_pages), (admin_user, admin_pages)):
        client.force_login(user)
        for url in pages:
            response = client.get(url)
            assert response.status_code == 200, (user.username, url)
            found = FORBIDDEN_WORDS.search(response.content.decode())
            assert not found, (user.username, url, found and found.group(0))


def test_create_project_via_form(client, enterprise, mrl_rules):
    client.force_login(enterprise)
    response = client.post(
        "/projects/new/",
        {
            "name": "新项目",
            "domain": "",
            "tech_type": "hardware",
            "include_trl": "on",
            "trl_target": "3",
            "include_mrl": "on",
            "mrl_target": "2",
            "remark": "",
        },
    )
    assert response.status_code == 302
    project = EvaluationProject.objects.get(name="新项目")
    assert project.owner == enterprise
    assert project.applicant == "甲企业有限公司"
    assert {a.system for a in project.assessments.all()} == {"trl", "mrl"}


def test_create_project_requires_a_system(client, enterprise):
    client.force_login(enterprise)
    response = client.post("/projects/new/", {"name": "空项目", "tech_type": "hardware", "trl_target": "3"})
    assert response.status_code == 200
    assert "请至少选择一项评价内容" in response.content.decode()
