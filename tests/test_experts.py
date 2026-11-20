"""第三轮：评审专家角色与专家库（档案、主页可见性、Excel 导入、样例专家）。"""

from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from apps.accounts.permissions import display_name, is_expert, role_label, visible_projects
from apps.audit.models import AuditLog
from apps.experts import services
from apps.experts.models import ExpertProfile
from apps.experts.sample_data import seed_sample_experts
from apps.notifications.models import Notification
from apps.rules.models import Specialty
from apps.rules.systems import TRL
from tests.test_core_flow import make_project


def make_expert(admin, name="测试专家", code="instrument", username="", **extra):
    data = {"name": name, "organization": "某研究院", "specialty": Specialty.objects.get(code=code), **extra}
    return services.create_expert(data, admin, username=username).user


@pytest.fixture
def expert(admin_user):
    return make_expert(admin_user, "甲专家", username="expa")


def test_specialties_seeded(db):
    assert Specialty.objects.count() >= 9
    assert Specialty.objects.filter(name="仪器仪表与检测技术").exists()
    assert Specialty.objects.filter(code="other", name="其他").exists()


def test_create_expert_role_and_notification(admin_user, expert, enterprise):
    assert is_expert(expert) and role_label(expert) == "评审专家" and display_name(expert) == "甲专家"
    assert not is_expert(enterprise) and not is_expert(admin_user)
    assert expert.check_password(services.INITIAL_PASSWORD)
    note = Notification.objects.get(recipient=expert)
    assert note.kind == "expert.create" and note.category == "task"
    assert AuditLog.objects.filter(action="expert.create").exists()
    # 专家不进入项目列表
    make_project(enterprise, targets={TRL: 1})
    assert not visible_projects(expert).exists()
    with pytest.raises(PermissionDenied):
        services.create_expert({"name": "x", "organization": "y", "specialty": Specialty.objects.first()}, enterprise)


def test_tags_limit_and_username_rules(admin_user):
    with pytest.raises(ValidationError):
        services.split_tags("a、b、c、d")
    with pytest.raises(ValidationError):
        make_expert(admin_user, "乙专家", tags=["a", "b", "c", "d"])
    with pytest.raises(ValidationError):
        make_expert(admin_user, "丙专家", username="1bad")
    first = make_expert(admin_user, "丁专家")
    assert first.username == "expert01"
    assert make_expert(admin_user, "戊专家").username == "expert02"


def test_library_and_profile_visibility(client, admin_user, expert, enterprise):
    profile = expert.expert_profile
    other = make_expert(admin_user, "乙专家", username="expb").expert_profile
    client.force_login(enterprise)
    assert client.get("/experts/").status_code == 403
    assert client.get(f"/experts/{profile.pk}/").status_code == 404
    assert "专家库" not in client.get("/trl/").content.decode()

    client.force_login(expert)
    assert client.get("/experts/").status_code == 403
    assert client.get(f"/experts/{profile.pk}/").status_code == 200
    assert client.get(f"/experts/{other.pk}/").status_code == 404
    # 专家首页是评审任务，访问项目列表被转走
    assert client.get("/").url == "/reviews/tasks/"
    assert client.get("/reviews/tasks/").status_code == 200
    assert client.get("/trl/").url == "/"
    assert client.get("/reports/").url == "/"
    assert client.get("/projects/new/").status_code == 403

    client.force_login(admin_user)
    page = client.get("/experts/").content.decode()
    assert "甲专家" in page and "乙专家" in page
    assert "甲专家" in client.get("/experts/?q=甲").content.decode()
    assert "乙专家" not in client.get("/experts/?q=甲").content.decode()
    assert client.get(f"/experts/{profile.pk}/").status_code == 200


def test_edit_expert_and_disable(client, admin_user, expert):
    profile = expert.expert_profile
    client.force_login(admin_user)
    response = client.post(
        f"/experts/{profile.pk}/edit/",
        {
            "name": "甲专家",
            "organization": "新单位",
            "title": "研究员",
            "position": "",
            "specialty": Specialty.objects.get(code="sensor").pk,
            "in_service": "on",
            "tags_text": "传感器、可靠性",
            "phone": "",
            "email": "",
            "website": "",
            "bio": "简介",
            "research": "",
            "achievements": "成果一\n成果二",
            "experience": "",
        },
    )
    assert response.status_code == 302
    profile.refresh_from_db()
    expert.refresh_from_db()
    assert profile.organization == "新单位" and profile.tags == ["传感器", "可靠性"]
    assert profile.has_homepage and profile.achievement_lines == ["成果一", "成果二"]
    # 未勾选“账号启用”即停用
    assert not expert.is_active and not profile.is_available
    assert AuditLog.objects.filter(action="expert.update").exists()


def _xlsx(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def test_import_experts(client, admin_user, expert):
    data = _xlsx(
        [
            ["姓名", "工作单位", "职称", "专业领域", "专家标签1", "专家标签2", "是否在职", "邮箱", "代表成果"],
            ["甲专家", "某研究院", "研究员", "仪器仪表与检测技术", "在线检测", "", "是", "", ""],
            ["新专家", "新单位", "教授", "传感器与敏感元件", "MEMS", "可靠性", "否", "new@example.com", "成果A"],
            ["错领域", "某单位", "", "不存在的领域", "", "", "", "", ""],
            ["", "", "", "", "", "", "", "", ""],
        ]
    )
    created, updated, errors = services.import_experts(data, admin_user)
    assert (created, updated) == (1, 1)
    assert len(errors) == 1 and "第 4 行" in errors[0]
    new = ExpertProfile.objects.get(name="新专家")
    assert new.tags == ["MEMS", "可靠性"] and not new.in_service and new.user.username.startswith("expert")
    assert ExpertProfile.objects.get(name="甲专家").title == "研究员"

    client.force_login(admin_user)
    upload = SimpleUploadedFile("experts.xlsx", _xlsx([["姓名", "工作单位", "专业领域"], ["网页导入", "某所", "光电与激光技术"]]).read())
    response = client.post("/experts/import/", {"file": upload})
    assert response.status_code == 302 and ExpertProfile.objects.filter(name="网页导入").exists()
    bad = SimpleUploadedFile("experts.csv", b"a,b")
    client.post("/experts/import/", {"file": bad})
    assert not ExpertProfile.objects.filter(organization="b").exists()


def test_sample_experts(admin_user):
    assert seed_sample_experts(admin_user) == 20
    assert seed_sample_experts(admin_user) == 0
    profiles = ExpertProfile.objects.all()
    assert profiles.count() == 20 and all(p.is_sample for p in profiles)
    assert sum(1 for p in profiles if p.has_homepage) == 10
    assert User.objects.filter(username="expert20").exists()
    # 联系方式脱敏、邮箱为 example.com
    assert all("****" in p.phone and p.email.endswith("@example.com") for p in profiles)
    assert services.mask_phone("13812345678") == "138****5678"


def test_expert_pages_have_no_forbidden_wording(client, admin_user):
    from tests.test_accounts_access import FORBIDDEN_WORDS

    seed_sample_experts(admin_user)
    profile = ExpertProfile.objects.filter(bio__gt="").first()
    expert = profile.user
    client.force_login(admin_user)
    for url in ("/experts/", "/experts/new/", f"/experts/{profile.pk}/", f"/experts/{profile.pk}/edit/"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert not FORBIDDEN_WORDS.search(response.content.decode()), url
    client.force_login(expert)
    for url in (f"/experts/{profile.pk}/", "/account/", "/inbox/", "/rules/process/"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert not FORBIDDEN_WORDS.search(response.content.decode()), url
