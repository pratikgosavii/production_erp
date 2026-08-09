from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User

ROLE_FIELDS = [flag for flag, _ in User.ROLE_FLAGS]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'employee_id', 'get_full_name', 'primary_role', 'is_active')
    list_filter = tuple(ROLE_FIELDS) + ('is_active', 'is_staff')
    search_fields = ('username', 'employee_id', 'first_name', 'last_name', 'email')

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Employee', {'fields': ('employee_id', 'phone')}),
        ('ERP roles', {'fields': tuple(ROLE_FIELDS)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Employee', {'fields': ('employee_id', 'phone')}),
        ('ERP roles', {'fields': tuple(ROLE_FIELDS)}),
    )
