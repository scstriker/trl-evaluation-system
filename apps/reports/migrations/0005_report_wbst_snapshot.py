# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0004_enterpriseprojectfile"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="enterpriseprojectfile",
            name="unique_enterprise_file_per_kind",
        ),
        migrations.AddField(
            model_name="report",
            name="wbst_file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="bound_reports",
                to="reports.enterpriseprojectfile",
                verbose_name="归档 WBST",
            ),
        ),
    ]