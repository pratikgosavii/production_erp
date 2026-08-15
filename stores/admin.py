from django.contrib import admin

from .models import DispatchRecord, Sheet, SheetCut


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


@admin.register(SheetCut)
class SheetCutAdmin(admin.ModelAdmin):
    list_display = (
        'sheet', 'created_at', 'so_item', 'used_note', 'used_sq_in',
        'remnant_note', 'remnant_sq_in', 'offcut', 'cut_by',
    )
    list_filter = ('created_at', 'cut_by')
    search_fields = ('sheet__uid', 'offcut__uid', 'used_note', 'remnant_note')
    raw_id_fields = ('sheet', 'offcut', 'so_item')


@admin.register(DispatchRecord)
class DispatchRecordAdmin(admin.ModelAdmin):
    list_display = ('order', 'ready_at', 'dispatched_at', 'dispatched_by')
    list_filter = ('dispatched_at',)
