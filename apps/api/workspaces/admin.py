from django.contrib import admin

from .models import Membership, Workspace


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "created_at"]
    search_fields = ["name", "slug"]
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    """V1 has no invitation UI; members are added here."""

    list_display = ["workspace", "user", "role", "created_at"]
    list_filter = ["role"]
    search_fields = ["workspace__name", "user__email"]
    autocomplete_fields = ["user", "workspace"]
