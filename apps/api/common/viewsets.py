"""Tenant-scoped viewset base.

Tenant isolation is the single most important invariant in this codebase, so it
is implemented once, here, and made the path of least resistance: a subclass
that forgets to define its tenant filter raises at import time rather than
quietly serving another workspace's rows.

Rules enforced by this base class:

* The queryset is always derived from ``request.user``'s memberships. A
  workspace id supplied by the client is only ever an additional *narrowing*
  filter, never the source of authority.
* A row outside the user's workspaces is indistinguishable from a row that does
  not exist: both produce 404, so the API discloses nothing about other tenants.
"""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import viewsets


class TenantScopedViewSetMixin:
    """Requires subclasses to define ``tenant_queryset(user)``."""

    #: Set to True on intermediate base classes that should not be checked.
    tenant_scope_abstract = False

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("tenant_scope_abstract", False):
            return
        # getattr always finds the base implementation below, so compare
        # identity: the subclass must actually override it.
        if cls.tenant_queryset is TenantScopedViewSetMixin.tenant_queryset:
            raise TypeError(
                f"{cls.__name__} must define tenant_queryset(self, user) so that "
                "its queryset is derived from the request user's memberships."
            )

    def tenant_queryset(self, user) -> QuerySet:  # pragma: no cover - overridden
        raise NotImplementedError

    def get_queryset(self) -> QuerySet:
        user = self.request.user
        if not user.is_authenticated:
            # DRF's IsAuthenticated runs before this in normal use; returning an
            # empty queryset keeps the class safe if it is ever reused without
            # that permission class.
            return self.queryset_model_none()
        return self.tenant_queryset(user)

    def queryset_model_none(self) -> QuerySet:
        meta = getattr(getattr(self, "serializer_class", None), "Meta", None)
        model = getattr(meta, "model", None)
        if model is None:
            raise NotImplementedError(
                "Cannot determine the model for an empty queryset; set serializer_class."
            )
        return model.objects.none()


class TenantScopedModelViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    tenant_scope_abstract = True
