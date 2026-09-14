"""
Delete-behavior tests for CompetencyCriteriaGroup, CompetencyRuleProfile, and CompetencyCriterion.

See ADR-0002 Decision 7 for why each foreign key here is CASCADE or PROTECT, including the two
that assert a different value than issue #641 itself specifies, and see the "residual tension"
section below for the one case where a PROTECT raises naming the wrong object.

Fixtures shared with test_criteria_models.py and test_criteria_trees.py live in this directory's
conftest.py.
"""
import pytest
from django.apps import apps
from django.db import connection
from django.db.models import ProtectedError
from organizations.models import Organization

from openedx_catalog.models import CourseRun
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


# ==============================================================================================
# One test per foreign key. The PROTECT ones inspect `protected_objects`, since more than one
# protected relationship can fire on one delete. The CASCADE ones assert the referencing row
# existed before the delete and is gone after, not just that no exception was raised.
# ==============================================================================================


def test_deleting_a_group_also_deletes_its_child_groups(tag: Tag) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any child group referencing it via `parent`:
    the delete succeeds and the child row is gone too.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()

    root.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()


def test_deleting_a_tag_also_deletes_its_competency_criteria_groups(tag: Tag, group: CompetencyCriteriaGroup) -> None:
    """
    Deleting a Tag cascades to any CompetencyCriteriaGroup referencing it via `tag`: the delete
    succeeds and the group row is gone. Also confirms django-simple-history records the cascaded
    removal as its own historical row (history_type='-'), not silently: an author or auditor
    reviewing history for a group that vanished this way still finds why it did.
    """
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    group_pk = group.pk

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group_pk).exists()

    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    assert historical_group.objects.filter(id=group_pk, history_type="-").exists()


def test_deleting_a_course_run_also_deletes_its_course_scoped_criteria_groups(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun cascades to any CompetencyCriteriaGroup scoped to it via `course`: the
    delete succeeds and the group row is gone too. A course-scoped criteria tree has no meaning
    once the course run it evaluates against no longer exists.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()

    course_run.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


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
    delete succeeds and the profile row is gone too. A CompetencyRuleProfile is never hard-deleted
    by a *direct* delete of the profile itself (ADR-0002 Decision 7); that does not stop it being
    cascaded away as a side effect of deleting the course it is scoped to, once nothing else (no
    CompetencyCriterion still assigned to it) protects it -- a course is only ever hard-deleted
    once nothing beneath it needs protecting.
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
    requires. A CompetencyRuleProfile is never hard-deleted by a *direct* delete of the profile itself
    (ADR-0002 Decision 7); that does not stop it being cascaded away as a side effect of deleting
    the taxonomy it is scoped to, once nothing else protects it. Nothing changes behaviorally in
    this MVP, since only the all-null system-default profile exists otherwise, so this scenario
    cannot arise until a taxonomy-scoped profile is actually created, which no authoring screen
    does yet. See the "residual tension" section below for what happens instead when a
    CompetencyCriterion is still assigned to the scoped profile being cascaded away.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


def test_deleting_a_group_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any CompetencyCriterion referencing it via
    `group`: the delete succeeds and the criterion row is gone too.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    group.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_deleting_an_object_tag_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an ObjectTag cascades to any CompetencyCriterion referencing it via `object_tag`: the
    delete succeeds and the criterion row is gone too. Doubles as the "OURS" half of #641's
    Deletions criterion for oel_tagging_objecttag, since ObjectTag has only this one hop down to
    CompetencyCriterion.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_deleting_a_rule_profile_referenced_by_a_criterion_raises_protected_error(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyRuleProfile that a CompetencyCriterion references via `rule_profile`
    raises ProtectedError.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    with pytest.raises(ProtectedError) as exc_info:
        default_rule_profile.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in protected)


# ==============================================================================================
# Residual tension: see ADR-0002 Decision 7's "Known limitation" bullet for why deleting a
# taxonomy whose scoped profile is assigned to a criterion raises ProtectedError naming that
# criterion, even though the criterion would also be cascade-deleted in the same operation.
# ==============================================================================================


def test_taxonomy_delete_blocked_by_its_scoped_profile_names_the_criterion_not_the_profile(
    competency_taxonomy: CompetencyTaxonomy, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    Deleting a CompetencyTaxonomy whose taxonomy-scoped profile is itself assigned to a criterion
    raises ProtectedError naming the CRITERION, not the profile actually being cascaded away.

    The taxonomy delete cascades into the profile (`competency_taxonomy` is CASCADE), and only then
    discovers the profile is referenced by the criterion via `rule_profile` (PROTECT). Django's
    PROTECT handler raises unconditionally whenever a referencing row exists in the database; it
    never checks whether that same row is also already part of the same delete's pending set, so it
    fires here even though this exact criterion would also be reached and removed via the tag chain
    (Tag -> CompetencyCriteriaGroup.tag -> CompetencyCriterion.group, all CASCADE) if the profile
    hadn't blocked the walk first. This is a spurious, confusing failure -- an author deleting a
    taxonomy is told a criterion is in the way, when nothing about that criterion actually survives
    the delete either -- but it is not reachable in this MVP (see the section header above), so
    this pins the current behavior rather than working around it with a schema change.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)

    with pytest.raises(ProtectedError) as exc_info:
        competency_taxonomy.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in protected)
    assert not any(isinstance(obj, CompetencyRuleProfile) and obj.pk == profile.pk for obj in protected)
    # Nothing was actually removed: the whole operation raised before any DELETE executed.
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


