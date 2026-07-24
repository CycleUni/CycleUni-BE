from django.urls import path
from .views import ReportCreateView, MyReportsView, ReportListView, ReportActionView

urlpatterns = [
    path('', ReportCreateView.as_view(), name='report-create'),
    path('mine/', MyReportsView.as_view(), name='report-mine'),
    path('all/', ReportListView.as_view(), name='report-list'),
    path('<uuid:id>/', ReportActionView.as_view(), name='report-action'),
]
