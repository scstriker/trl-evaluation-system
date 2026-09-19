import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.evaluations import services
from apps.evaluations.models import CheckItem, EvaluationProject
from apps.evidence.models import EvidenceFile
from apps.evidence.services import soft_delete_evidence, upload_evidence_file
from apps.reports.services import generate_report
from apps.rules.models import MaturityLevel, MrlLevel, RuleItem


def _project(user, track=EvaluationProject.TRACK_EQUIPMENT, target=2):
    project = EvaluationProject.objects.create(
        name="测试项目", applicant="测试单位", track=track, target_level=target, created_by=user
    )
    services.generate_check_items(project)
    return project


def _judge_all(project, level, actor):
    for item in project.items.filter(level=level):
        services.evaluate_check_item(item, actor, {"result": "satisfied", "statement": "已核实"})


def test_rule_seed_counts(rules):
    assert MaturityLevel.objects.count() == 9
    assert RuleItem.objects.count() == 77
    assert RuleItem.objects.filter(track="hw").count() == 44
    assert RuleItem.objects.filter(track="sw").count() == 33
    assert MrlLevel.objects.count() == 10


@pytest.mark.parametrize(
    "track,expected",
    [("equipment", 77), ("hardware", 44), ("software", 33)],
)
def test_generate_check_items_by_track(evaluator, track, expected):
    project = _project(evaluator, track=track)
    assert project.items.count() == expected
    assert project.items.filter(result=CheckItem.RESULT_UNEVALUATED).count() == expected


def test_evaluation_requires_statement(evaluator):
    project = _project(evaluator)
    item = project.items.filter(level=1).first()
    with pytest.raises(ValidationError):
        services.evaluate_check_item(item, evaluator, {"result": "satisfied", "statement": ""})
    with pytest.raises(ValidationError):
        services.evaluate_check_item(item, evaluator, {"result": "bogus", "statement": "x"})
    services.evaluate_check_item(item, evaluator, {"result": "not_applicable", "statement": "理由"})
    item.refresh_from_db()
    assert item.result == "not_applicable"
    assert project.audit_logs.filter(action="item.evaluate").count() == 1


def test_locked_level_cannot_be_evaluated(evaluator):
    project = _project(evaluator)
    item = project.items.filter(level=2).first()
    with pytest.raises(ValidationError):
        services.evaluate_check_item(item, evaluator, {"result": "satisfied", "statement": "x"})


def test_pass_level_requires_all_judged_and_unlocks_next(evaluator):
    project = _project(evaluator)
    with pytest.raises(ValidationError):
        services.pass_level(project, 1, evaluator)
    _judge_all(project, 1, evaluator)
    services.pass_level(project, 1, evaluator)
    project.refresh_from_db()
    assert project.unlocked_level == 2
    assert services.get_achieved_level(project) == 1
    # 已通过级别的结论冻结
    with pytest.raises(ValidationError):
        services.evaluate_check_item(
            project.items.filter(level=1).first(), evaluator, {"result": "satisfied", "statement": "改"}
        )


def test_report_blocked_until_target_reached_then_versions_increment(evaluator, auditor):
    project = _project(evaluator, target=2)
    _judge_all(project, 1, evaluator)
    services.pass_level(project, 1, evaluator)
    with pytest.raises(ValidationError):
        generate_report(project, auditor)
    _judge_all(project, 2, evaluator)
    services.pass_level(project, 2, evaluator)

    report1 = generate_report(project, auditor)
    project.refresh_from_db()
    assert report1.version == 1
    assert project.status == EvaluationProject.STATUS_REPORTED
    assert report1.file.name.endswith(".docx")
    assert len(report1.sha256) == 64

    services.unlock_project(project, auditor)
    report2 = generate_report(project, auditor)
    assert report2.version == 2


def test_evidence_hash_and_soft_delete(evaluator):
    project = _project(evaluator)
    item = project.items.filter(level=1).first()
    upload = SimpleUploadedFile("任务书.pdf", b"demo-bytes", content_type="application/pdf")
    evidence = upload_evidence_file(item, evaluator, upload, "说明")
    assert len(evidence.sha256) == 64
    assert item.active_evidence_count == 1

    with pytest.raises(ValidationError):
        upload_evidence_file(item, evaluator, SimpleUploadedFile("bad.exe", b"x"))

    soft_delete_evidence(evidence, evaluator)
    evidence.refresh_from_db()
    assert evidence.is_deleted is True
    assert EvidenceFile.objects.count() == 1
    assert item.active_evidence_count == 0


def test_pages_render(client, evaluator, auditor):
    project = _project(evaluator)
    client.force_login(evaluator)
    for url in [
        "/projects/",
        "/projects/new/",
        f"/projects/{project.id}/",
        f"/projects/{project.id}/?level=1",
        "/rules/",
        "/rules/process/",
        "/rules/mrl/",
        "/reports/",
        f"/reports/projects/{project.id}/",
    ]:
        response = client.get(url)
        assert response.status_code == 200, url
    client.force_login(auditor)
    assert client.get("/audit/").status_code == 200
