from django.urls import path
from search import views

urlpatterns = [
    path('books/', views.BookSearchView.as_view(), name='search-books'),
]
