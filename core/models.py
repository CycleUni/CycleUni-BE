from django.db import models
from django.conf import settings


class Category(models.Model):
    """Homepage college category (was hardcoded mock data).

    `title`/`description` are the canonical English values; localized values
    live in `translations`, e.g. {"zh-TW": {"title": "商管學院",
    "description": "經濟、會計、企管"}}.
    """
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    translations = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name_plural = 'categories'

    def localized(self, lang):
        from core.i18n import pick_translation
        translated = pick_translation(self.translations, lang)
        return {
            'slug': self.slug,
            'title': translated.get('title') or self.title,
            'desc': translated.get('description') or self.description,
        }

    def __str__(self):
        return self.title


class AuditEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=100)
    meta = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"AuditEvent {self.kind} by {self.user}"
