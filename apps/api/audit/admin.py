from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: audit records are written by the application, never by hand."""

    list_display = ["created_at", "action", "provider", "workspace", "project", "actor"]
    list_filter = ["action", "provider"]
    search_fields = ["workspace__name", "project__name", "actor__email"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
