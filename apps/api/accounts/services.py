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

    Either all three rows exist afterwards or none do.

    Raises ``EmailAlreadyRegistered`` only for a genuine collision on the email
    unique constraint — the race the serializer's pre-check cannot close. Any
    other integrity failure propagates as ``IntegrityError``: reporting a broken
    workspace or membership invariant as "that email is taken" would hide a real
    fault behind a validation message the user cannot act on.
    """
    with transaction.atomic():
        try:
            # Inner atomic block = savepoint. A rejected INSERT poisons the
            # transaction, and any further query would raise
            # TransactionManagementError; rolling back to this savepoint leaves
            # the outer transaction usable for the probe below and for the
            # workspace insert.
            with transaction.atomic():
                user = User.objects.create_user(email=email, password=password, name=name)
        except IntegrityError as exc:
            # Establish the cause from the database's own state rather than by
            # parsing driver messages or matching a constraint name: if the
            # address is now taken, this was the email race. Matches the
            # normalization applied by UserManager and User.save().
            if User.objects.filter(email=email.strip().lower()).exists():
                raise EmailAlreadyRegistered from exc
            raise

        workspace = create_initial_workspace(user)

    return user, workspace