# ==============================================================================================
# MySQL cannot defer foreign-key constraint checks, and Django's CASCADE handler reads that flag
# directly: it nulls a nullable cascading foreign key before the DELETE. On SQLite that nulling
# never happens, so the tests below monkeypatch the flag to reproduce it. Without the monkeypatch
# they pass against broken and correct code alike, so do not drop it.
# ==============================================================================================


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


def test_course_run_delete_cascades_its_course_scoped_criteria_group_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, tag: Tag, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun with a course-scoped CompetencyCriteriaGroup succeeds and cascades the
    group away even under MySQL's non-deferred constraint semantics. `course` is one of the two
    nullable foreign keys this change turns from PROTECT to CASCADE, so it shares the exact
    pre-delete-nulling collector path the taxonomy case above does; unlike scope_code,
    CompetencyCriteriaGroup carries no uniqueness constraint a null `course_id` could collide with,
    so this path is expected to just succeed. Pinned here anyway, alongside the taxonomy case,
    since a future fix to one foreign key without the other would otherwise go unnoticed.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)

    course_run.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_course_run_delete_cascades_its_scoped_rule_profile_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun with a course-scoped CompetencyRuleProfile succeeds and cascades the
    profile away even under MySQL's non-deferred constraint semantics, the same as the taxonomy
    case above: `course` is CompetencyRuleProfile's other newly-CASCADE foreign key, and shares the
    same pre-delete-nulling collector path and the same scope_code collision this fix removes.
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


# ==============================================================================================
# Transitive deletion tests: deleting an oel_tagging.Tag, a CompetencyCriteriaGroup at depth, an
# oel_tagging.ObjectTag, or an oel_tagging.Taxonomy, with no learner status beneath the target,
# must succeed and take the whole referencing criteria tree with it. The other half of each, a
# ProtectedError once a learner status row exists beneath it, needs #642's Student*Status tables
# and is deliberately not stubbed here. See test_criteria_trees.py for the fuller integrative
# version, asserting exactly which rows survive rather than only that a cascade fired.
# ==============================================================================================


def test_tag_delete_with_no_status_cascades_whole_criteria_tree(
    tag: Tag, group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an oel_tagging.Tag with no learner status beneath it succeeds and cascades away
    every CompetencyCriteriaGroup and CompetencyCriterion that references it, transitively:
    Tag -> CompetencyCriteriaGroup.tag (CASCADE) -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_group_delete_at_depth_cascades_descendants_and_their_criteria(
    tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup that is not a root removes it, every descendant group, and
    every CompetencyCriterion under any of them, while leaving the rest of the tree (here, the
    root) alone.

    Builds a genuinely nested tree, root -> child -> grandchild, with criteria at two different
    levels (on `child` and on `grandchild`), so "at depth" and "every descendant" both mean
    something: a shallower tree could pass this by accident.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    grandchild = CompetencyCriteriaGroup.objects.create(tag=tag, parent=child)
    child_criterion = CompetencyCriterion.objects.create(
        group=child, object_tag=object_tag, rule_profile=default_rule_profile
    )
    grandchild_criterion = CompetencyCriterion.objects.create(
        group=grandchild, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()

    child.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()


def test_taxonomy_delete_cascades_every_tag_and_its_criteria(
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Deleting an oel_tagging.Taxonomy collects every Tag beneath it (Tag.taxonomy is CASCADE), so
    the tag-deletion cases above hold transitively through a taxonomy delete too. This asserts the
    succeeding case (no learner status beneath the tag), which is what #641's Deletions criterion
    for taxonomy-level deletion requires "at minimum".

    Chain exercised: CompetencyTaxonomy -> Tag (CASCADE) -> CompetencyCriteriaGroup.tag (CASCADE)
    -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    competency_taxonomy.delete()

    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
