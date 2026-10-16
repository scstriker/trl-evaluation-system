from django import forms

from apps.reports.models import ReportFrontMatter, ReportModule


class FrontMatterForm(forms.ModelForm):
    class Meta:
        model = ReportFrontMatter
        fields = ["compiled_by", "checked_by", "approved_by", "issue_date", "standard_version", *ReportFrontMatter.SECTION_FIELDS]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            **{field: forms.Textarea(attrs={"rows": 3}) for field in ReportFrontMatter.SECTION_FIELDS},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")


ModuleFormSet = forms.inlineformset_factory(
    ReportFrontMatter,
    ReportModule,
    fields=["name", "design", "trial", "verification"],
    extra=1,
    can_delete=True,
    widgets={
        "name": forms.TextInput(attrs={"class": "input", "placeholder": "例如：高稳定性 X 射线源"}),
        "design": forms.Textarea(attrs={"class": "input", "rows": 3}),
        "trial": forms.Textarea(attrs={"class": "input", "rows": 3}),
        "verification": forms.Textarea(attrs={"class": "input", "rows": 3}),
    },
)
