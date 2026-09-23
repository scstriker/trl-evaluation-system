from django.urls import path, register_converter

from apps.evaluations import views
from apps.rules.systems import MRL, TRL


class SystemConverter:
    regex = "trl|mrl"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(SystemConverter, "system")

app_name = "evaluations"

urlpatterns = [
    path("trl/", views.assessment_list, {"system": TRL}, name="trl_list"),
    path("mrl/", views.assessment_list, {"system": MRL}, name="mrl_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<uuid:project_id>/", views.project_home, name="project_home"),
    path("projects/<uuid:project_id>/<system:system>/", views.assessment_detail, name="assessment_detail"),
    path("projects/<uuid:project_id>/<system:system>/add/", views.add_assessment_view, name="add_assessment"),
    path("projects/<uuid:project_id>/<system:system>/submit/", views.submit_view, name="submit"),
    path("projects/<uuid:project_id>/<system:system>/pass/", views.pass_view, name="pass_levels"),
    path("projects/<uuid:project_id>/<system:system>/return/", views.return_view, name="return_levels"),
    path("projects/<uuid:project_id>/<system:system>/reopen/", views.reopen_view, name="reopen"),
    path("items/<uuid:item_id>/evaluate/", views.item_evaluate, name="item_evaluate"),
    path("items/<uuid:item_id>/review-comment/", views.item_review_comment, name="item_review_comment"),
]
