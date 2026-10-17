from django.db import migrations
from django.utils import timezone


def close_legacy_expert_rounds(apps, schema_editor):
    """评审方式改为管理员决定：旧“企业申报专家评审”方式下未结束的轮次标为“流程调整，已撤销”，
    并撤销未完成的专家任务。已写入的逐级审核记录保留，企业此后照常提交审核。"""
    ReviewRound = apps.get_model("reviews", "ReviewRound")
    ExpertAssignment = apps.get_model("reviews", "ExpertAssignment")
    now = timezone.now()
    legacy = ReviewRound.objects.filter(assessment__review_mode="expert").exclude(status="closed")
    ExpertAssignment.objects.filter(round__in=legacy, status__in=["invited", "accepted"]).update(
        status="cancelled", cancelled_at=now, cancel_reason="评审流程调整，任务已撤销"
    )
    legacy.update(status="closed", outcome="cancelled")


class Migration(migrations.Migration):

    dependencies = [
        ("evaluations", "0005_assessment_review_mode_levelreview_source"),
        ("reviews", "0002_outcome_cancelled"),
    ]

    operations = [
        migrations.RunPython(close_legacy_expert_rounds, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="assessment",
            name="review_mode",
        ),
    ]
