from django.contrib import admin

from .models import AuditLog, Notification, Sequence


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'object_ref', 'object_type', 'action', 'actor')
    list_filter = ('object_type', 'action')
    search_fields = ('object_ref', 'action', 'detail')
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'recipient', 'message', 'object_ref', 'is_read')
    list_filter = ('is_read',)


@admin.register(Sequence)
class SequenceAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'last_value', 'padding')
