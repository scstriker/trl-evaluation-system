from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SystemLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("register/done/", views.register_done, name="register_done"),
    path("captcha.svg", views.captcha_image, name="captcha"),
    path("account/", views.account, name="account"),
    path("users/", views.user_list, name="user_list"),
    path("users/<int:profile_id>/<slug:action>/", views.user_action, name="user_action"),
]
