from django.urls import path
from .views import ActiveAdsListView, AdRecordViewView, AdRecordClickView

urlpatterns = [
    path('active/', ActiveAdsListView.as_view(), name='active-ads-list'),
    path('<int:pk>/view/', AdRecordViewView.as_view(), name='ad-record-view'),
    path('<int:pk>/click/', AdRecordClickView.as_view(), name='ad-record-click'),
]
