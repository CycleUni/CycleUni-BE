from django.contrib import admin
from django import forms
from django.utils import timezone
from django.contrib.auth.forms import UserChangeForm
from .models import User, School

class UserAdminForm(UserChangeForm):
    manual_is_verified = forms.BooleanField(
        required=False,
        label='已驗證校園信箱 (Is Verified)',
        help_text='開啟此開關可手動將使用者設為已驗證狀態（勾選時會更新 verified_at 為現在時間）。'
    )

    class Meta:
        model = User
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['manual_is_verified'].initial = self.instance.is_verified()

    def save(self, commit=True):
        user = super().save(commit=False)
        is_verified = self.cleaned_data.get('manual_is_verified', False)
        
        if is_verified and not user.verified_at:
            user.verified_at = timezone.now()
        elif not is_verified and user.verified_at:
            user.verified_at = None
            
        if commit:
            user.save()
        return user


from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserAdminForm
    list_display = ('email', 'last_name', 'first_name', 'school', 'edu_email', 'is_verified_status', 'is_active', 'is_staff')
    search_fields = ('email', 'first_name', 'last_name', 'edu_email')
    list_filter = ('is_active', 'is_staff', 'school')
    ordering = ('email',)

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'school', 'edu_email', 'manual_is_verified')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login',)}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password'),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        # Prevent non-superusers from disabling superuser/staff accounts
        if not request.user.is_superuser:
            if obj and obj.is_superuser:
                return ['is_active', 'is_staff', 'is_superuser']
            if obj and obj.is_staff and request.user != obj:
                return ['is_active']
        return super().get_readonly_fields(request, obj)

    @admin.display(boolean=True, description='Verified')
    def is_verified_status(self, obj):
        return obj.is_verified()

@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ('name', 'email_domain')
    search_fields = ('name', 'email_domain')
