from django.urls import path
from listings import views

urlpatterns = [
    path('', views.ListingListCreateView.as_view(), name='listing-list-create'),
    path('recent_books/', views.RecentBooksView.as_view(), name='listing-recent-books'),
    path('<uuid:pk>/', views.ListingDetailView.as_view(), name='listing_detail'),
    path('uploads/', views.ListingUploadURLView.as_view(), name='listing-uploads'),
    path('uploads/direct/', views.ListingUploadDirectView.as_view(), name='listing-uploads-direct'),
    path('uploads/delete/', views.ListingUploadDeleteView.as_view(), name='listing-uploads-delete'),
]
