from django.urls import path

from . import views

urlpatterns = [
    path("documents/", views.DocumentListView.as_view()),
    path("runs/", views.RunListCreateView.as_view()),
    path("runs/<int:pk>/", views.RunDetailView.as_view()),
    path("findings/", views.FindingListView.as_view()),
    path("findings/<int:pk>/", views.FindingDetailView.as_view()),
    path("findings/<int:pk>/feedback/", views.FindingFeedbackView.as_view()),
    path("evaluations/", views.EvalResultListView.as_view()),
]
