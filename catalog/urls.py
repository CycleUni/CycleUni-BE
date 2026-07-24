from django.urls import path
from catalog import views

urlpatterns = [
    path('manual/', views.ManualBookCreateView.as_view(), name='book-manual-create'),
    path('', views.BookDetailView.as_view(), name='book-detail'),
]
