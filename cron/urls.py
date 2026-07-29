from django.urls import path
from cron import views

urlpatterns = [
    path('waitlist-notify/', views.WaitlistNotifyView.as_view(), name='cron-waitlist-notify'),
    path('cleanup/', views.CleanupView.as_view(), name='cron-cleanup'),
]
