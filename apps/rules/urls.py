from django.urls import path

from apps.rules import views

app_name = "rules"

urlpatterns = [
    path("", views.rule_library, name="rule_library"),
    path("process/", views.process_overview, name="process_overview"),
    path("mrl/", views.mrl_framework, name="mrl_framework"),
]
