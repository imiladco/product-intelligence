from __future__ import annotations

from rest_framework import serializers

from .models import Membership, Workspace


class WorkspaceSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ["id", "name", "slug", "role", "created_at"]
        read_only_fields = ["id", "slug", "role", "created_at"]

    def get_role(self, obj: Workspace) -> str | None:
        """The requesting user's role, annotated by the viewset where available."""
        role = getattr(obj, "current_user_role", None)
        if role is not None:
            return role
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return None
        membership = obj.memberships.filter(user=request.user).first()
        return membership.role if membership else None


class WorkspaceCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ["name"]

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name cannot be blank.")
        return value


class MembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user_email", "role", "created_at"]
