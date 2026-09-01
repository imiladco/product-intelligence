from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from workspaces.models import Workspace

from .models import Project
from .normalization import normalize_website_url


# One message for a workspace that does not exist and for one the user cannot
# reach, so the field is not a workspace-existence oracle.
WORKSPACE_ACCESS_ERROR = "You do not have access to that workspace."


class ProjectSerializer(serializers.ModelSerializer):
    # Declared as a CharField, not the URLField that ModelSerializer would infer:
    # its validator runs before validate_website_url and would reject the
    # scheme-less input ("example.com") that normalization exists to accept.
    website_url = serializers.CharField(max_length=255)
    # required=False so that it can default to the user's sole workspace in
    # validate(); a missing-and-ambiguous workspace is reported there.
    workspace = serializers.PrimaryKeyRelatedField(
        queryset=Workspace.objects.none(),
        required=False,
        error_messages={
            "does_not_exist": WORKSPACE_ACCESS_ERROR,
            "incorrect_type": WORKSPACE_ACCESS_ERROR,
        },
    )

    class Meta:
        model = Project
        fields = [
            "id",
            "workspace",
            "name",
            "website_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        # The model's unique_together would generate a UniqueTogetherValidator
        # that reports under non_field_errors and runs before validate(). The
        # check in validate() reports under "name" instead, which is the field
        # the user can actually correct. The database constraint still stands.
        validators: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The workspace choices are the requesting user's workspaces only, so a
        # client cannot create a project inside a workspace it does not belong
        # to. A foreign id and a nonexistent id produce the same error.
        request = self.context.get("request")
        field = self.fields["workspace"]
        if request is not None and request.user.is_authenticated:
            field.queryset = Workspace.objects.filter(
                memberships__user=request.user
            ).distinct()
        if self.instance is not None:
            # Moving a project between workspaces is not a V1 operation.
            field.read_only = True
            field.required = False

    def validate_website_url(self, value: str) -> str:
        try:
            return normalize_website_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name cannot be blank.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")

        workspace = attrs.get("workspace")
        if workspace is None and self.instance is None:
            workspace = self._default_workspace(request)
            attrs["workspace"] = workspace
        if workspace is None and self.instance is not None:
            workspace = self.instance.workspace

        name = attrs.get("name", getattr(self.instance, "name", None))
        if workspace is not None and name:
            clash = Project.objects.filter(workspace=workspace, name=name)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"name": ["A project with this name already exists in this workspace."]}
                )
        return attrs

    def _default_workspace(self, request) -> Workspace:
        """Allow omitting the workspace when the user has exactly one."""
        if request is None or not request.user.is_authenticated:
            raise serializers.ValidationError({"workspace": ["This field is required."]})
        workspaces = list(
            Workspace.objects.filter(memberships__user=request.user).distinct()[:2]
        )
        if len(workspaces) == 1:
            return workspaces[0]
        raise serializers.ValidationError({"workspace": ["This field is required."]})
