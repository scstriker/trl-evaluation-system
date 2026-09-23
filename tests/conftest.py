import tempfile

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command

from apps.accounts.forms import credit_code_check_char
from apps.accounts.models import EnterpriseProfile
from apps.rules.models import RuleItem
from apps.rules.systems import MRL


@pytest.fixture(autouse=True)
def _sandbox_media(settings):
    settings.MEDIA_ROOT = tempfile.mkdtemp(prefix="trl-test-media-")


@pytest.fixture
def rules(db):
    call_command("import_rules", verbosity=0)


@pytest.fixture
def mrl_rules(rules):
    """仅用于测试的制造成熟度细则（正式细则由评价机构提供后导入）。"""
    for level in range(1, 11):
        for seq in (1, 2):
            RuleItem.objects.create(
                system=MRL, level=level, track=RuleItem.TRACK_GENERAL, seq=seq, name=f"测试条件 {level}-{seq}", evidence="测试材料"
            )


def make_enterprise(username, company, index, status=EnterpriseProfile.STATUS_ACTIVE):
    user = User.objects.create_user(username, password="Passw0rd!", is_active=status == EnterpriseProfile.STATUS_ACTIVE)
    code17 = f"91110000MA{index:07d}"
    EnterpriseProfile.objects.create(
        user=user,
        company_name=company,
        credit_code=code17 + credit_code_check_char(code17),
        contact_name="联系人",
        phone="13800000000",
        status=status,
    )
    return user


@pytest.fixture
def admin_user(rules):
    return User.objects.create_user("boss", password="Passw0rd!", is_staff=True)


@pytest.fixture
def enterprise(rules):
    return make_enterprise("alpha", "甲企业有限公司", 1)


@pytest.fixture
def other_enterprise(rules):
    return make_enterprise("beta", "乙企业有限公司", 2)
