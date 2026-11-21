from django import forms
from django.core.exceptions import ValidationError

from apps.experts.models import ExpertProfile
from apps.experts.services import split_tags


class ExpertForm(forms.ModelForm):
    tags_text = forms.CharField(label="专家标识", required=False, help_text="最多 3 个，用逗号或顿号分隔")
    username = forms.CharField(label="登录账号", required=False, help_text="留空则按 expert01、expert02…顺延")
    is_active = forms.BooleanField(label="账号启用", required=False, initial=True)
    website = forms.URLField(label="个人网站", required=False, assume_scheme="https")

    class Meta:
        model = ExpertProfile
        fields = [
            "name",
            "organization",
            "title",
            "position",
            "specialty",
            "in_service",
            "phone",
            "email",
            "website",
            "bio",
            "research",
            "achievements",
            "experience",
        ]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4}),
            "research": forms.Textarea(attrs={"rows": 2}),
            "achievements": forms.Textarea(attrs={"rows": 5, "placeholder": "每行一项"}),
            "experience": forms.Textarea(attrs={"rows": 4, "placeholder": "每行一段，例如：2008—2015 某研究院 研究员"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["specialty"].empty_label = "请选择专业领域"
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput,)):
                field.widget.attrs.setdefault("class", "input")
        if self.instance.pk:
            del self.fields["username"]
            self.fields["tags_text"].initial = "、".join(self.instance.tags or [])
            self.fields["is_active"].initial = self.instance.user.is_active
        else:
            del self.fields["is_active"]

    def clean_tags_text(self):
        try:
            return split_tags(self.cleaned_data.get("tags_text", ""))
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)

    def profile_data(self):
        data = {key: self.cleaned_data[key] for key in self.Meta.fields}
        data["tags"] = self.cleaned_data["tags_text"]
        return data


class ExpertImportForm(forms.Form):
    file = forms.FileField(label="专家名单（.xlsx）")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("请上传 .xlsx 格式的 Excel 文件。")
        return upload
