from django.urls import path

from . import views

# Mounted at /api/auth/. User management (admins) is at /api/users/ (config/urls.py).
urlpatterns = [
    path("login/", views.LoginView.as_view()),
    path("refresh/", views.RefreshView.as_view()),  # {refresh} -> new {access, refresh}; old refresh blacklisted
    path("logout/", views.LogoutView.as_view()),
    path("me/", views.MeView.as_view()),
]
