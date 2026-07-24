import uuid
from django.db import models
from django.conf import settings
from catalog.models import Book

# NOTE: these two callables are no longer used by the current Listing model
# (the delivery_methods/payment_methods fields they defaulted were removed in
# migration 0002_remove_listing_delivery_methods_and_more.py), but migration
# 0001_initial.py still references them by import path
# ("listings.models.default_delivery"/"default_payment") to reconstruct its
# historical field defaults. Removing them breaks migration replay on a fresh
# database. Do not delete without first squashing/rewriting migration 0001.
def default_delivery():
    return ['meetup']

def default_payment():
    return ['cash']

class Listing(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    CONDITION_CHOICES = [
        ('new', 'new'),
        ('like_new', 'like_new'),
        ('noted', 'noted'),
        ('damaged', 'damaged'),
    ]

    STATUS_CHOICES = [
        ('active', 'active'),
        ('reserved', 'reserved'),
        ('sold', 'sold'),
        ('removed', 'removed'),
    ]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='listings')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='listings')
    school = models.ForeignKey('accounts.School', on_delete=models.CASCADE, related_name='listings', null=True, blank=True)
    price = models.PositiveIntegerField()
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES)
    private_note = models.TextField(blank=True)
    description = models.TextField(blank=True)
    photos = models.JSONField(default=list, help_text="完整照片網址陣列（R2 儲存後的公開 URL，見 ListingUploadURLView）")
    category = models.ForeignKey('core.Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='listings')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    course_name = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Listing {self.id} for {self.book.title} by {self.seller.email}"
