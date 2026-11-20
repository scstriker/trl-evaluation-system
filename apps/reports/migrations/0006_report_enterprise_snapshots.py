import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0005_report_wbst_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="overview_file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="bound_report_overviews",
                to="reports.enterpriseprojectfile",
                verbose_name="归档项目概况",
            ),
        ),
        migrations.AddField(
            model_name="report",
            name="support_file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="bound_report_supports",
                to="reports.enterpriseprojectfile",
                verbose_name="归档评价支撑信息",
            ),
        ),
    ]