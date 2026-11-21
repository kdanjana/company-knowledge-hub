from django.urls import path

from .views import (
    DocumentApproveView,
    DocumentListUploadView,
    DocumentOriginalView,
    DocumentRejectView,
    NextNameView,
    StandardsView,
)

urlpatterns = [
    path("standards/", StandardsView.as_view()),
    path("documents/", DocumentListUploadView.as_view()),
    path("documents/next-name/", NextNameView.as_view()),
    path("documents/<int:pk>/original/", DocumentOriginalView.as_view()),
    path("documents/<int:pk>/approve/", DocumentApproveView.as_view()),
    path("documents/<int:pk>/reject/", DocumentRejectView.as_view()),
]
