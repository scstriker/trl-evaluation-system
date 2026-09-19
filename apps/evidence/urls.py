from django.urls import path

from apps.evidence import views

app_name = "evidence"

urlpatterns = [
    path("items/<uuid:item_id>/upload/", views.upload_evidence, name="upload"),
    path("<uuid:evidence_id>/delete/", views.delete_evidence, name="delete"),
    path("<uuid:evidence_id>/download/", views.download_evidence, name="download"),
]
