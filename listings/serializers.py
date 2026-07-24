from urllib.parse import urlparse

from django.conf import settings
from rest_framework import serializers
from listings.models import Listing

from core.models import Category

MAX_LISTING_PHOTOS = 6


def _allowed_photo_hosts(request):
    """Hosts a listing photo URL is allowed to point at: the configured R2
    custom domain in production, or this server's own host in local dev
    (FileSystemStorage fallback serves uploads same-origin under /media/).
    Without this, `photos` — populated straight from client input, not
    derived from the upload endpoints — could point anywhere, e.g. a
    tracking pixel or phishing image on an attacker-controlled domain."""
    hosts = set()
    storage = settings.STORAGES.get("default", {})
    custom_domain = storage.get("OPTIONS", {}).get("custom_domain")
    if custom_domain:
        hosts.add(custom_domain.lower())
    if request is not None:
        hosts.add(request.get_host().split(':')[0].lower())
    return hosts


class ListingSerializer(serializers.ModelSerializer):
    seller_name = serializers.SerializerMethodField()
    seller_avatar_url = serializers.SerializerMethodField()
    school_name = serializers.SerializerMethodField()
    book_title = serializers.CharField(source='book.title', read_only=True, default='')
    book_authors = serializers.CharField(source='book.authors', read_only=True, default='')
    book_cover_url = serializers.CharField(source='book.cover_url', read_only=True, default='')
    book_source = serializers.CharField(source='book.source', read_only=True, default='')
    course_name = serializers.CharField(required=False, allow_blank=True, default='')
    isbn = serializers.CharField(source='book.isbn13', read_only=True, default='')
    category = serializers.SlugRelatedField(
        slug_field='slug',
        queryset=Category.objects.filter(is_active=True),
        required=False,
        allow_null=True
    )
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = '__all__'
        read_only_fields = ('seller', 'created_at', 'updated_at')

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        request = self.context.get('request')
        if not request or not request.user.is_authenticated or request.user.id != instance.seller_id:
            rep.pop('private_note', None)
        
        # Ensure we always return lists even if database had null
        if not rep.get('delivery_methods'):
            rep['delivery_methods'] = ['meetup']
        if not rep.get('payment_methods'):
            rep['payment_methods'] = ['cash']
            
        return rep

    def get_seller_name(self, obj):
        if not obj.seller:
            return ''
        return obj.seller.display_name

    def get_seller_avatar_url(self, obj):
        if not obj.seller:
            return ''
        return obj.seller.avatar_url

    def get_photo_url(self, obj):
        if obj.photos and len(obj.photos) > 0:
            return obj.photos[0]
        return ''

    def validate_photos(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('listing.errInvalidPhotos')
        if len(value) > MAX_LISTING_PHOTOS:
            raise serializers.ValidationError('listing.errTooManyPhotos')

        allowed_hosts = _allowed_photo_hosts(self.context.get('request'))
        for url in value:
            if not isinstance(url, str):
                raise serializers.ValidationError('listing.errInvalidPhotos')
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https') or parsed.hostname is None:
                raise serializers.ValidationError('listing.errInvalidPhotos')
            if parsed.hostname.lower() not in allowed_hosts:
                raise serializers.ValidationError('listing.errPhotoHostNotAllowed')
        return value

    def get_school_name(self, obj):
        school = obj.seller.school if obj.seller else None
        if not school:
            return ''
        request = self.context.get('request')
        if request:
            from core.i18n import resolve_language
            lang = resolve_language(request)
            return school.localized_name(lang)
        return school.name

