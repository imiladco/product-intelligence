from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "workspace", "website_url", "created_at"]
    list_filter = ["workspace"]
    search_fields = ["name", "website_url"]
    autocomplete_fields = ["workspace", "created_by"]
