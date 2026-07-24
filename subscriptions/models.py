import uuid
from django.db import models
from django.db.models import Count, F, Q
from django.conf import settings
from catalog.models import Book
from accounts.models import School


def subscriptions_with_new_listings_count(queryset):
    """Annotate `new_listings_count` (active listings created after subscribing)
    so SubscriptionSerializer can render lists without per-object queries."""
    return queryset.select_related('book').annotate(
        new_listings_count=Count(
            'book__listings',
            filter=Q(
                book__listings__status='active',
                book__listings__created_at__gt=F('created_at'),
            ),
            distinct=True,
        )
    )

class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscriptions')
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='subscriptions')
    school = models.ForeignKey(School, on_delete=models.SET_NULL, null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'book')

    def __str__(self):
        return f"{self.user.email} -> {self.book.title}"
