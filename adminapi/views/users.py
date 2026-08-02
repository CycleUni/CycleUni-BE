import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import School, User
from core.i18n import resolve_language
from core.models import AuditEvent

from ..serializers import AdminUserSerializer

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

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['lang'] = resolve_language(self.request)
        return context

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

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['lang'] = resolve_language(self.request)
        return context

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

        return Response(AdminUserSerializer(instance, context=self.get_serializer_context()).data)
