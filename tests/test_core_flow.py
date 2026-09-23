import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.evaluations import services
from apps.evaluations.models import CheckItem, EvaluationProject, LevelReview
from apps.evidence.models import EvidenceFile
from apps.evidence.services import soft_delete_evidence, upload_evidence_file
from apps.rules.models import LevelDefinition, RuleItem
from apps.rules.systems import MRL, TRL


def make_project(owner, tech_type=EvaluationProject.TECH_EQUIPMENT, targets=None):
    project = EvaluationProject(name="测试项目", applicant=owner.enterprise.company_name, tech_type=tech_type)
    services.create_project(project, owner, targets or {TRL: 2})
    return project


def fill(assessment, level, actor, result="satisfied"):
    for item in assessment.items.filter(level=level):
        services.evaluate_check_item(item, actor, {"result": result, "statement": "已核实"})


def test_rule_seed_counts(rules):
    assert LevelDefinition.objects.filter(system=TRL).count() == 9
    assert LevelDefinition.objects.filter(system=MRL).count() == 10
    assert RuleItem.objects.filter(system=TRL).count() == 77
    assert RuleItem.objects.filter(system=TRL, track="hw").count() == 44
    assert RuleItem.objects.filter(system=TRL, track="sw").count() == 33
    # 制造成熟度正式细则待评价机构提供，出厂时只有等级定义
    assert RuleItem.objects.filter(system=MRL).count() == 0


@pytest.mark.parametrize("tech_type,expected", [("equipment", 77), ("hardware", 44), ("software", 33)])
def test_items_generated_by_tech_type(enterprise, tech_type, expected):
    assessment = make_project(enterprise, tech_type=tech_type).get_assessment(TRL)
    assert assessment.items.count() == expected
    assert assessment.items.filter(result=CheckItem.RESULT_UNEVALUATED).count() == expected


def test_mrl_requires_imported_rules(enterprise):
    with pytest.raises(ValidationError):
        make_project(enterprise, targets={MRL: 3})


def test_mrl_assessment_with_rules(enterprise, mrl_rules):
    project = make_project(enterprise, targets={TRL: 2, MRL: 3})
    assert project.get_assessment(MRL).items.count() == 20
    assert project.get_assessment(MRL).items.first().code == "MRL1-1"


def test_self_assessment_requires_statement_and_owner(enterprise, other_enterprise, admin_user):
    assessment = make_project(enterprise).get_assessment(TRL)
    item = assessment.items.filter(level=1).first()
    with pytest.raises(ValidationError):
        services.evaluate_check_item(item, enterprise, {"result": "satisfied", "statement": ""})
    with pytest.raises(ValidationError):
        services.evaluate_check_item(item, enterprise, {"result": "bogus", "statement": "x"})
    for outsider in (other_enterprise, admin_user):
        with pytest.raises(PermissionDenied):
            services.evaluate_check_item(item, outsider, {"result": "satisfied", "statement": "x"})
    services.evaluate_check_item(item, enterprise, {"result": "not_applicable", "statement": "理由"})
    item.refresh_from_db()
    assert item.result == "not_applicable"


def test_any_level_within_target_is_editable_but_not_beyond(enterprise):
    assessment = make_project(enterprise, targets={TRL: 2}).get_assessment(TRL)
    services.evaluate_check_item(assessment.items.filter(level=2).first(), enterprise, {"result": "satisfied", "statement": "x"})
    with pytest.raises(ValidationError):
        services.evaluate_check_item(assessment.items.filter(level=3).first(), enterprise, {"result": "satisfied", "statement": "x"})


def test_submit_requires_complete_levels_and_contiguity(enterprise):
    assessment = make_project(enterprise, targets={TRL: 3}).get_assessment(TRL)
    with pytest.raises(ValidationError):
        services.submit_levels(assessment, enterprise, 1)
    fill(assessment, 2, enterprise)
    # 第 1 级未填完，不能跳过它提交第 2 级
    with pytest.raises(ValidationError):
        services.submit_levels(assessment, enterprise, 2)
    fill(assessment, 1, enterprise)
    assert services.submit_levels(assessment, enterprise, 1) == [1]
    # 已提交的级别冻结
    with pytest.raises(ValidationError):
        services.evaluate_check_item(assessment.items.filter(level=1).first(), enterprise, {"result": "satisfied", "statement": "改"})
    with pytest.raises(ValidationError):
        upload_evidence_file(assessment.items.filter(level=1).first(), enterprise, SimpleUploadedFile("a.pdf", b"x"))


