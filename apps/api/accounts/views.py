"""Session authentication endpoints.

DRF's ``APIView`` is csrf_exempt, so ``CsrfViewMiddleware`` does not cover these
views. ``SessionAuthentication`` enforces CSRF only for requests that already
carry a session, which would leave signup and login unprotected — login CSRF
lets an attacker sign a victim into an account the attacker controls. These
views therefore apply ``csrf_protect`` explicitly.
"""

from __future__ import annotations

from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspaces.models import Workspace
from workspaces.serializers import WorkspaceSerializer
from workspaces.services import create_initial_workspace

from .serializers import LoginSerializer, SignupSerializer, UserSerializer


def session_payload(request) -> dict:
    """The shape both signup/login and /me return, so the client has one parser."""
    workspaces = Workspace.objects.filter(memberships__user=request.user).distinct()
    return {
        "user": UserSerializer(request.user).data,
        "workspaces": WorkspaceSerializer(
            workspaces, many=True, context={"request": request}
        ).data,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CSRFView(APIView):
    """Sets the CSRF cookie so the client can send X-CSRFToken on its first POST."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(ensure_csrf_cookie, name="dispatch")
class SignupView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = SignupSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Every new user gets a workspace; V1 has no invitation flow.
        create_initial_workspace(user)

        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return Response(session_payload(request), status=status.HTTP_201_CREATED)


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(ensure_csrf_cookie, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        # Rotates the session key, so a pre-login fixated session cannot be
        # reused after authentication.
        django_login(request, serializer.validated_data["user"])
        return Response(session_payload(request))


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        django_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(session_payload(request))
