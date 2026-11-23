from django.contrib import admin

from .models import Break, Event, Programme, ProgrammeItem, Speaker, Talk


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'start_date', 'end_date')
    search_fields = ('name',)
    list_filter = ('start_date',)


@admin.register(Speaker)
class SpeakerAdmin(admin.ModelAdmin):
    list_display = ('name', 'affiliation', 'requests_sponsorship')
    list_filter = ('requests_sponsorship',)
    search_fields = ('name', 'affiliation')
    filter_horizontal = ('talks',)


@admin.register(Talk)
class TalkAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'speaker', 'status', 'submitted_at')
    list_filter = ('status', 'event')
    search_fields = ('title', 'speaker__name', 'event__name')
    autocomplete_fields = ('event', 'speaker')


class BreakInline(admin.TabularInline):
    model = Break
    extra = 0
    fields = ('name', 'start_time', 'end_time')


class ProgrammeItemInline(admin.TabularInline):
    model = ProgrammeItem
    extra = 0
    fields = ('position', 'talk', 'brk')
    ordering = ('position',)


@admin.register(Programme)
class ProgrammeAdmin(admin.ModelAdmin):
    list_display = ('name', 'event')
    list_filter = ('event',)
    search_fields = ('name', 'event__name')
    autocomplete_fields = ('event',)
    inlines = [BreakInline, ProgrammeItemInline]


@admin.register(Break)
class BreakAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'programme', 'start_time', 'end_time')
    list_filter = ('programme__event', 'programme')
    search_fields = ('name', 'programme__name')
    autocomplete_fields = ('programme',)
