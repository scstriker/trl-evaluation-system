from django import forms

from apps.evaluations.models import EvaluationProject, EvaluationTeamMember
from apps.evaluations.services import rules_available
from apps.rules.models import Specialty
from apps.rules.systems import MRL, SYSTEMS, TRL


class ProjectForm(forms.ModelForm):
    """企业申报评价项目：委托单位取注册企业名，出具单位固定为评价机构。"""

    include_trl = forms.BooleanField(label="技术就绪度评价", required=False)
    trl_target = forms.TypedChoiceField(
        label="目标技术就绪度等级", coerce=int, choices=[(i, f"TRL {i}") for i in SYSTEMS[TRL].levels], required=False
    )
    include_mrl = forms.BooleanField(label="制造成熟度评价", required=False)
    mrl_target = forms.TypedChoiceField(
        label="目标制造成熟度等级", coerce=int, choices=[(i, f"MRL {i}") for i in SYSTEMS[MRL].levels], required=False
    )

    class Meta:
        model = EvaluationProject
        fields = ["name", "specialty", "domain", "tech_type", "remark"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "例如：XT萃取分离过程元素成分在线检测仪表"}),
            "specialty": forms.Select(attrs={"class": "input"}),
            "domain": forms.TextInput(attrs={"class": "input", "placeholder": "例如：智能制造 / 仪器仪表 / 工业软件"}),
            "tech_type": forms.RadioSelect(),
            "remark": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "可选"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tech_type"].choices = EvaluationProject.TECH_TYPE_CHOICES
        other = Specialty.objects.filter(code="other").first()
        if other:
            self.fields["specialty"].queryset = Specialty.objects.filter(pk=other.pk)
            self.fields["specialty"].initial = other.pk
            self.fields["specialty"].required = True
            self.fields["specialty"].empty_label = None
            self.fields["specialty"].help_text = "所属技术领域由评价机构在分配专家时确定，申报统一选择“其他”。"
        else:
            self.fields["specialty"].empty_label = "请选择技术领域"
        self.mrl_available = rules_available(MRL)
        if not self.mrl_available:
            # 制造成熟度细则导入前不开放，申报页只保留技术就绪度
            del self.fields["include_mrl"]
            del self.fields["mrl_target"]

    def clean(self):
        cleaned = super().clean()
        if not self.mrl_available:
            cleaned["include_trl"] = True
        if cleaned.get("include_mrl") and not self.mrl_available:
            self.add_error("include_mrl", "制造成熟度评价细则尚未导入，暂不能申报制造成熟度评价。")
        if not cleaned.get("include_trl") and not cleaned.get("include_mrl"):
            raise forms.ValidationError("请至少选择一项评价内容：技术就绪度评价或制造成熟度评价。")
        if cleaned.get("include_trl") and not cleaned.get("trl_target"):
            self.add_error("trl_target", "请选择目标技术就绪度等级。")
        if cleaned.get("include_mrl") and not cleaned.get("mrl_target"):
            self.add_error("mrl_target", "请选择目标制造成熟度等级。")
        return cleaned

    def targets(self):
        targets = {}
        if self.cleaned_data.get("include_trl"):
            targets[TRL] = self.cleaned_data["trl_target"]
        if self.cleaned_data.get("include_mrl"):
            targets[MRL] = self.cleaned_data["mrl_target"]
        return targets


class ProjectInfoForm(forms.ModelForm):
    """报告编制信息：项目概况由企业或管理员填写；报告编号仅管理员可改。"""

    class Meta:
        model = EvaluationProject
        fields = ["report_no", "overview_text"]
        widgets = {
            "report_no": forms.TextInput(attrs={"class": "input", "placeholder": "留空则生成报告时自动分配"}),
            "overview_text": forms.Textarea(attrs={"class": "input", "rows": 6}),
        }

    def __init__(self, *args, admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not admin:
            del self.fields["report_no"]


TeamMemberFormSet = forms.inlineformset_factory(
    EvaluationProject,
    EvaluationTeamMember,
    fields=["role", "name", "title", "specialty", "org"],
    extra=2,
    can_delete=True,
    widgets={
        "role": forms.TextInput(attrs={"class": "input", "placeholder": "组长 / 成员"}),
        "name": forms.TextInput(attrs={"class": "input"}),
        "title": forms.TextInput(attrs={"class": "input"}),
        "specialty": forms.TextInput(attrs={"class": "input"}),
        "org": forms.TextInput(attrs={"class": "input"}),
    },
)


def team_formset(project, group, data=None):
    """评价组成员表单：按组（自评估工作组 / 第三方评价组）分别编辑。"""
    return TeamMemberFormSet(
        data,
        instance=project,
        prefix=f"team_{group}",
        queryset=project.team_members.filter(group=group),
    )


def save_team_formset(formset, group):
    members = formset.save(commit=False)
    for member in members:
        member.group = group
        member.save()
    for member in formset.deleted_objects:
        member.delete()
