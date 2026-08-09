from django.contrib import admin

from .models import SalesOrder, SOItem


class SOItemInline(admin.TabularInline):
    model = SOItem
    extra = 0
    readonly_fields = ('design_approved_at', 'design_approved_by')


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = (
        'number', 'customer', 'quotation', 'status', 'stage_label',
        'health_label', 'expected_delivery', 'order_type',
    )
    list_filter = ('status', 'order_type', 'expected_delivery')
    search_fields = ('number', 'customer__name', 'quotation__number')
    inlines = [SOItemInline]
    readonly_fields = ('number', 'booked_on')


@admin.register(SOItem)
class SOItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'position', 'design_approved', 'material_reserved', 'material_issued')
    list_filter = ('design_approved',)
