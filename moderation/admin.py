from django.contrib import admin
from .models import Report

@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('reporter', 'listing', 'status', 'created_at')
    search_fields = ('reporter__email', 'listing__id', 'reason')
    list_filter = ('status', 'created_at')
