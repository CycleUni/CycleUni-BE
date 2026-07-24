from django.contrib import admin
from .models import AuditEvent

@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('kind', 'user', 'created_at')
    search_fields = ('kind', 'user__email')
    list_filter = ('kind', 'created_at')
