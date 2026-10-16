"""第三轮：系统设置（印章图片、期限、劳务费）。"""

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import SystemConfig
from apps.audit.models import AuditLog

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _post(client, **extra):
    data = {"expert_review_days": "7", "rectify_days": "14", "expert_fee_default": "800"}
    data.update(extra)
    return client.post("/settings/", data)


def test_settings_admin_only(client, enterprise, admin_user):
    client.force_login(enterprise)
    assert client.get("/settings/").status_code == 403
    assert client.get("/settings/seal.png").status_code == 403
    client.force_login(admin_user)
    assert client.get("/settings/").status_code == 200
    assert client.get("/settings/seal.png").status_code == 404


def test_settings_validation_and_seal(client, admin_user):
    client.force_login(admin_user)
    response = _post(client, expert_review_days="0")
    assert response.status_code == 200 and "1～60" in response.content.decode()

    fake = SimpleUploadedFile("seal.png", b"not a png", content_type="image/png")
    response = _post(client, seal_upload=fake)
    assert "PNG 格式" in response.content.decode()
    assert not SystemConfig.get().seal_image

    response = _post(client, seal_upload=SimpleUploadedFile("seal.png", PNG_1PX, content_type="image/png"))
    assert response.status_code == 302
    config = SystemConfig.get()
    assert config.seal_image and config.expert_fee_default == 800
    assert client.get("/settings/seal.png").status_code == 200
    assert AuditLog.objects.filter(action="config.update").count() == 1

    _post(client, clear_seal="1")
    assert not SystemConfig.get().seal_image