def test_batch_submit_to_target_and_ordered_pass(enterprise, admin_user):
    assessment = make_project(enterprise, targets={TRL: 3}).get_assessment(TRL)
    for level in (1, 2, 3):
        fill(assessment, level, enterprise)
    assert services.submit_levels(assessment, enterprise, 3) == [1, 2, 3]
    assert services.get_pass_plan(assessment) == [1, 2, 3]
    with pytest.raises(PermissionDenied):
        services.pass_levels(assessment, enterprise, 1)
    assert services.pass_levels(assessment, admin_user, 2) == [1, 2]
    assert services.get_achieved_level(assessment) == 2
    assert services.pass_levels(assessment, admin_user, 3) == [3]
    assert services.get_achieved_level(assessment) == 3


def test_return_requires_comment_and_reopens_editing(enterprise, admin_user):
    assessment = make_project(enterprise, targets={TRL: 2}).get_assessment(TRL)
    fill(assessment, 1, enterprise)
    fill(assessment, 2, enterprise)
    services.submit_levels(assessment, enterprise, 2)
    with pytest.raises(ValidationError):
        services.return_levels(assessment, admin_user, [1], "  ")
    item = assessment.items.filter(level=1).first()
    services.set_review_comment(item, admin_user, "请补充佐证")
    services.return_levels(assessment, admin_user, [1], "第 1 级佐证不足")
    assert services.get_returned_levels(assessment) == [1]
    assert services.get_pass_plan(assessment) == []  # 第 1 级未通过前不能通过第 2 级
    services.evaluate_check_item(item, enterprise, {"result": "satisfied", "statement": "已补充"})
    assert services.submit_levels(assessment, enterprise, 1) == [1]
    services.pass_levels(assessment, admin_user, 2)
    item.refresh_from_db()
    assert item.review_comment == ""
    assert services.get_achieved_level(assessment) == 2


def test_next_step_hints(enterprise, admin_user):
    assessment = make_project(enterprise, targets={TRL: 1}).get_assessment(TRL)
    text, _ = services.next_step(assessment, for_admin=False)
    assert "请填写第 1 级" in text
    fill(assessment, 1, enterprise)
    assert "可提交审核" in services.next_step(assessment, for_admin=False)[0]
    services.submit_levels(assessment, enterprise, 1)
    assert "待审核" in services.next_step(assessment, for_admin=True)[0]
    services.return_levels(assessment, admin_user, [1], "请修改")
    assert "被退回" in services.next_step(assessment, for_admin=False)[0]


def test_evidence_hash_and_soft_delete(enterprise):
    assessment = make_project(enterprise).get_assessment(TRL)
    item = assessment.items.filter(level=1).first()
    upload = SimpleUploadedFile("任务书.pdf", b"file-bytes", content_type="application/pdf")
    evidence = upload_evidence_file(item, enterprise, upload, "说明")
    assert len(evidence.sha256) == 64
    assert item.active_evidence_count == 1
    with pytest.raises(ValidationError):
        upload_evidence_file(item, enterprise, SimpleUploadedFile("bad.exe", b"x"))
    soft_delete_evidence(evidence, enterprise)
    evidence.refresh_from_db()
    assert evidence.is_deleted is True
    assert EvidenceFile.objects.count() == 1


def test_reopen_allows_returning_passed_levels(enterprise, admin_user):
    assessment = make_project(enterprise, targets={TRL: 1}).get_assessment(TRL)
    fill(assessment, 1, enterprise)
    services.submit_levels(assessment, enterprise, 1)
    services.pass_levels(assessment, admin_user, 1)
    assessment.status = assessment.STATUS_REPORTED
    assessment.save()
    with pytest.raises(ValidationError):
        services.return_levels(assessment, admin_user, [1], "复核")
    services.reopen_assessment(assessment, admin_user)
    services.return_levels(assessment, admin_user, [1], "复核")
    assert assessment.level_reviews.get(level=1).status == LevelReview.STATUS_RETURNED
    assert services.get_achieved_level(assessment) == 0
