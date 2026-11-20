# Generated manually

import apps.reports.models
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("evaluations", "0006_remove_review_mode"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("reports", "0003_front_matter_and_pdf"),
    ]

    operations = [
        migrations.CreateModel(
            name="EnterpriseProjectFile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("support", "评价支撑信息"),
                            ("overview", "项目概况"),
                            ("wbst", "附录A WBST 表"),
                        ],
                        max_length=16,
                    ),
                ),
                ("file", models.FileField(upload_to=apps.reports.models.enterprise_file_path)),
                ("original_filename", models.CharField(max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="enterprise_files",
                        to="evaluations.evaluationproject",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="enterprise_uploads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-uploaded_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="enterpriseprojectfile",
            constraint=models.UniqueConstraint(fields=("project", "kind"), name="unique_enterprise_file_per_kind"),
        ),
    ]