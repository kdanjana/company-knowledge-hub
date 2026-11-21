from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Conversation
from .serializers import ConversationDetailSerializer, ConversationSerializer, QuestionSerializer
from .services import stream_reply

# All chat views: any logged-in user (default permission IsAuthenticated). Users only see their own conversations.


class ConversationListCreateView(generics.ListCreateAPIView):
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user).order_by("-updated_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ConversationDetailView(generics.RetrieveDestroyAPIView):
    serializer_class = ConversationDetailSerializer

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)


class SendMessageView(APIView):
    """POST {"question": "..."} -> streamed answer (application/x-ndjson). Rate-limited per user."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat"

    def post(self, request, pk: int):
        conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
        data = QuestionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        response = StreamingHttpResponse(
            stream_reply(
                request.user,
                conversation,
                data.validated_data["question"].strip(),
                request_id=getattr(request, "request_id", ""),  # set by config.observability.RequestIdMiddleware
            ),
            content_type="application/x-ndjson",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # tell proxies not to buffer the stream
        return response
