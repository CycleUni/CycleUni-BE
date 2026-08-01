import uuid

from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.core.cache import cache
from django.core.files.storage import default_storage
from listings.models import Listing
from listings.serializers import ListingSerializer

from rest_framework.throttling import ScopedRateThrottle

# Extension is derived from this allowlist, never from the client-supplied
# filename or Content-Type header alone (both are trivially spoofable). The
# presigned-upload path (ListingUploadURLView) can only check the declared
# Content-Type up front — the file never touches this server — so R2's own
# `Content-Type` condition is what actually enforces it at upload time. The
# direct-proxy fallback (ListingUploadDirectView, dev-only) additionally
# decodes the bytes with Pillow since it does receive them.
from core.uploads import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    detect_image_extension,
    r2_client_and_options,
    storage_key_from_url,
)
from core.cache import HOME_RECENT_TTL, LISTING_CACHE_TTL, versioned_key
from listings.utils import PERMANENT_LISTING_PREFIX, tmp_key_prefix

class ListingListCreateView(views.APIView):
    permission_classes = [AllowAny]

    def get_throttles(self):
        if self.request and self.request.method == 'POST':
            self.throttle_scope = 'listing_create'
        else:
            self.throttle_scope = 'search'
        return [ScopedRateThrottle()]

    def get(self, request):
        from core.i18n import resolve_language
        lang = resolve_language(request)
        
        school = request.query_params.get('school', '')
        seller_id = request.query_params.get('seller_id', '')
        page_param = request.query_params.get('page', '1')
        
        cache_key = versioned_key('listing_list', lang, school, seller_id, page_param)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)
        # select_related avoids per-row queries for the serializer's related fields
        listings = Listing.objects.filter(status='active').select_related(
                'book', 'seller', 'seller__school'
        ).order_by('-created_at')
        if school:
            listings = listings.filter(seller__school__name=school)
        if seller_id:
            listings = listings.filter(seller_id=seller_id)
            
        listings = listings[:4000]
            
        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(listings, request)
        if page is not None:
            serializer = ListingSerializer(page, many=True, context={'request': request})
            response_data = paginator.get_paginated_response(serializer.data).data
            cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
            return Response(response_data)
            
        serializer = ListingSerializer(listings, many=True, context={'request': request})
        response_data = serializer.data
        cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
        return Response(response_data)

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"error": {"code": "auth.errNotLoggedIn"}}, status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_verified():
            return Response({"error": {"code": "acct.errUnverified"}}, status=status.HTTP_403_FORBIDDEN)
        
        serializer = ListingSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(seller=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response({"error": {"code": "sell.errValidation", "fields": serializer.errors}}, status=status.HTTP_400_BAD_REQUEST)

class RecentBooksView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from core.i18n import resolve_language
        lang = resolve_language(request)
        
        school = request.query_params.get('school', '')
        page_param = request.query_params.get('page', '1')
        
        limit_param = request.query_params.get('limit', '200')
        cache_key = f"recent_books_{lang}_{school}_{page_param}_{limit_param}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)
        
        from catalog.models import Book
        from collections import Counter
        from django.db.models import Max
        from rest_framework.pagination import PageNumberPagination

        books_qs = Book.objects.filter(listings__status='active')
        if school:
            books_qs = books_qs.filter(listings__seller__school__name=school)
            
        books_qs = books_qs.annotate(
            latest_listing=Max('listings__created_at')
        ).order_by('-latest_listing')

        paginator = PageNumberPagination()
        paginated_books = paginator.paginate_queryset(books_qs, request)
        if paginated_books is not None:
            # Slice only after pagination to maintain queryset integrity
            limit = int(limit_param) if limit_param.isdigit() else 200
            books_to_process = paginated_books[:limit]
        else:
            books_to_process = books_qs[:int(limit_param) if limit_param.isdigit() else 200]

        book_ids = [b.id for b in books_to_process]
        
        if not book_ids:
            return paginator.get_paginated_response([]) if paginated_books is not None else Response([])
            
        active_filter = {'book_id__in': book_ids, 'status': 'active'}
        if school:
            active_filter['seller__school__name'] = school
            
        all_book_listings = Listing.objects.filter(**active_filter).values('book_id', 'price', 'condition')
        
        book_stats = {}
        for lst in all_book_listings:
            b_id = lst['book_id']
            if b_id not in book_stats:
                book_stats[b_id] = {'prices': [], 'conditions': Counter()}
            book_stats[b_id]['prices'].append(lst['price'])
            book_stats[b_id]['conditions'][lst['condition']] += 1
            
        results = []
        for book in books_to_process:
            stats = book_stats.get(book.id, {'prices': [], 'conditions': Counter()})
            avg_price = sum(stats['prices']) / len(stats['prices']) if stats['prices'] else 0
            
            results.append({
                'id': book.id,
                'isbn': book.isbn13,
                'title': book.title,
                'authors': book.authors,
                'cover_url': book.cover_url,
                'avg_price': round(avg_price),
                'conditions': dict(stats['conditions'])
            })
            
        if paginated_books is not None:
            response_data = paginator.get_paginated_response(results).data
        else:
            response_data = results
            
        cache.set(cache_key, response_data, timeout=HOME_RECENT_TTL)
        return Response(response_data)


class ListingUploadURLView(views.APIView):
    """Issues a presigned R2 PUT URL so the browser uploads the file
    directly to object storage — it never passes through this server.
    (R2 doesn't support S3 POST policies, which is what would otherwise let
    a presigned upload enforce a server-side size limit via a
    `content-length-range` condition — a plain presigned PUT can't do that,
    so `MAX_UPLOAD_SIZE_BYTES` is enforced only on the ListingUploadDirectView
    fallback below, not on real R2 uploads. Closing that gap needs either an
    R2 event notification that deletes oversized objects after the fact, or
    routing uploads through a Worker that can inspect Content-Length.)

    Falls back to `mode: "direct"` (see ListingUploadDirectView) when R2
    isn't configured (local dev without real credentials — see
    core.conf.resolve_storage_config), since there's no S3-compatible
    endpoint to presign against in that case.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def post(self, request):
        content_type = request.data.get('content_type')
        ext = ALLOWED_CONTENT_TYPES.get(content_type)
        if not ext:
            return Response({"error": {"code": "listing.errUnsupportedFileType"}}, status=status.HTTP_400_BAD_REQUEST)

        client, options = r2_client_and_options()
        if client is None:
            return Response({"mode": "direct"})

        key = f"{tmp_key_prefix(request.user.id)}{uuid.uuid4().hex}.{ext}"

        # Cloudflare R2 does not support S3 POST policies (returns 501).
        # We must use PUT presigned URLs instead.
        upload_url = client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': options["bucket_name"],
                'Key': key,
                'ContentType': content_type,
            },
            ExpiresIn=300,
        )
        
        protocol = options.get("url_protocol", "https:").rstrip(":")
        photo_url = f"{protocol}://{options['custom_domain']}/{key}"

        return Response({
            "mode": "presigned_put",
            "upload_url": upload_url,
            "photo_url": photo_url,
        })


class ListingUploadDirectView(views.APIView):
    """Local-dev-only fallback: proxies the file through this server into
    FileSystemStorage when R2 isn't configured. The frontend only calls this
    when ListingUploadURLView responded with `mode: "direct"`."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({"error": {"code": "listing.errNoFile"}}, status=status.HTTP_400_BAD_REQUEST)

        if file.size > MAX_UPLOAD_SIZE_BYTES:
            return Response({"error": {"code": "listing.errFileTooLarge"}}, status=status.HTTP_400_BAD_REQUEST)

        ext = detect_image_extension(file)
        if not ext:
            return Response({"error": {"code": "listing.errUnsupportedFileType"}}, status=status.HTTP_400_BAD_REQUEST)

        filename = f"{tmp_key_prefix(request.user.id)}{uuid.uuid4().hex}.{ext}"
        path = default_storage.save(filename, file)
        url = request.build_absolute_uri(default_storage.url(path))

        return Response({"url": url})

class ListingUploadDeleteView(views.APIView):
    """Deletes an image the caller uploaded but hasn't attached to a listing
    yet, or is removing from a listing they own.

    Both paths are fail-closed — anything not provably the caller's is
    refused rather than deleted:

    - `tmp/listings/<user_id>/…` — only the user whose id is in the key.
      Nothing else records who uploaded an unattached file, which is exactly
      why the uploader's id is baked into the key at upload time.
    - `listings/…` — only if a listing the caller owns actually references
      this exact URL.

    The URL's host is validated too (storage_key_from_url): deriving the key
    from `path` alone would let anyone name any object in the bucket just by
    swapping the hostname while keeping the path.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def delete(self, request):
        url = request.query_params.get('url') or request.data.get('url')
        if not url or not isinstance(url, str):
            return Response({"error": {"code": "listing.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        key = storage_key_from_url(url, request)
        if key is None:
            return Response({"error": {"code": "listing.errPhotoHostNotAllowed"}}, status=status.HTTP_400_BAD_REQUEST)

        if key.startswith(tmp_key_prefix(request.user.id)):
            default_storage.delete(key)
            return Response(status=status.HTTP_204_NO_CONTENT)

        if key.startswith(f"{PERMANENT_LISTING_PREFIX}/"):
            # icontains only narrows the candidate rows (JSONField has no
            # cross-backend exact-membership lookup: `contains` is unsupported
            # on SQLite). Exact membership is then confirmed in Python, so a
            # substring collision can't authorise a delete.
            candidates = Listing.objects.filter(seller=request.user, photos__icontains=url)
            if any(url in (listing.photos or []) for listing in candidates):
                default_storage.delete(key)
                return Response(status=status.HTTP_204_NO_CONTENT)

        return Response({"error": {"code": "listing.errPhotoNotOwned"}}, status=status.HTTP_403_FORBIDDEN)

class ListingDetailView(views.APIView):
    permission_classes = [AllowAny]

    def get_object(self, request, pk, require_seller=True):
        try:
            listing = Listing.objects.select_related(
            'book', 'seller', 'seller__school'
            ).get(pk=pk)
            if require_seller and listing.seller != request.user:
                return None
            return listing
        except Listing.DoesNotExist:
            return None

    def get(self, request, pk):
        from core.i18n import resolve_language
        lang = resolve_language(request)
        
        cache_key = versioned_key(f'listing:{pk}', lang)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        listing = self.get_object(request, pk, require_seller=False)
        if not listing:
             return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ListingSerializer(listing, context={'request': request})
        response_data = serializer.data
        cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
        return Response(response_data)

    def patch(self, request, pk):
        listing = self.get_object(request, pk, require_seller=True)
        if not listing:
             return Response(status=status.HTTP_404_NOT_FOUND)
        
        # Handle manual book updates
        if listing.book.source == 'manual':
            book_title = request.data.get('book_title')
            book_authors = request.data.get('book_authors')
            isbn = request.data.get('isbn')
            updated_book = False
            if book_title is not None:
                listing.book.title = book_title
                updated_book = True
            if book_authors is not None:
                listing.book.authors = book_authors
                updated_book = True
            if isbn is not None:
                listing.book.isbn13 = isbn
                updated_book = True
            if updated_book:
                listing.book.save()

        serializer = ListingSerializer(listing, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
             # Cache invalidation (every language variant, the listing lists
             # and the book page) happens in the post_save signal — see
             # listings.models.invalidate_listing_caches.
             serializer.save()
             return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        listing = self.get_object(request, pk, require_seller=True)
        if not listing:
            return Response(status=status.HTTP_404_NOT_FOUND)
        book = listing.book
        listing.delete()  # post_delete signal invalidates the caches

        # If the book now has zero listings and zero subscriptions,
        # it’s an orphan — delete it immediately so the catalog
        # doesn’t accumulate dead entries.
        from django.db.models import Count
        from catalog.models import Book
        orphan = (
            Book.objects
            .filter(id=book.id)
            .annotate(
                listing_count=Count('listings'),
                subscription_count=Count('subscriptions'),
            )
            .filter(
                listing_count=0,
                subscription_count=0,
            )
        )
        orphan.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
