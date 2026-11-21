from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_project_name(apps, schema_editor):
    AuditLog = apps.get_model("audit", "AuditLog")
    EvaluationProject = apps.get_model("evaluations", "EvaluationProject")
    names = EvaluationProject.objects.filter(pk=OuterRef("project_id")).values("name")[:1]
    AuditLog.objects.filter(project__isnull=False).update(project_name=Subquery(names))


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
        ("evaluations", "0003_alter_evaluationproject_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="project_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(backfill_project_name, migrations.RunPython.noop),
    ]
