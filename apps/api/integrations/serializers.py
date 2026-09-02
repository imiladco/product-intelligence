from __future__ import annotations

from rest_framework import serializers

from .models import IntegrationConnection


class IntegrationConnectionSerializer(serializers.ModelSerializer):
    """The stored part of an integration entry.

    Fields are listed explicitly rather than with ``__all__``: this model will
    gain neighbours in later milestones, and an explicit list means a new field
    is never exposed by accident.

    Deliberately not exposed: ``granted_scopes`` and ``external_resource_meta``
    (internal detail with no UI use yet) and ``connected_by`` (a user id the
    Integrations page does not render). They can be added when a screen needs
    them.
    """

    class Meta:
        model = IntegrationConnection
        fields = [
            "id",
            "status",
            "external_resource_id",
            "external_resource_label",
            "google_account_email",
            "last_health_check_at",
            "last_successful_check_at",
            "last_error_code",
            "last_error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IntegrationEntrySerializer(serializers.Serializer):
    """One row of the Integrations page: a catalog provider, plus its connection.

    ``connection`` is null when no row exists for this (project, provider), in
    which case ``status`` is the synthesized ``not_connected``.
    """

    provider = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    connection = IntegrationConnectionSerializer(read_only=True, allow_null=True)
