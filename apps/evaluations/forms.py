from django import forms

from apps.evaluations.models import EvaluationProject, EvaluationTeamMember
from apps.evaluations.services import rules_available
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
        fields = ["name", "domain", "tech_type", "remark"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "例如：稀土萃取分离过程元素成分在线检测仪表"}),
            "domain": forms.TextInput(attrs={"class": "input", "placeholder": "例如：智能制造 / 仪器仪表 / 工业软件"}),
            "tech_type": forms.RadioSelect(),
            "remark": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "可选"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tech_type"].choices = EvaluationProject.TECH_TYPE_CHOICES
        self.mrl_available = rules_available(MRL)

    def clean(self):
        cleaned = super().clean()
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
