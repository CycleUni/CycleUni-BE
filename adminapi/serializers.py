"""Read-only serializers backing the Angular admin UI (users / listings / orders).

These are deliberately manual/read-only: writes for each resource are handled
by explicit, narrowly-scoped logic in views.py rather than generic
ModelSerializer.update(), so we can enforce field allow-lists (e.g. never
letting is_staff/is_superuser/password through) precisely per-endpoint.
"""
from rest_framework import serializers

from accounts.models import User, School
from core.models import Category
from listings.models import Listing
from moderation.models import ChatReport
from orders.models import Order


class AdminSchoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = ('id', 'name', 'email_domain', 'translations')


class AdminCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ('id', 'slug', 'title', 'description', 'sort_order', 'is_active', 'translations')


class AdminUserSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()
    is_verified = serializers.SerializerMethodField()

    def get_school_name(self, obj):
        return obj.school.name if obj.school else ''

    def get_is_verified(self, obj):
        return obj.is_verified()

    class Meta:
        model = User
        fields = (
            'id', 'email', 'first_name', 'last_name', 'display_name',
            'school', 'school_name', 'edu_email', 'is_active', 'is_verified',
            'verified_at', 'created_at', 'is_staff', 'is_superuser',
        )
        read_only_fields = fields


class AdminListingSerializer(serializers.ModelSerializer):
    book = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()
    school = serializers.SerializerMethodField()

    def get_book(self, obj):
        return {'id': obj.book_id, 'title': obj.book.title}

    def get_seller(self, obj):
        return {'id': obj.seller_id, 'email': obj.seller.email}

    def get_school(self, obj):
        if not obj.school_id:
            return None
        return {'id': obj.school_id, 'name': obj.school.name}

    class Meta:
        model = Listing
        fields = ('id', 'book', 'seller', 'school', 'price', 'condition', 'status', 'created_at')
        read_only_fields = fields


class AdminOrderSerializer(serializers.ModelSerializer):
    buyer = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()
    listing = serializers.SerializerMethodField()

    def get_buyer(self, obj):
        return {'id': obj.buyer_id, 'email': obj.buyer.email}

    def get_seller(self, obj):
        return {'id': obj.seller_id, 'email': obj.seller.email}

    def get_listing(self, obj):
        return {'id': str(obj.listing_id), 'book_title': obj.listing.book.title}

    class Meta:
        model = Order
        fields = ('id', 'buyer', 'seller', 'listing', 'status', 'total_amount', 'created_at', 'updated_at')
        read_only_fields = fields


class AdminChatReportSerializer(serializers.ModelSerializer):
    conversation_id = serializers.SerializerMethodField()
    listing_title = serializers.SerializerMethodField()
    reporter_email = serializers.SerializerMethodField()
    reported_party_email = serializers.SerializerMethodField()

    def get_conversation_id(self, obj):
        return str(obj.conversation_id)

    def get_listing_title(self, obj):
        return obj.conversation.listing.book.title

    def get_reporter_email(self, obj):
        return obj.reporter.email

    def get_reported_party_email(self, obj):
        return obj.reported_party.email

    class Meta:
        model = ChatReport
        fields = ('id', 'conversation_id', 'listing_title', 'reporter_email',
                  'reported_party_email', 'reason', 'detail', 'status', 'created_at')
        read_only_fields = fields



