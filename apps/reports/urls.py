from django.urls import path

from apps.reports import views

app_name = "reports"

urlpatterns = [
    path("", views.report_list, name="report_list"),
    path("projects/<uuid:project_id>/", views.report_preview, name="report_preview"),
    path("projects/<uuid:project_id>/info/", views.update_report_info, name="update_report_info"),
    path("projects/<uuid:project_id>/generate/", views.generate_report, name="generate_report"),
    path("projects/<uuid:project_id>/request/", views.request_export, name="request_export"),
    path("requests/<uuid:request_id>/approve/", views.approve_request, name="approve_request"),
    path("requests/<uuid:request_id>/reject/", views.reject_request, name="reject_request"),
    path("<uuid:report_id>/download/", views.download_report, name="download_report"),
    path("<uuid:report_id>/download.pdf", views.download_report_pdf, name="download_report_pdf"),
    path("<uuid:report_id>/make-pdf/", views.make_pdf, name="make_pdf"),
    path("projects/<uuid:project_id>/front-matter/", views.front_matter_edit, name="front_matter"),
]
