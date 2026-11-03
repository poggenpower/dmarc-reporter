from django.contrib import admin

from .models import ImportedMessage, Record, Report


class RecordInline(admin.TabularInline):
    model = Record
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in Record._meta.fields if field.name != "id"]


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("domain", "reporter", "begin", "end")
    list_filter = ("domain", "begin", "policy_disposition")
    search_fields = ("domain", "reporter", "report_id")
    readonly_fields = [field.name for field in Report._meta.fields]
    inlines = [RecordInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportedMessage)
class ImportedMessageAdmin(admin.ModelAdmin):
    list_display = ("folder", "uidvalidity", "uid", "status", "report_count", "processed_at")
    list_filter = ("status", "folder")
    readonly_fields = [field.name for field in ImportedMessage._meta.fields]
    search_fields = ("error",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
