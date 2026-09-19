from django.urls import path

from apps.evaluations import views

app_name = "evaluations"

urlpatterns = [
    path("", views.project_list, name="project_list"),
    path("new/", views.project_create, name="project_create"),
    path("<uuid:project_id>/", views.project_detail, name="project_detail"),
    path("<uuid:project_id>/items/<uuid:item_id>/evaluate/", views.item_evaluate, name="item_evaluate"),
    path("<uuid:project_id>/pass-level/", views.pass_level_view, name="pass_level"),
    path("<uuid:project_id>/lock/", views.lock_view, name="lock"),
    path("<uuid:project_id>/unlock/", views.unlock_view, name="unlock"),
]
