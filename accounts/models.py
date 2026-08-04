from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.contrib.postgres.indexes import GinIndex
from django.utils import timezone


class School(models.Model):
    """School registry, used to derive a user's school from their email domain.

    `name` is the canonical (English) name. Localized names live in
    `translations`, keyed by language code:
    {"zh-TW": {"name": "國立台灣大學"}, "ja": {"name": "..."}}
    """
    email_domain = models.CharField(max_length=255, unique=True, help_text="例如: ntu.edu.tw")
    name = models.CharField(max_length=255, help_text="Canonical English name, e.g. National Taiwan University")
    translations = models.JSONField(default=dict, blank=True, help_text='Localized fields per language, e.g. {"zh-TW": {"name": "國立台灣大學"}}')

    def localized_name(self, lang):
        """Name in the requested language, falling back to the canonical name."""
        from core.i18n import pick_translation
        return pick_translation(self.translations, lang).get('name') or self.name

    def __str__(self):
        return self.name

    class Meta:
        indexes = [
            GinIndex(
                name='school_trgm_idx',
                fields=['name', 'email_domain'],
                opclasses=['gin_trgm_ops', 'gin_trgm_ops']
            )
        ]



class UserManager(BaseUserManager):
    def create_user(self, email, first_name, last_name, password=None, **extra_fields):
        if not email:
            raise ValueError('Users must have an email address')
        
        email = self.normalize_email(email)
        user = self.model(
            email=email,
            first_name=first_name,
            last_name=last_name,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, first_name, last_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, first_name, last_name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom user model keyed by email, linked to the user's school."""
    email = models.EmailField(unique=True, help_text="註冊信箱，可為任意信箱")
    edu_email = models.EmailField(null=True, blank=True, help_text="已驗證的校園信箱")
    school = models.ForeignKey(School, on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    first_name = models.CharField(max_length=150, default='')
    last_name = models.CharField(max_length=150, default='')
    avatar_url = models.URLField(max_length=500, null=True, blank=True, help_text="頭貼網址")
    
    verified_at = models.DateTimeField(null=True, blank=True, help_text="首次通過 .edu.tw 驗證時間")
    last_reverified_at = models.DateTimeField(null=True, blank=True, help_text="上一次重新驗證在校生身分的時間")
    
    last_seen_bought_orders_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the most recent bought order seen by the user")
    last_seen_sold_orders_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the most recent sold order seen by the user")
    
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    class Meta:
        indexes = [
            GinIndex(
                name='user_trgm_idx',
                fields=['email', 'first_name', 'last_name', 'edu_email'],
                opclasses=['gin_trgm_ops', 'gin_trgm_ops', 'gin_trgm_ops', 'gin_trgm_ops']
            )
        ]

    def __str__(self):
        return self.email

    def is_verified(self):
        """Whether the user has completed .edu.tw email verification."""
        return self.verified_at is not None

    @property
    def display_name(self):
        last = self.last_name or ''
        first = self.first_name or ''
        if last and first:
            import re
            if re.search(r'[A-Za-z]', last) or re.search(r'[A-Za-z]', first):
                return f"{first} {last}"
            return f"{last}{first}"
        return f"{last}{first}".strip()

    @property
    def average_rating(self):
        reviews = self.reviews_received.filter(is_no_show=False, rating__isnull=False)
        if not reviews.exists():
            return 0.0
        from django.db.models import Avg
        return round(reviews.aggregate(Avg('rating'))['rating__avg'], 1)

    @property
    def review_count(self):
        return self.reviews_received.filter(is_no_show=False, rating__isnull=False).count()

    @property
    def no_show_count(self):
        return self.reviews_received.filter(is_no_show=True).count()
