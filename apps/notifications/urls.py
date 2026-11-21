from django.urls import path

from apps.notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<int:notification_id>/open/", views.open_notification, name="open"),
    path("read-all/", views.read_all, name="read_all"),
]
