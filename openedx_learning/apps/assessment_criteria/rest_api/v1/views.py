"""
Assessment criteria API v1 views.
"""
from rest_framework import viewsets

from ...models import (
    AssessmentCriteria,
    AssessmentCriteriaGroup,
    StudentAssessmentCriteriaStatus,
    StudentCompetencyStatus,
)
from .serializers import (
    AssessmentCriteriaGroupSerializer,
    AssessmentCriteriaSerializer,
    StudentAssessmentCriteriaStatusSerializer,
    StudentCompetencyStatusSerializer,
)


class AssessmentCriteriaGroupView(viewsets.ModelViewSet):
    """
    CRUD for AssessmentCriteriaGroup.
    """

    queryset = AssessmentCriteriaGroup.objects.all().order_by("ordering", "id")
    serializer_class = AssessmentCriteriaGroupSerializer


class AssessmentCriteriaView(viewsets.ModelViewSet):
    """
    CRUD for AssessmentCriteria.
    """

    queryset = AssessmentCriteria.objects.all().order_by("id")
    serializer_class = AssessmentCriteriaSerializer

    def get_queryset(self):
        """
        Optionally filter by criteria group via ?group=<id>.
        """
        queryset = super().get_queryset()
        group_id = self.request.query_params.get("group")
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        return queryset


class StudentAssessmentCriteriaStatusView(viewsets.ModelViewSet):
    """
    CRUD for StudentAssessmentCriteriaStatus.
    """

    queryset = StudentAssessmentCriteriaStatus.objects.all().order_by("-timestamp", "id")
    serializer_class = StudentAssessmentCriteriaStatusSerializer


class StudentCompetencyStatusView(viewsets.ModelViewSet):
    """
    CRUD for StudentCompetencyStatus.
    """

    queryset = StudentCompetencyStatus.objects.all().order_by("-timestamp", "id")
    serializer_class = StudentCompetencyStatusSerializer
