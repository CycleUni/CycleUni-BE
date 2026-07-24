from django.urls import path
from subscriptions import views

urlpatterns = [
    path('', views.SubscriptionListView.as_view(), name='subscription-list'),
    path('<uuid:id>/', views.SubscriptionDetailView.as_view(), name='subscription-detail'),
]
