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


class ResourceSelectionSerializer(serializers.Serializer):
    """The body of a resource selection: an identifier, and nothing else.

    Only ``resource_id`` is declared, so a request carrying a label, a status,
    or any other field simply has nowhere for it to land. That is the point: a
    connection's stored label comes from Google's verification response, never
    from the browser, and the shortest way to guarantee it is to make the value
    unreadable rather than to remember to ignore it.

    Format is checked in the provider boundary that owns it, not here — this
    only bounds the size of what reaches it.
    """

    resource_id = serializers.CharField(max_length=255, allow_blank=False, trim_whitespace=True)


class DiscoveredResourceSerializer(serializers.Serializer):
    """One selectable resource, as offered to the picker.

    Built from a RemoteResource, never from a provider response dict: the API
    shape is ours, and does not change because Google adds a field.

    The field names are provider-neutral because one picker renders every
    provider: ``group_label`` is a GA4 account or nothing at all, and
    ``resource_type`` is whatever that provider calls its kinds. Naming them
    after GA4's vocabulary would make the payload readable for one provider and
    misleading for the rest.
    """

    id = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    group_label = serializers.CharField(read_only=True)
    resource_type = serializers.CharField(read_only=True)
