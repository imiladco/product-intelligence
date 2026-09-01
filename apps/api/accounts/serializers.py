from __future__ import annotations

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name", "date_joined"]
        read_only_fields = fields


# One message for the serializer's pre-check and for the database's unique
# constraint, so a lost race looks identical to an ordinary duplicate.
EMAIL_TAKEN_MESSAGE = "An account with this email already exists."


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    name = serializers.CharField(required=False, allow_blank=True, max_length=150)

    def validate_email(self, value: str) -> str:
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(EMAIL_TAKEN_MESSAGE)
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Run Django's validators against a non-persisted user so that
        # similarity-to-email checks work.
        candidate = User(email=attrs["email"], name=attrs.get("name", ""))
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    # No create(): registration goes through accounts.services.register_user,
    # the single path that persists the user and their workspace atomically.


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = authenticate(
            request=request,
            username=attrs["email"].strip().lower(),
            password=attrs["password"],
        )
        if user is None:
            # One message for both wrong-email and wrong-password so the
            # endpoint is not an account-existence oracle.
            raise serializers.ValidationError("Incorrect email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is disabled.")
        attrs["user"] = user
        return attrs
