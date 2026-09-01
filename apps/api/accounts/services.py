"""Account registration.

Signup persists three rows — User, Workspace, Membership — and they only make
sense together: a User with no workspace can sign in but has nowhere to put a
project, and nothing in the product creates the missing workspace later. So the
whole operation is one transaction.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from workspaces.models import Workspace
from workspaces.services import create_initial_workspace

User = get_user_model()


class EmailAlreadyRegistered(Exception):
    """The email was taken between the serializer's check and the insert."""


def register_user(*, email: str, password: str, name: str = "") -> tuple[User, Workspace]:
    """Create a user with their initial workspace, atomically.

    Either all three rows exist afterwards or none do. Raises
    ``EmailAlreadyRegistered`` when the unique constraint rejects the insert,
    which is the race the serializer's pre-check cannot close.
    """
    try:
        # The IntegrityError must be caught outside the atomic block: once the
        # database rejects a statement the transaction is unusable, and any
        # further query inside the block would fail with TransactionManagementError.
        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password, name=name)
            workspace = create_initial_workspace(user)
    except IntegrityError as exc:
        raise EmailAlreadyRegistered from exc
    return user, workspace
