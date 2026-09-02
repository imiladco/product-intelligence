from django.contrib import admin

from .models import IntegrationConnection


@admin.register(IntegrationConnection)
class IntegrationConnectionAdmin(admin.ModelAdmin):
    list_display = [
        "project",
        "provider",
        "status",
        "external_resource_label",
        "last_successful_check_at",
    ]
    list_filter = ["provider", "status"]
    search_fields = [
        "project__name",
        "external_resource_id",
        "external_resource_label",
        "google_account_email",
    ]
    autocomplete_fields = ["project", "connected_by"]
    readonly_fields = ["created_at", "updated_at"]
