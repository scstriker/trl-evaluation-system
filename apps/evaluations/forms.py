from django import forms

from apps.evaluations.models import EvaluationProject


class EvaluationProjectForm(forms.ModelForm):
    class Meta:
        model = EvaluationProject
        fields = ["name", "applicant", "issuer", "domain", "track", "target_level", "remark"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "例如：稀土萃取分离过程元素成分在线检测仪表"}),
            "applicant": forms.TextInput(attrs={"class": "input", "placeholder": "委托评价的企业或单位全称"}),
            "issuer": forms.TextInput(attrs={"class": "input"}),
            "domain": forms.TextInput(attrs={"class": "input", "placeholder": "例如：智能制造 / 仪器仪表 / 工业软件"}),
            "track": forms.RadioSelect(),
            "target_level": forms.RadioSelect(choices=[(i, f"TRL {i}") for i in range(1, 10)]),
            "remark": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "可选"}),
        }

    def clean_target_level(self):
        value = int(self.cleaned_data["target_level"])
        if not 1 <= value <= 9:
            raise forms.ValidationError("目标就绪度等级必须是 TRL 1~9。")
        return value


class ProjectReportInfoForm(forms.ModelForm):
    class Meta:
        model = EvaluationProject
        fields = ["report_no", "overview_text", "summary_text"]
        widgets = {
            "report_no": forms.TextInput(attrs={"class": "input", "placeholder": "例如：ITEI-TRL-2026-001"}),
            "overview_text": forms.Textarea(attrs={"class": "input", "rows": 6}),
            "summary_text": forms.Textarea(attrs={"class": "input", "rows": 6}),
        }
