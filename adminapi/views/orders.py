import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.models import AuditEvent
from orders.models import Order
from orders.views import send_order_notification

from ..serializers import AdminOrderSerializer

logger = logging.getLogger(__name__)


class AdminOrderListView(generics.ListAPIView):
    """GET /api/v1/admin/orders/ (read-only)"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminOrderSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = Order.objects.select_related('buyer', 'seller', 'listing', 'listing__book').order_by('-created_at')
        q = self.request.query_params.get('q')
        if q:
            q_clean = q.lstrip('#').strip()
            qs = qs.filter(
                Q(id__icontains=q_clean)
                | Q(buyer__email__icontains=q)
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
