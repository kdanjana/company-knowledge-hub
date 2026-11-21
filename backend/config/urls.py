from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from accounts.views import UserViewSet

from .health import health, ready

router = SimpleRouter()
router.register("users", UserViewSet, basename="user")  # /api/users/ (admins only)

urlpatterns = [
    path("api/health/", health),
    path("api/ready/", ready),
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/", include(router.urls)),
    path("api/chat/", include("chat.urls")),
    path("api/kb/", include("kb.urls")),
    path("api/", include("audit.urls")),
]
