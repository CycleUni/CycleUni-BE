import uuid
from django.db import models
from django.conf import settings
from listings.models import Listing

class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name='conversations')
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations_as_buyer')

    latest_message_body = models.TextField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Per-participant read pointers. A conversation is unread for a given
    # participant when `updated_at` is newer than their own column here —
    # two columns (not a generic read-state table) because a conversation
    # only ever has exactly these two participants (buyer, listing.seller).
    buyer_last_read_at = models.DateTimeField(null=True, blank=True)
    seller_last_read_at = models.DateTimeField(null=True, blank=True)

    # Soft-delete per participant: a conversation is hidden from a user
    # once they delete it. When both parties have deleted it the whole row
    # is permanently removed (see `mark_deleted_by`).
    buyer_deleted_at = models.DateTimeField(null=True, blank=True)
    seller_deleted_at = models.DateTimeField(null=True, blank=True)

    def mark_deleted_by(self, user):
        """Set the caller's deletion timestamp; remove the row if both are set."""
        from django.utils import timezone
        if self.buyer_id == user.id:
            self.buyer_deleted_at = timezone.now()
        elif self.listing.seller_id == user.id:
            self.seller_deleted_at = timezone.now()
        else:
            return False  # not a participant
        self.save()
        if self.buyer_deleted_at and self.seller_deleted_at:
            self.delete()
            return 'deleted'
        return 'hidden'

    class Meta:
        unique_together = ('listing', 'buyer')

    def __str__(self):
        return f"Conversation on {self.listing.id} by {self.buyer.email}"



