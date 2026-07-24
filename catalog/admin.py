from django.contrib import admin
from .models import Book

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ('title', 'isbn13', 'authors', 'publisher')
    search_fields = ('title', 'isbn13', 'authors')
