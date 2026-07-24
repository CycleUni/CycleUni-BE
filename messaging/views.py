import hmac
import logging

from rest_framework import views, status, generics
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.exceptions import PermissionDenied, NotFound
from messaging.models import Conversation
from messaging.serializers import ConversationSerializer
from listings.models import Listing
from django.core.exceptions import ValidationError
from django.db.models import OuterRef, Q, Subquery, F
from django.utils import timezone
from rest_framework_simplejwt.backends import TokenBackend
import datetime
from django.conf import settings

logger = logging.getLogger(__name__)

class ConversationListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ConversationSerializer

    def get_queryset(self):
        user = self.request.user
        return Conversation.objects.filter(
            Q(buyer=user) | Q(listing__seller=user)
        ).exclude(
            Q(buyer=user, buyer_deleted_at__isnull=False) |
            Q(listing__seller=user, seller_deleted_at__isnull=False)
        ).select_related('listing__book', 'listing__seller', 'buyer').order_by('-updated_at').distinct()

    def post(self, request):
        if not request.user.is_verified():
            return Response({"error": {"code": "auth.errNotVerified"}}, status=status.HTTP_403_FORBIDDEN)
            
        listing_id = request.data.get('listing_id')
        if not listing_id:
            return Response({"error": "listing_id required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            listing = Listing.objects.get(id=listing_id)
            if listing.seller == request.user:
                return Response({"error": "Cannot message yourself"}, status=status.HTTP_400_BAD_REQUEST)
                
            conv, created = Conversation.objects.get_or_create(
                listing=listing,
                buyer=request.user
            )
            serializer = ConversationSerializer(conv, context={'request': request})
            return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        except Listing.DoesNotExist:
            return Response({"error": "Listing not found"}, status=status.HTTP_404_NOT_FOUND)





class ConversationDeleteView(views.APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, conversation_id):
        try:
            conversation = Conversation.objects.select_related('listing').get(id=conversation_id)
        except (Conversation.DoesNotExist, ValidationError):
            return Response(status=status.HTTP_404_NOT_FOUND)

        result = conversation.mark_deleted_by(request.user)
        if result is False:
            return Response(status=status.HTTP_403_FORBIDDEN)
        # 'deleted' = both parties deleted, row is gone; 'hidden' = only one side
        return Response({"status": result}, status=status.HTTP_200_OK)






class ChatTokenView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not settings.EDGE_CHAT_JWT_SECRET:
            return Response({"error": "Chat not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        conversation_id = request.query_params.get('conversation_id')
        if not conversation_id:
            return Response({"error": {"code": "msg.errConversationRequired"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            conversation = Conversation.objects.select_related('listing').get(id=conversation_id)
        except (Conversation.DoesNotExist, ValidationError):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if conversation.buyer_id != request.user.id and conversation.listing.seller_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)

        # room_id ties this token to exactly this conversation: CFEdgeChat
        # rejects any attempt to use it against a different room, so a valid
        # token can't be replayed to read/send in someone else's conversation.
        #
        # A standalone TokenBackend instance (not settings.SIMPLE_JWT) is
        # used to sign with EDGE_CHAT_JWT_SECRET instead of the app's own JWT
        # key: rest_framework_simplejwt caches api_settings.SIGNING_KEY on
        # first use per-process, so temporarily mutating settings.SIMPLE_JWT
        # here would silently no-op after the first login/refresh in the
        # process and sign with the wrong key.
        token_backend = TokenBackend(algorithm="HS256", signing_key=settings.EDGE_CHAT_JWT_SECRET)
        token = token_backend.encode({
            "user_id": str(request.user.id),
            "room_id": str(conversation.id),
            # Must match the `appId` URL segment CFEdgeChat validates the
            # room-scoped request/connection against (`/ws/<app_id>/<room_id>`,
            # `/api/<app_id>/<room_id>/...`) — its ChatRoom DO is keyed by
            # `${appId}:${roomId}`, and rejects (403) any token whose app_id
            # doesn't match the path exactly. See settings.EDGE_CHAT_APP_ID.
            "app_id": settings.EDGE_CHAT_APP_ID,
            # Lets CFEdgeChat's ChatRoom DO learn, from the signed token itself
            # rather than trusting client input, who is allowed to receive
            # cross-room notifications about this conversation on their
            # single per-user hub connection.
            "participant_ids": [str(conversation.buyer_id), str(conversation.listing.seller_id)],
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2),
        })
        edge_chat_url = getattr(settings, 'EDGE_CHAT_URL', 'http://localhost:8787')
        return Response({"token": token, "edge_chat_url": edge_chat_url})


class HubTokenView(views.APIView):
    # A single, room-independent token for the per-user notification hub
    # (one WebSocket per user covering all of their conversations, instead of
    # one per open chat). It only proves identity — it carries no room_id and
    # grants no access to any conversation's content, since CFEdgeChat's
    # UserHub only relays events that ChatRoom DOs push to it (see
    # ChatRoom's use of `participant_ids` above), never anything a client
    # requests by room_id over this connection.
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not settings.EDGE_CHAT_JWT_SECRET:
            return Response({"error": "Chat not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        token_backend = TokenBackend(algorithm="HS256", signing_key=settings.EDGE_CHAT_JWT_SECRET)
        token = token_backend.encode({
            "user_id": str(request.user.id),
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2),
        })
        edge_chat_url = getattr(settings, 'EDGE_CHAT_URL', 'http://localhost:8787')
        return Response({"token": token, "edge_chat_url": edge_chat_url})


class EdgeChatWebhookView(views.APIView):
    # Server-to-server callback from the CFEdgeChat Worker: not a logged-in
    # user, so DRF's normal JWT auth doesn't apply. Authenticity is instead
    # verified via a shared secret (X-Webhook-Secret header, compared with
    # settings.EDGE_CHAT_WEBHOOK_SECRET using a timing-safe comparison) —
    # see _verify_webhook_secret below. If the secret isn't configured at
    # all, the endpoint refuses every request rather than accepting
    # unauthenticated calls.
    #
    # Read-state (`{buyer,seller}_last_read_at`) is NO LONGER updated here.
    # Mark-read is owned by CFEdgeChat's UserHub (see
    # `POST /api/<app>/<room>/read`). This webhook only mirrors the
    # conversation's latest_message_body + updated_at into the Django row
    # so the inbox listing (order_by('-updated_at')) and listing-detail
    # preview stay in sync.
    permission_classes = [AllowAny]

    def _verify_webhook_secret(self, request):
        """Timing-safe shared-secret check. Returns an error Response, or None if OK."""
        expected = settings.EDGE_CHAT_WEBHOOK_SECRET
        if not expected:
            return Response(
                {"error": {"code": "msg.errWebhookNotConfigured"}},
                status=status.HTTP_403_FORBIDDEN,
            )
        provided = request.headers.get("X-Webhook-Secret", "")
        if not provided or not hmac.compare_digest(provided, expected):
            return Response(
                {"error": {"code": "msg.errWebhookSecretInvalid"}},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def post(self, request):
        auth_error = self._verify_webhook_secret(request)
        if auth_error is not None:
            return auth_error

        # Example payload: {"room_id": "...", "sender_id": "...", "content": "...", "timestamp": ..., "is_offline": true/false}
        data = request.data
        room_id = data.get("room_id")
        sender_id = data.get("sender_id")
        content = data.get("content")
        is_offline = data.get("is_offline", False)

        try:
            conversation = Conversation.objects.select_related('listing').get(id=room_id)
            conversation.latest_message_body = content
            conversation.save(update_fields=['latest_message_body', 'updated_at'])

            if is_offline:
                # Push notification (FCM/email) not yet implemented.
                # Message content is deliberately excluded from this log line.
                logger.info("Offline message in room %s from %s", room_id, sender_id)

            return Response({"status": "received"}, status=status.HTTP_200_OK)

        except (Conversation.DoesNotExist, ValidationError):
            # ValidationError covers a malformed (non-UUID) room_id
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)



