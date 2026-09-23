from django.urls import path

from apps.rules import views
from apps.rules.systems import MRL, TRL

app_name = "rules"

urlpatterns = [
    path("process/", views.process_overview, name="process_overview"),
    path("trl/", views.rule_library, {"system": TRL}, name="trl_rules"),
    path("mrl/", views.rule_library, {"system": MRL}, name="mrl_rules"),
]
