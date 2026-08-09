from django.contrib import admin

from .models import JobCard, JobCardItem, JobCardStation


class JobCardItemInline(admin.TabularInline):
    model = JobCardItem
    extra = 0
    autocomplete_fields = ()
    raw_id_fields = ('so_item',)


class JobCardStationInline(admin.TabularInline):
    model = JobCardStation
    extra = 0
    readonly_fields = ('duration_seconds',)


@admin.register(JobCard)
class JobCardAdmin(admin.ModelAdmin):
    list_display = (
        'number', 'item_count', 'primary_order', 'priority', 'is_urgent_split',
        'started_at', 'progress_percent', 'completed_at', 'floor_manager',
    )
    list_filter = ('priority', 'is_urgent_split', 'completed_at')
    search_fields = ('number', 'items__so_item__order__number')
    readonly_fields = ('number',)
    inlines = [JobCardItemInline, JobCardStationInline]


@admin.register(JobCardItem)
class JobCardItemAdmin(admin.ModelAdmin):
    list_display = ('job_card', 'so_item', 'item_code', 'qty')
    search_fields = ('job_card__number', 'so_item__order__number')
    raw_id_fields = ('so_item',)


@admin.register(JobCardStation)
class JobCardStationAdmin(admin.ModelAdmin):
    list_display = ('job_card', 'sequence', 'station', 'status', 'operator', 'duration_display')
    list_filter = ('status',)
    readonly_fields = ('duration_seconds',)
