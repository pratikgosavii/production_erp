from django.contrib import admin

from .models import (
    Customer, Finish, Grade, ItemCode, Material, ProcessStation, Rack,
    ReorderLevel, SheetSize, Supplier, Thickness,
)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_person', 'phone', 'email', 'gstin', 'credit_limit', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'gstin', 'contact_person', 'email')


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
