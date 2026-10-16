from django.urls import path

from apps.experts import views

app_name = "experts"

urlpatterns = [
    path("", views.expert_list, name="expert_list"),
    path("new/", views.expert_create, name="expert_create"),
    path("import/", views.expert_import, name="expert_import"),
    path("<int:pk>/", views.expert_detail, name="expert_detail"),
    path("<int:pk>/edit/", views.expert_edit, name="expert_edit"),
    path("fees/", views.fee_list, name="fee_list"),
    path("fees/settle/", views.fee_settle, name="fee_settle"),
    path("fees/<int:record_id>/edit/", views.fee_edit, name="fee_edit"),
    path("fees/export.<str:fmt>", views.fee_export, name="fee_export"),
]
