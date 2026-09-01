from django.urls import path

from .views import CSRFView, LoginView, LogoutView, MeView, SignupView

urlpatterns = [
    path("csrf", CSRFView.as_view(), name="auth-csrf"),
    path("signup", SignupView.as_view(), name="auth-signup"),
    path("login", LoginView.as_view(), name="auth-login"),
    path("logout", LogoutView.as_view(), name="auth-logout"),
    path("me", MeView.as_view(), name="auth-me"),
]
