from django.utils import timezone
from rest_framework import generics, views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from django.db.models import F, Q
from django.shortcuts import get_object_or_404

from ads.models import Ad
from ads.serializers import PublicAdSerializer
from adminapi.pagination import AdminPagination

class ActiveAdsListView(generics.ListAPIView):
    """GET /api/v1/ads/active/"""
    permission_classes = [AllowAny]
    serializer_class = PublicAdSerializer
    pagination_class = AdminPagination

    def get_queryset(self):
        now = timezone.now()
        qs = Ad.objects.select_related('advertiser').filter(
            is_active=True,
            start_date__lte=now,
            end_date__gte=now,
            advertiser__is_active=True,
        ).order_by('-created_at')
        
        position = self.request.query_params.get('position')
        if position:
            qs = qs.filter(position=position)
            
        school_name = self.request.query_params.get('school')
        if school_name:
            qs = qs.filter(
                Q(advertiser__all_schools=True) |
                Q(advertiser__schools__name=school_name)
            ).distinct()
        else:
            qs = qs.filter(advertiser__all_schools=True)
            
        return qs

class AdRecordViewView(views.APIView):
    """POST /api/v1/ads/<id>/view/"""
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'ad_stats'

    def post(self, request, pk):
        # We don't restrict to active only in case an ad was just disabled but still rendered
        updated = Ad.objects.filter(pk=pk).update(views_count=F('views_count') + 1)
        if not updated:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

class AdRecordClickView(views.APIView):
    """POST /api/v1/ads/<id>/click/"""
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'ad_stats'

    def post(self, request, pk):
        # We don't restrict to active only in case an ad was just disabled but still rendered
        updated = Ad.objects.filter(pk=pk).update(clicks_count=F('clicks_count') + 1)
        if not updated:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
