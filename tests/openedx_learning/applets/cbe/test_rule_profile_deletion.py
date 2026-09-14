"""
Delete-behavior tests for CompetencyRuleProfile's own foreign keys.

| Foreign key | Value | Why |
| CompetencyRuleProfile.organization | PROTECT | an Organization is not a competency record |
| CompetencyRuleProfile.course | CASCADE | a course-scoped profile goes with its run |
| CompetencyRuleProfile.competency_taxonomy | CASCADE | a taxonomy-scoped profile goes with its taxonomy |

``on_delete`` expresses containment rather than protection (ADR-0002 Decision 7): it governs
deletion of the row a foreign key points *at*, never the row holding it. A CompetencyRuleProfile
is never hard-deleted by a *direct* delete of the profile itself; retirement is archive-only.
That does not stop it being cascaded away as a side effect of deleting the course or taxonomy it
is scoped to.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.db import connection
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
    `competency_taxonomy`: the delete succeeds and the profile row is gone too, as #641
    requires. Nothing changes behaviorally in this MVP, since only the all-null system-default
    profile exists otherwise, so this scenario cannot arise until a taxonomy-scoped profile is
    actually created, which no authoring screen does yet.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


# ---------------------------------------------------------------------------------------------
# MySQL collector semantics, reproduced on SQLite
# MySQL cannot defer foreign-key constraint checks, and Django's CASCADE handler reads that
# flag directly: it nulls a nullable cascading foreign key before the DELETE. On SQLite that
# nulling never happens, so the tests below monkeypatch the flag to reproduce it. Without the
# monkeypatch they pass against broken and correct code alike, so do not drop it. This is also
# why `scope_code` is a plain column rather than a `GeneratedField`: a generated column would
# recompute from the nulled scope foreign key mid-cascade and collide with whichever row already
# holds the resulting blank scope.


# ---------------------------------------------------------------------------------------------


def test_taxonomy_delete_cascades_its_scoped_profile_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """
    Deleting a CompetencyTaxonomy with a taxonomy-scoped profile succeeds and cascades the profile
    away even under MySQL's non-deferred constraint semantics, the same as it does under ordinary
    SQLite semantics (see test_deleting_a_taxonomy_with_a_scoped_rule_profile_also_deletes_the_
    profile above). Nulling the profile's `competency_taxonomy_id` before deleting it leaves
    `scope_code` alone, so it cannot collide with the seeded system-default profile's identical
    blank scope and raise IntegrityError instead of completing the cascade.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


def test_course_run_delete_cascades_its_scoped_rule_profile_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun with a course-scoped CompetencyRuleProfile succeeds and cascades the
    profile away even under MySQL's non-deferred constraint semantics, the same as the taxonomy
    case above: `course` is CompetencyRuleProfile's other CASCADE foreign key, and shares the same
    pre-delete-nulling collector path and the same scope_code collision this design avoids.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    course_run.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


def test_deleting_two_taxonomies_together_cascades_both_their_scoped_profiles_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Deleting two CompetencyTaxonomy rows in one `.delete()` call, each with its own taxonomy-scoped
    profile, succeeds and cascades both profiles away -- neither profile's scope_code collides with
    the other's, even though both get their `competency_taxonomy_id` nulled in the same collector
    batch under MySQL's non-deferred constraint semantics.

    Same path as the single-taxonomy MySQL case above, but confirms it does not get worse when two
    scope owners are collected in the same collector pass: before scope_code became a plain column,
    nulling both profiles' `competency_taxonomy_id` in the same batch drove both scope_code values
    to the identical blank "org:,course:,taxonomy:" string and raised IntegrityError on whichever
    row the database processed second.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
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
