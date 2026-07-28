"""Admin-only REST API backing the Angular admin UI (phase 1: users, listings,
orders). Every view here requires IsAdminUser (request.user.is_staff) — no
exceptions. Granting admin rights (is_staff/is_superuser) stays Django-admin
only, by design; this API can never set those fields (see AdminUserDetailView).
"""
import logging
import os
import time
import datetime as dt
import requests

import jwt

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import School, User
from accounts.views import invalidate_home_static_cache
from core.models import AuditEvent, Category
from listings.models import Listing
from moderation.models import ChatReport
from orders.models import Order
from orders.views import send_order_notification

from .serializers import (
    AdminChatReportSerializer, AdminListingSerializer, AdminOrderSerializer,
    AdminUserSerializer, AdminSchoolSerializer, AdminCategorySerializer,
)

logger = logging.getLogger(__name__)

# is_staff/is_superuser/password/groups/user_permissions must never be settable
# through this API — granting admin rights stays Django-admin-only (security
# decision, not an oversight). Reject explicitly rather than silently dropping,
# so the frontend doesn't think the write succeeded.
FORBIDDEN_USER_FIELDS = {'is_staff', 'is_superuser', 'password', 'groups', 'user_permissions'}


class AdminUserListView(generics.ListAPIView):
    """GET /api/v1/admin/users/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminUserSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = User.objects.select_related('school').order_by('-created_at')
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(edu_email__icontains=q)
            )
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')
        school = self.request.query_params.get('school')
        if school:
            qs = qs.filter(school_id=school)
        return qs


class AdminUserDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/v1/admin/users/<id>/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminUserSerializer
    queryset = User.objects.select_related('school').all()
    lookup_field = 'pk'
    http_method_names = ['get', 'patch']

    def patch(self, request, *args, **kwargs):
        forbidden = FORBIDDEN_USER_FIELDS & set(request.data.keys())
        if forbidden:
            return Response(
                {"error": {"code": "admin.errForbiddenField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        changes = {}

        # Prevent admin from disabling their own account
        if request.data.get('is_active') is False and instance.id == request.user.id:
            return Response(
                {"error": {"code": "admin.errSelfDisable"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if 'is_active' in request.data:
            value = request.data['is_active']
            if not isinstance(value, bool):
                return Response(
                    {"error": {"code": "admin.errInvalidField"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Prevent disabling superuser or staff accounts
            if not value and (instance.is_superuser or instance.is_staff):
                return Response(
                    {"error": {"code": "admin.errForbiddenField"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if instance.is_active != value:
                instance.is_active = value
                changes['is_active'] = value

        if 'school' in request.data:
            school_id = request.data['school']
            new_school = None
            if school_id is not None:
                try:
                    new_school = School.objects.get(pk=school_id)
                except (School.DoesNotExist, ValueError, TypeError):
                    return Response(
                        {"error": {"code": "admin.errInvalidField"}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            new_school_id = new_school.id if new_school else None
            if instance.school_id != new_school_id:
                instance.school = new_school
                changes['school'] = new_school_id

        if 'verified' in request.data:
            value = request.data['verified']
            if not isinstance(value, bool):
                return Response(
                    {"error": {"code": "admin.errInvalidField"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if value and not instance.verified_at:
                instance.verified_at = timezone.now()
                changes['verified_at'] = instance.verified_at.isoformat()
            elif not value and instance.verified_at:
                instance.verified_at = None
                changes['verified_at'] = None

        instance.save()

        AuditEvent.objects.create(
            user=request.user,
            kind='admin.user_updated',
            meta={'target_user_id': instance.id, 'changes': changes},
        )

        return Response(AdminUserSerializer(instance).data)


class AdminListingListView(generics.ListAPIView):
    """GET /api/v1/admin/listings/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminListingSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = Listing.objects.select_related('book', 'seller', 'school').order_by('-created_at')
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(book__title__icontains=q)
                | Q(seller__email__icontains=q)
                | Q(school__name__icontains=q)
            )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        condition = self.request.query_params.get('condition')
        if condition:
            qs = qs.filter(condition=condition)
        school = self.request.query_params.get('school')
        if school:
            qs = qs.filter(school_id=school)
        return qs


class AdminListingDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/v1/admin/listings/<id>/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminListingSerializer
    queryset = Listing.objects.select_related('book', 'seller', 'school').all()
    lookup_field = 'pk'
    http_method_names = ['get', 'patch']

    def patch(self, request, *args, **kwargs):
        allowed_fields = {'status'}
        extra = set(request.data.keys()) - allowed_fields
        if extra:
            return Response(
                {"error": {"code": "admin.errForbiddenField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'status' not in request.data:
            return Response(
                {"error": {"code": "admin.errInvalidField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_status = request.data['status']
        valid_statuses = dict(Listing.STATUS_CHOICES)
        if new_status not in valid_statuses:
            return Response(
                {"error": {"code": "admin.errInvalidStatus"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        old_status = instance.status
        instance.status = new_status
        instance.save(update_fields=['status'])

        AuditEvent.objects.create(
            user=request.user,
            kind='admin.listing_status_changed',
            meta={'listing_id': str(instance.id), 'old_status': old_status, 'new_status': new_status},
        )

        return Response(AdminListingSerializer(instance).data)


class AdminOrderListView(generics.ListAPIView):
    """GET /api/v1/admin/orders/ (read-only)"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminOrderSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = Order.objects.select_related('buyer', 'seller', 'listing', 'listing__book').order_by('-created_at')
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(buyer__email__icontains=q)
                | Q(seller__email__icontains=q)
                | Q(listing__book__title__icontains=q)
            )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs


class AdminOrderDetailView(generics.RetrieveAPIView):
    """GET /api/v1/admin/orders/<id>/ (read-only)"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminOrderSerializer
    queryset = Order.objects.select_related('buyer', 'seller', 'listing', 'listing__book').all()
    lookup_field = 'pk'


class AdminOrderForceCancelView(views.APIView):
    """POST /api/v1/admin/orders/<id>/force_cancel/"""
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk)

        reason = (request.data.get('reason') or '').strip()
        if len(reason) < 3:
            return Response(
                {"error": {"code": "admin.errInvalidReason"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order.status in ('cancelled', 'completed'):
            return Response(
                {"error": {"code": "admin.errOrderAlreadyFinal"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = 'cancelled'
        order.cancel_reason = f'admin_override: {reason}'
        order.save(update_fields=['status', 'cancel_reason', 'updated_at'])

        send_order_notification(order, 'order.notify.admin_cancelled', sender=request.user)

        AuditEvent.objects.create(
            user=request.user,
            kind='admin.order_force_cancelled',
            meta={'order_id': str(order.id), 'reason': reason},
        )

        return Response(AdminOrderSerializer(order).data)


class AdminManagerToggleView(views.APIView):
    """POST /api/v1/admin/managers/<id>/toggle/"""
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        if not request.user.is_superuser:
            return Response(
                {"error": {"code": "admin.errForbiddenAction"}},
                status=status.HTTP_403_FORBIDDEN,
            )

        target_user = get_object_or_404(User, pk=pk)

        if 'is_staff' not in request.data:
            return Response(
                {"error": {"code": "admin.errInvalidField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_is_staff = request.data['is_staff']
        if not isinstance(new_is_staff, bool):
            return Response(
                {"error": {"code": "admin.errInvalidField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if target_user.is_staff != new_is_staff:
            target_user.is_staff = new_is_staff
            target_user.save(update_fields=['is_staff'])

            AuditEvent.objects.create(
                user=request.user,
                kind='admin.manager_toggled',
                meta={'target_user_id': target_user.id, 'is_staff': new_is_staff},
            )

        return Response(AdminUserSerializer(target_user).data)


class AdminSchoolListView(generics.ListCreateAPIView):
    """GET / POST /api/v1/admin/schools/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminSchoolSerializer
    pagination_class = PageNumberPagination
    queryset = School.objects.all().order_by('id')

    def perform_create(self, serializer):
        serializer.save()
        invalidate_home_static_cache()


class AdminSchoolDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET / PATCH / DELETE /api/v1/admin/schools/<id>/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminSchoolSerializer
    queryset = School.objects.all()
    http_method_names = ['get', 'patch', 'delete']

    def perform_update(self, serializer):
        serializer.save()
        invalidate_home_static_cache()

    def perform_destroy(self, instance):
        instance.delete()
        invalidate_home_static_cache()


class AdminSchoolBulkImportView(views.APIView):
    """POST /api/v1/admin/schools/bulk/"""
    permission_classes = [IsAdminUser]

    def post(self, request):
        action = request.data.get('action') # 'preview' or 'apply'
        items = request.data.get('items', [])
        
        if not isinstance(items, list):
            return Response({"error": "Items must be a list"}, status=status.HTTP_400_BAD_REQUEST)
            
        new_items = []
        unchanged_items = []
        modified_items = []
        
        # Build map of existing schools by email_domain
        existing_schools = {s.email_domain: s for s in School.objects.all()}
        
        for idx, item in enumerate(items):
            domain = item.get('email_domain')
            if not domain:
                continue
            
            existing = existing_schools.get(domain)
            if not existing:
                new_items.append(item)
                if action == 'apply':
                    School.objects.create(
                        name=item.get('name', ''),
                        email_domain=domain,
                        translations=item.get('translations', {})
                    )
            else:
                # Check for changes
                name = item.get('name', existing.name)
                translations = item.get('translations', existing.translations)
                
                is_changed = existing.name != name or existing.translations != translations
                if is_changed:
                    modified_items.append({
                        'old': AdminSchoolSerializer(existing).data,
                        'new': item
                    })
                    if action == 'apply':
                        existing.name = name
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


# ── ChatReport admin views ─────────────────────────────────────────────

class AdminChatReportListView(generics.ListAPIView):
    """GET /api/v1/admin/chat-reports/ (read-only list)"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminChatReportSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = ChatReport.objects.select_related(
            'conversation', 'conversation__listing', 'conversation__listing__book',
            'reporter', 'reported_party'
        ).order_by('-created_at')
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs


class AdminChatReportDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/v1/admin/chat-reports/<id>/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminChatReportSerializer
    queryset = ChatReport.objects.select_related(
        'conversation', 'conversation__listing', 'conversation__listing__book',
        'reporter', 'reported_party'
    ).all()
    lookup_field = 'pk'
    http_method_names = ['get', 'patch']

    def patch(self, request, *args, **kwargs):
        allowed_fields = {'status'}
        extra = set(request.data.keys()) - allowed_fields
        if extra:
            return Response(
                {"error": {"code": "admin.errForbiddenField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'status' not in request.data:
            return Response(
                {"error": {"code": "admin.errInvalidField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_status = request.data['status']
        if new_status not in ('open', 'actioned', 'dismissed'):
            return Response(
                {"error": {"code": "admin.errInvalidStatus"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        old_status = instance.status
        instance.status = new_status
        instance.save(update_fields=['status'])

        AuditEvent.objects.create(
            user=request.user,
            kind='admin.chat_report_updated',
            meta={
                'chat_report_id': str(instance.id),
                'old_status': old_status,
                'new_status': new_status,
            },
        )

        return Response(AdminChatReportSerializer(instance).data)


class AdminChatReportTokenView(views.APIView):
    """GET /api/v1/admin/chat-reports/<id>/chat-token/ — issue room-scoped JWT for admin message viewing."""
    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        chat_report = get_object_or_404(ChatReport.objects.select_related('conversation'), pk=pk)
        conv = chat_report.conversation

        edge_jwt_secret = os.getenv('EDGE_CHAT_JWT_SECRET', '')
        if not edge_jwt_secret:
            return Response(
                {"error": {"code": "admin.chatNotConfigured"}},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        app_id = getattr(settings, 'EDGE_CHAT_APP_ID', 'cycleuni')
        payload = {
            'user_id': str(request.user.id),
            'room_id': str(conv.id),
            'app_id': app_id,
            'exp': int(time.time()) + 300,
        }
        token = jwt.encode(payload, edge_jwt_secret, algorithm='HS256')

        edge_chat_url = os.getenv('EDGE_CHAT_URL', '').strip('/')
        return Response({
            'token': token,
            'edge_chat_url': edge_chat_url,
            'room_id': str(conv.id),
        })
