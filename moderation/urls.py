from django.urls import path
from .views import (
    ReportCreateView, MyReportsView, ReportListView, ReportActionView,
    ChatReportCreateView, MyChatReportsView, ChatReportListView, ChatReportActionView, ChatReportDetailView,
)

urlpatterns = [
    path('', ReportCreateView.as_view(), name='report-create'),
    path('mine/', MyReportsView.as_view(), name='report-mine'),
    path('all/', ReportListView.as_view(), name='report-list'),
    path('<uuid:id>/', ReportActionView.as_view(), name='report-action'),
    path('chat-reports/', ChatReportCreateView.as_view(), name='chat-report-create'),
    path('chat-reports/mine/', MyChatReportsView.as_view(), name='chat-report-mine'),
    path('chat-reports/all/', ChatReportListView.as_view(), name='chat-report-list'),
    path('chat-reports/<uuid:id>/', ChatReportDetailView.as_view(), name='chat-report-detail'),
    path('chat-reports/<uuid:id>/action/', ChatReportActionView.as_view(), name='chat-report-action'),
]
