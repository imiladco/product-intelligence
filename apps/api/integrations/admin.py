from django.contrib import admin

from .models import IntegrationConnection, OAuthAuthorizationRequest


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


@admin.register(OAuthAuthorizationRequest)
class OAuthAuthorizationRequestAdmin(admin.ModelAdmin):
    """Read-only, and the code verifier is never displayed."""

    list_display = ["created_at", "provider", "project", "user", "expires_at", "consumed_at"]
    list_filter = ["provider"]
    fields = ["provider", "project", "user", "created_at", "expires_at", "consumed_at"]
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


# IntegrationCredential is deliberately NOT registered. Tokens must never be
# viewable in the admin, and a ModelAdmin over it would make them so.
