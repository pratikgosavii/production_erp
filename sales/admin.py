from django.contrib import admin

from .models import PriceTier, Quotation, QuotationItem


class PriceTierInline(admin.TabularInline):
    model = PriceTier
    extra = 1


class QuotationItemInline(admin.TabularInline):
    model = QuotationItem
    extra = 0
    autocomplete_fields = ('item_code',)


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = (
        'number', 'version', 'customer', 'salesperson', 'status',
        'quote_date', 'grand_total',
    )
    list_filter = ('status', 'order_type', 'quote_date')
    search_fields = ('number', 'customer__name', 'po_number', 'enquiry_ref')
    inlines = [QuotationItemInline]
    readonly_fields = ('number', 'approved_at', 'sent_to_customer_at')

    @admin.display(description='Grand total')
    def grand_total(self, obj):
        return obj.grand_total


@admin.register(QuotationItem)
class QuotationItemAdmin(admin.ModelAdmin):
    list_display = ('quotation', 'position', 'item_code', 'quantity', 'effective_rate', 'line_amount')
    inlines = [PriceTierInline]
    autocomplete_fields = ('item_code',)
