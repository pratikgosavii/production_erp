from django.contrib import admin

from .models import (
    Customer, Finish, Grade, ItemCode, ItemCodeRate, Material, ProcessStation,
    Rack, ReorderLevel, SheetSize, Supplier, Thickness,
)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_person', 'phone', 'email', 'gstin', 'credit_limit', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'gstin', 'contact_person', 'email')


class ItemCodeRateInline(admin.TabularInline):
    """The rate card, band by band — this is the source of truth for pricing."""

    model = ItemCodeRate
    extra = 0
    fields = ('qty_from', 'qty_to', 'rate_per_unit', 'approved_rate',
              'proposed_rate', 'approval_state', 'approved_via', 'approved_on')
    readonly_fields = ('approved_via', 'approved_on')


@admin.register(ItemCode)
class ItemCodeAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'customer', 'spec_line', 'approved_rate', 'approval_state',
        'approved_via', 'approved_on', 'is_active',
    )
    list_filter = ('approval_state', 'is_active', 'customer')
    search_fields = ('code', 'description', 'customer__name')
    autocomplete_fields = ('customer',)
    readonly_fields = ('approved_on', 'approved_by', 'approved_via')
    inlines = [ItemCodeRateInline]


@admin.register(ItemCodeRate)
class ItemCodeRateAdmin(admin.ModelAdmin):
    list_display = (
        'item_code', 'qty_from', 'qty_to', 'approved_rate', 'proposed_rate',
        'approval_state', 'approved_via', 'approved_on',
    )
    list_filter = ('approval_state', 'approval_source', 'item_code__customer')
    search_fields = ('item_code__code', 'item_code__customer__name', 'approved_via')
    autocomplete_fields = ('item_code',)
    readonly_fields = ('approved_on', 'approved_by')


@admin.register(ProcessStation)
class ProcessStationAdmin(admin.ModelAdmin):
    list_display = ('sequence', 'name', 'is_active')
    list_editable = ('is_active',)
    ordering = ('sequence',)


@admin.register(ReorderLevel)
class ReorderLevelAdmin(admin.ModelAdmin):
    """§10.2·6 — reorder benchmarks live here, not on the Stores screen."""

    list_display = ('material', 'thickness', 'minimum_qty')
    list_filter = ('material',)


@admin.register(SheetSize)
class SheetSizeAdmin(admin.ModelAdmin):
    list_display = ('name', 'length_mm', 'width_mm', 'is_active')


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'address', 'is_active')
    search_fields = ('name',)


@admin.register(Thickness)
class ThicknessAdmin(admin.ModelAdmin):
    list_display = ('name', 'value_mm', 'is_active')


for model in (Material, Grade, Finish, Rack):
    admin.site.register(model, type(
        f'{model.__name__}Admin', (admin.ModelAdmin,),
        {'list_display': ('name', 'is_active'), 'search_fields': ('name',)},
    ))
