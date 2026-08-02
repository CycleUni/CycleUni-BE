import logging

from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.views import invalidate_home_static_cache
from core.models import Category

from ..serializers import AdminCategorySerializer

logger = logging.getLogger(__name__)


class AdminCategoryListView(generics.ListCreateAPIView):
    serializer_class = AdminCategorySerializer
    permission_classes = [IsAdminUser]
    pagination_class = PageNumberPagination

    def get_queryset(self):
        return Category.objects.all().order_by('sort_order', 'id')

    def perform_create(self, serializer):
        serializer.save()
        invalidate_home_static_cache()


class AdminCategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Category.objects.all()
    serializer_class = AdminCategorySerializer
    permission_classes = [IsAdminUser]
    http_method_names = ['get', 'patch', 'delete']

    def perform_update(self, serializer):
        serializer.save()
        invalidate_home_static_cache()

    def perform_destroy(self, instance):
        instance.delete()
        invalidate_home_static_cache()


class AdminCategoryBulkImportView(views.APIView):
    """POST /api/v1/admin/categories/bulk/"""
    permission_classes = [IsAdminUser]

    def post(self, request):
        action = request.data.get('action') # 'preview' or 'apply'
        items = request.data.get('items', [])

        if not isinstance(items, list):
            return Response({"error": "Items must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        new_items = []
        unchanged_items = []
        modified_items = []

        existing_cats = {c.slug: c for c in Category.objects.all()}

        for item in items:
            slug = item.get('slug')
            if not slug:
                continue

            existing = existing_cats.get(slug)
            if not existing:
                new_items.append(item)
                if action == 'apply':
                    Category.objects.create(
                        slug=slug,
                        title=item.get('title', ''),
                        description=item.get('description', ''),
                        sort_order=item.get('sort_order', 0),
                        is_active=item.get('is_active', True),
                        translations=item.get('translations', {})
                    )
            else:
                title = item.get('title', existing.title)
                description = item.get('description', existing.description)
                sort_order = item.get('sort_order', existing.sort_order)
                is_active = item.get('is_active', existing.is_active)
                translations = item.get('translations', existing.translations)

                is_changed = (
                    existing.title != title or
                    existing.description != description or
                    existing.sort_order != sort_order or
                    existing.is_active != is_active or
                    existing.translations != translations
                )

                if is_changed:
                    modified_items.append({
                        'old': AdminCategorySerializer(existing).data,
                        'new': item
                    })
                    if action == 'apply':
                        existing.title = title
                        existing.description = description
                        existing.sort_order = sort_order
                        existing.is_active = is_active
                        existing.translations = translations
                        existing.save()
                else:
                    unchanged_items.append(item)

        if action == 'apply' and (new_items or modified_items):
            invalidate_home_static_cache()

        return Response({
            "new": new_items,
            "modified": modified_items,
            "unchanged": unchanged_items
        })
