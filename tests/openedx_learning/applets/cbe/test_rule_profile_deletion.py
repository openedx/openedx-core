"""Delete-behavior tests for CompetencyRuleProfile's own foreign keys."""
import pytest
from django.db.models import ProtectedError
from organizations.models import Organization

from openedx_catalog.models import CourseRun
from openedx_learning.models import CompetencyRuleProfile, CompetencyTaxonomy, RuleType

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


# ---------------------------------------------------------------------------------------------
# A profile is never hard-deleted by a direct delete; retirement is an archive. That does not
# stop a profile being cascaded away with the course or taxonomy it is scoped to. The PROTECT
# test inspects the exception's collected objects rather than only catching the exception,
# because several such relationships can fire on one delete.


# ---------------------------------------------------------------------------------------------


def test_deleting_an_organization_with_a_scoped_profile_raises_protected_error_naming_the_profile(
    organization2: Organization,
) -> None:
    """
    Deleting an Organization that a CompetencyRuleProfile references via `organization` raises
    ProtectedError naming the profile.

    Uses `organization2`, which this test never attaches a CatalogCourse to, instead of
    `organization` (the one `course_run` uses elsewhere in this module): CatalogCourse.org is
    itself PROTECT, so deleting an organization with a CatalogCourse attached raises
    ProtectedError regardless of whether a CompetencyRuleProfile references it too, and this
    test would pass for the wrong reason.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization2, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    with pytest.raises(ProtectedError) as exc_info:
        organization2.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyRuleProfile) and obj.pk == profile.pk for obj in protected)


def test_deleting_a_course_run_with_a_scoped_rule_profile_also_deletes_the_profile(
    course_run: CourseRun,
) -> None:
    """
    Deleting a CourseRun cascades to any CompetencyRuleProfile scoped to it via `course`: the
    delete succeeds and the profile row is gone too.
    """
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()

    course_run.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


def test_deleting_a_taxonomy_with_a_scoped_rule_profile_also_deletes_the_profile(
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    Deleting a CompetencyTaxonomy cascades to any CompetencyRuleProfile scoped to it via
    `competency_taxonomy`: the delete succeeds and the profile row is gone too.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


# ---------------------------------------------------------------------------------------------
# MySQL collector semantics
# MySQL cannot defer foreign-key constraint checks, and Django's CASCADE handler reads that
# flag directly: it nulls a nullable cascading foreign key before the DELETE. On SQLite that
# nulling never happens, so a regression in this path fails in CI (which runs MySQL) but can
# still pass locally on SQLite. This is also why `scope_code` is a plain column rather than a
# `GeneratedField`: a generated column would recompute from the nulled scope foreign key
# mid-cascade and collide with whichever row already holds the resulting blank scope.


# ---------------------------------------------------------------------------------------------


def test_deleting_two_taxonomies_together_cascades_both_their_scoped_profiles_away() -> None:
    """
    Deleting two CompetencyTaxonomy rows in one `.delete()` call, each with its own taxonomy-scoped
    profile, succeeds and cascades both away, exercising the collector's multi-row nulling path
    rather than the single-row path test_deleting_a_taxonomy_with_a_scoped_rule_profile_also_
    deletes_the_profile above covers.
    """
    taxonomy1 = CompetencyTaxonomy.objects.create(name="Nursing Two Taxonomy Delete", export_id="nursing-two-del")
    taxonomy2 = CompetencyTaxonomy.objects.create(name="Welding Two Taxonomy Delete", export_id="welding-two-del")
    profile1 = CompetencyRuleProfile.objects.create(
        competency_taxonomy=taxonomy1, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile2 = CompetencyRuleProfile.objects.create(
        competency_taxonomy=taxonomy2, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    CompetencyTaxonomy.objects.filter(pk__in=[taxonomy1.pk, taxonomy2.pk]).delete()

    assert not CompetencyRuleProfile.objects.filter(pk__in=[profile1.pk, profile2.pk]).exists()
