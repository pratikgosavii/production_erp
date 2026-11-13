from django.contrib import admin

from .models import (
    Customer, Finish, Grade, ItemCode, ItemCodeRate, Material, MaterialRate,
    ProcessStation, Rack, ReorderLevel, RepeatRate, SheetSize, Supplier, Thickness,
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
        'code', 'customer', 'process', 'spec_line', 'approved_rate',
        'approval_state', 'approved_via', 'approved_on', 'is_active',
    )
    # Process is filterable and editable in place: it decides which checks the
    # designer is prompted for, so it has to be correctable without opening
    # each part one at a time.
    list_filter = ('process', 'approval_state', 'is_active', 'customer')
    list_editable = ('process',)
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


@admin.register(MaterialRate)
class MaterialRateAdmin(admin.ModelAdmin):
    """Cost and benchmark per sq.inch — what the margin panel reads.

    Without a row here the Sales Manager's margin analysis simply says the
    pairing is uncosted, which is the honest answer but not a useful one. The
    panel tells them to add it to the material master, so the material master
    has to be a place they can actually reach.
    """

    list_display = ('material', 'thickness', 'cost_per_sq_in',
                    'benchmark_rate_per_sq_in', 'margin_at_benchmark', 'note')
    list_filter = ('material', 'thickness')
    list_editable = ('cost_per_sq_in', 'benchmark_rate_per_sq_in')
    search_fields = ('material__name', 'thickness__name', 'note')

    @admin.display(description='Margin at benchmark')
    def margin_at_benchmark(self, obj):
        bench = obj.benchmark_rate_per_sq_in
        if not bench:
            return '—'
        return f'{(bench - obj.cost_per_sq_in) / bench * 100:.1f}%'


@admin.register(RepeatRate)
class RepeatRateAdmin(admin.ModelAdmin):
    """Accounts' agreed rate per repeat part. Not banded - see the model."""

    list_display = ('item_code', 'customer', 'rate_per_unit', 'is_active', 'set_by', 'updated_at')
    list_filter = ('is_active', 'item_code__customer')
    list_editable = ('rate_per_unit', 'is_active')
    search_fields = ('item_code__code', 'item_code__customer__name', 'note')
    autocomplete_fields = ('item_code',)

    @admin.display(description='Customer')
    def customer(self, obj):
        return obj.item_code.customer.name


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
