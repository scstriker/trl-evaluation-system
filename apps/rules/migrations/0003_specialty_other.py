# Generated manually for fourth revision

from django.db import migrations


def add_other_specialty(apps, schema_editor):
    Specialty = apps.get_model("rules", "Specialty")
    Specialty.objects.get_or_create(code="other", defaults={"name": "其他", "order": 0})


class Migration(migrations.Migration):

    dependencies = [
        ("rules", "0002_specialty"),
    ]

    operations = [
        migrations.RunPython(add_other_specialty, migrations.RunPython.noop),
    ]