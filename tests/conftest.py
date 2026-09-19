import tempfile

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command

from apps.accounts.permissions import ROLE_AUDITOR, ROLE_EVALUATOR


@pytest.fixture(autouse=True)
def _sandbox_media(settings):
    settings.MEDIA_ROOT = tempfile.mkdtemp(prefix="trl-test-media-")


@pytest.fixture
def rules(db):
    call_command("init_roles", verbosity=0)
    call_command("import_trl_rules", verbosity=0)


@pytest.fixture
def evaluator(rules):
    user = User.objects.create_user("eva", password="pw")
    user.groups.add(Group.objects.get(name=ROLE_EVALUATOR))
    return user


@pytest.fixture
def auditor(rules):
    user = User.objects.create_user("aud", password="pw")
    user.groups.add(Group.objects.get(name=ROLE_AUDITOR))
    return user
