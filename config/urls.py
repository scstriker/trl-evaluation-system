from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.evaluations.urls")),
    path("rules/", include("apps.rules.urls")),
    path("evidence/", include("apps.evidence.urls")),
    path("reports/", include("apps.reports.urls")),
    path("audit/", include("apps.audit.urls")),
    path("inbox/", include("apps.notifications.urls")),
    path("experts/", include("apps.experts.urls")),
    path("reviews/", include("apps.reviews.urls")),
]

# 佐证材料与报告文件不开放直链访问，只能经过权限校验的下载视图获取
