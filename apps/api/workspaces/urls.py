from rest_framework.routers import DefaultRouter

from .views import WorkspaceViewSet

router = DefaultRouter(trailing_slash=False)
router.register("workspaces", WorkspaceViewSet, basename="workspace")

urlpatterns = router.urls
