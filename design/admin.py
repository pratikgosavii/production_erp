from django.contrib import admin

from .models import DesignQuery, DrawingRevision, MaterialReservation, QueryMessage


class QueryMessageInline(admin.TabularInline):
    model = QueryMessage
    extra = 0


@admin.register(DesignQuery)
class DesignQueryAdmin(admin.ModelAdmin):
    list_display = ('order', 'subject', 'raised_by', 'assigned_to', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('subject', 'order__number')
    inlines = [QueryMessageInline]


@admin.register(DrawingRevision)
class DrawingRevisionAdmin(admin.ModelAdmin):
    list_display = ('so_item', 'label', 'source', 'uploaded_by', 'is_current', 'created_at')
    list_filter = ('is_current', 'source')


@admin.register(MaterialReservation)
class MaterialReservationAdmin(admin.ModelAdmin):
    """Reserved by Design, issued by Stores — both stamps visible here."""

    list_display = (
        'sheet', 'so_item', 'reserved_by', 'reserved_at',
        'issued_by', 'issued_at', 'released_at',
    )
    list_filter = ('issued_at', 'released_at')
