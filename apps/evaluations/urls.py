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
    path("items/<uuid:item_id>/admin-edit/", views.item_admin_edit, name="item_admin_edit"),
    path("projects/<uuid:project_id>/<system:system>/target/", views.target_request_view, name="target_request"),
    path("target-requests/<uuid:request_id>/approve/", views.target_approve_view, name="target_approve"),
    path("target-requests/<uuid:request_id>/reject/", views.target_reject_view, name="target_reject"),
]
