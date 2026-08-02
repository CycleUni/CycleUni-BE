import logging

from django.shortcuts import get_object_or_404
from rest_framework import status, views
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import User
from core.models import AuditEvent

from ..serializers import AdminUserSerializer

logger = logging.getLogger(__name__)


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
