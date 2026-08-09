from django.contrib import admin

from .models import DispatchRecord, Sheet


@admin.register(Sheet)
class SheetAdmin(admin.ModelAdmin):
    list_display = (
        'uid', 'material', 'thickness', 'size_display', 'status',
        'rack', 'origin', 'received_on', 'is_offcut',
    )
    list_filter = ('status', 'is_offcut', 'material', 'thickness')
    search_fields = ('uid', 'material__name', 'supplier__name')
    readonly_fields = ('uid',)
    autocomplete_fields = ('supplier',)


@admin.register(DispatchRecord)
class DispatchRecordAdmin(admin.ModelAdmin):
    list_display = ('order', 'ready_at', 'dispatched_at', 'dispatched_by')
    list_filter = ('dispatched_at',)
