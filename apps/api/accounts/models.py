from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Email is the login identifier; there is no separate username.

    Defined in the very first migration because swapping AUTH_USER_MODEL after
    the fact is painful.
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        # Emails are case-insensitive in practice; store one canonical form so
        # the unique constraint actually prevents duplicate accounts.
        if self.email:
            self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        return self.name or self.email.split("@")[0]
