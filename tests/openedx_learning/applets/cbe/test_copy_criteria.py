"""
Tests for copy_competency_criteria_for_course_rerun().
"""
import logging
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from opaque_keys.edx.keys import CourseKey

from openedx_catalog.api import create_course_run_for_modulestore_course_with
from openedx_catalog.models import CourseRun
from openedx_learning.api import CompetencyCriteriaCopyResult, copy_competency_criteria_for_course_rerun
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

pytestmark = pytest.mark.django_db

SOURCE_KEY = CourseKey.from_string("course-v1:Org1+Python100+Fall2026")
DESTINATION_KEY = CourseKey.from_string("course-v1:Org1+Python100+Spring2027")
DESTINATION_TITLE = "Python 100, Spring 2027"
OVERRIDE_PAYLOAD = {"op": "gte", "value": 0.6, "scale": "percent"}


@pytest.fixture(name="destination_run")
def _destination_run(course_run: CourseRun) -> CourseRun:
    """A second run of `course_run`'s course, the target of the copy."""
    assert course_run.course_key == SOURCE_KEY
    return create_course_run_for_modulestore_course_with(DESTINATION_KEY, title=DESTINATION_TITLE)


def _object_tag(run: CourseRun, block: str, tag: Tag) -> ObjectTag:
    """Tag the problem `block` of `run` with `tag`."""
    assert run.course_key is not None
    return ObjectTag.objects.create(
        object_id=str(run.course_key.make_usage_key("problem", block)), taxonomy=tag.taxonomy, tag=tag,
    )


def _tag_on_both_runs(block: str, tag: Tag, source: CourseRun, destination: CourseRun) -> ObjectTag:
    """Tag `block` on both runs, as the rerun's tag copy does, and return the source ObjectTag."""
    _object_tag(destination, block, tag)
    return _object_tag(source, block, tag)


def _course_level_group(tag: Tag, run: CourseRun, **fields: Any) -> CompetencyCriteriaGroup:
    """Return a new course-level group for `tag` on `run`, creating the root if needed."""
    root, _ = CompetencyCriteriaGroup.objects.get_or_create(tag=tag, parent=None, defaults={"name": "root"})
    return CompetencyCriteriaGroup.objects.create(tag=tag, course=run, parent=root, name="course group", **fields)


def _child_group(parent: CompetencyCriteriaGroup, **fields: Any) -> CompetencyCriteriaGroup:
    """Return a new group under `parent`."""
    return CompetencyCriteriaGroup.objects.create(tag=parent.tag, parent=parent, **fields)


def _criterion(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, profile: CompetencyRuleProfile | None, **fields: Any,
) -> CompetencyCriterion:
    """Return a new criterion under `group`, on `profile` unless overrides are given."""
    return CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile, **fields)


def _copy(source: CourseRun, destination: CourseRun, **kwargs: Any) -> CompetencyCriteriaCopyResult:
    """Run the copy from `source` to `destination`."""
    assert source.course_key is not None
    assert destination.course_key is not None
    return copy_competency_criteria_for_course_rerun(source.course_key, destination.course_key, **kwargs)


def _destination_course_level_group(tag: Tag, destination: CourseRun) -> CompetencyCriteriaGroup:
    """Return the course-level group the copy created for `tag` on `destination`."""
    return CompetencyCriteriaGroup.objects.get(tag=tag, course=destination)


def _children(group: CompetencyCriteriaGroup) -> list[CompetencyCriteriaGroup]:
    """Return `group`'s child groups in sibling order."""
    return list(group.child_groups.order_by("ordering", "id"))


def _snapshot() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return every group and criterion row as dicts, for comparing before and after a copy."""
    return (
        [dict(row) for row in CompetencyCriteriaGroup.objects.order_by("id").values()],
        [dict(row) for row in CompetencyCriterion.objects.order_by("id").values()],
    )


def _history_ids(model_name: str, ids: Any) -> list[int]:
    """Return the row id of each `model_name` history row for `ids`, once per history row, sorted."""
    historical_model = apps.get_model("openedx_learning", f"Historical{model_name}")
    return sorted(historical_model.objects.filter(id__in=ids).values_list("id", flat=True))


def _history_count(model_name: str) -> int:
    """Return how many `model_name` history rows exist."""
    return apps.get_model("openedx_learning", f"Historical{model_name}").objects.count()


@pytest.fixture(name="second_tag")
def _second_tag(competency_taxonomy: CompetencyTaxonomy) -> Tag:
    """A second competency in `competency_taxonomy`."""
    return Tag.objects.create(taxonomy=competency_taxonomy, value="Reading Prose")


@pytest.mark.parametrize("destination_exists", [True, False])
def test_source_run_without_catalog_row_copies_nothing(
    request: pytest.FixtureRequest, destination_exists: bool,
) -> None:
    """A source key with no CourseRun copies nothing, and does not fail on a missing destination either."""
    if destination_exists:
        request.getfixturevalue("destination_run")
    source_key = CourseKey.from_string("course-v1:Org9+NoCourse+NoRun")

    result = copy_competency_criteria_for_course_rerun(source_key, DESTINATION_KEY)

    assert result == CompetencyCriteriaCopyResult(0, 0)


def test_source_run_without_criteria_copies_nothing(course_run: CourseRun, destination_run: CourseRun) -> None:
    """A source run with no criteria tree copies nothing and creates no group."""
    result = _copy(course_run, destination_run)

    assert result == CompetencyCriteriaCopyResult(0, 0)
    assert not CompetencyCriteriaGroup.objects.exists()


def test_missing_destination_run_raises(course_run: CourseRun) -> None:
    """A destination key with no CourseRun is an error."""
    assert course_run.course_key == SOURCE_KEY

    with pytest.raises(CourseRun.DoesNotExist):
        copy_competency_criteria_for_course_rerun(SOURCE_KEY, DESTINATION_KEY)


def test_copies_course_level_group_under_same_root(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """The new course-level group hangs off the source's root and keeps its operator and ordering."""
    source_group = _course_level_group(tag, course_run, logic_operator=LogicOperator.AND, ordering=3)
    object_tag = _tag_on_both_runs("p1", tag, course_run, destination_run)
    _criterion(_child_group(source_group), object_tag, default_rule_profile)
    tag_and_taxonomy_counts = (Tag.objects.count(), Taxonomy.objects.count())

    _copy(course_run, destination_run)

    copied = _destination_course_level_group(tag, destination_run)
    assert copied.parent_id == source_group.parent_id
    assert copied.tag_id == tag.id
    assert copied.logic_operator == LogicOperator.AND
    assert copied.ordering == 3
    assert copied.name == f"{tag.value} — {DESTINATION_TITLE}"
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent=None).count() == 1
    assert CompetencyCriteriaGroup.objects.get(tag=tag, parent=None).logic_operator is None
    assert (Tag.objects.count(), Taxonomy.objects.count()) == tag_and_taxonomy_counts


def test_course_level_group_name_is_truncated(
    course_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A tag value and run title too long together are cut to the group name's column length."""
    destination_run = create_course_run_for_modulestore_course_with(DESTINATION_KEY, title="T" * 255)
    _criterion(_child_group(_course_level_group(tag, course_run)),
               _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)

    _copy(course_run, destination_run)

    name = _destination_course_level_group(tag, destination_run).name
    assert len(name) == 255
    assert name.startswith(f"{tag.value} — T")


def test_copies_leaf_groups_with_operator_name_and_ordering(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Every in-force group below the course-level group is copied, at any depth, with its own fields."""
    source_group = _course_level_group(tag, course_run)
    leaf_b = _child_group(source_group, name="Leaf B", ordering=2, logic_operator=None)
    leaf_a = _child_group(source_group, name="Leaf A", ordering=1, logic_operator=LogicOperator.AND)
    nested = _child_group(leaf_a, name="Nested", ordering=5, logic_operator=LogicOperator.OR)
    _criterion(leaf_b, _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    _criterion(leaf_a, _tag_on_both_runs("p2", tag, course_run, destination_run), default_rule_profile)
    _criterion(nested, _tag_on_both_runs("p3", tag, course_run, destination_run), default_rule_profile)

    _copy(course_run, destination_run)

    copied_leaves = _children(_destination_course_level_group(tag, destination_run))
    assert [(g.name, g.ordering, g.logic_operator) for g in copied_leaves] == [
        ("Leaf A", 1, LogicOperator.AND),
        ("Leaf B", 2, None),
    ]
    copied_nested = _children(copied_leaves[0])
    assert [(g.name, g.ordering, g.logic_operator) for g in copied_nested] == [("Nested", 5, LogicOperator.OR)]
    assert not _children(copied_leaves[1])
    for copied, source in zip([*copied_leaves, *copied_nested], [leaf_a, leaf_b, nested]):
        assert copied.uuid != source.uuid


def test_copied_criterion_points_at_destination_object_tag(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A copied criterion uses the destination run's ObjectTag for the same block and tag."""
    source_object_tag = _tag_on_both_runs("p1", tag, course_run, destination_run)
    source_criterion = _criterion(_child_group(_course_level_group(tag, course_run)), source_object_tag,
                                  default_rule_profile)

    _copy(course_run, destination_run)

    copied = CompetencyCriterion.objects.exclude(pk=source_criterion.pk).select_related("object_tag").get()
    assert copied.object_tag.object_id == str(DESTINATION_KEY.make_usage_key("problem", "p1"))
    assert copied.object_tag.taxonomy_id == tag.taxonomy_id
    assert copied.object_tag.tag_id == tag.id
    assert copied.uuid != source_criterion.uuid


@pytest.mark.parametrize("rule_source", ["default_profile", "override", "archived_profile"])
def test_copies_rule_source_as_is(
    course_run: CourseRun,
    destination_run: CourseRun,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
    rule_source: str,
) -> None:
    """A criterion's profile, or its overrides, are copied unchanged, even with another live profile in scope."""
    # A live taxonomy-scoped profile, which would win if the copy re-resolved the applicable profile.
    CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=OVERRIDE_PAYLOAD, competency_taxonomy_id=tag.taxonomy_id,
    )
    profile: CompetencyRuleProfile | None = None
    overrides: dict[str, Any] = {}
    if rule_source == "default_profile":
        profile = default_rule_profile
    elif rule_source == "override":
        overrides = {"rule_type_override": RuleType.GRADE, "rule_payload_override": OVERRIDE_PAYLOAD}
    else:
        profile = CompetencyRuleProfile.objects.create(
            rule_type=RuleType.GRADE, rule_payload=OVERRIDE_PAYLOAD, competency_taxonomy_id=tag.taxonomy_id,
            archived=True,
        )
    leaf = _child_group(_course_level_group(tag, course_run))
    _criterion(leaf, _tag_on_both_runs("p1", tag, course_run, destination_run), profile, **overrides)

    _copy(course_run, destination_run)

    copied = CompetencyCriterion.objects.get(group__parent__course=destination_run)
    assert copied.rule_profile_id == (profile.id if profile else None)
    assert copied.rule_type_override == overrides.get("rule_type_override")
    assert copied.rule_payload_override == overrides.get("rule_payload_override")


def test_skips_archived_criterion(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """An archived criterion is not copied, while its live sibling is."""
    leaf = _child_group(_course_level_group(tag, course_run))
    _criterion(leaf, _tag_on_both_runs("live", tag, course_run, destination_run), default_rule_profile)
    _criterion(leaf, _tag_on_both_runs("gone", tag, course_run, destination_run), default_rule_profile,
               archived=True)

    _copy(course_run, destination_run)

    copied = CompetencyCriterion.objects.select_related("object_tag").get(group__parent__course=destination_run)
    assert copied.object_tag.object_id == str(DESTINATION_KEY.make_usage_key("problem", "live"))


@pytest.mark.parametrize("archived_level", ["course_level", "leaf"])
def test_skips_archived_group_and_descendants(
    course_run: CourseRun,
    destination_run: CourseRun,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
    archived_level: str,
) -> None:
    """An archived group is not copied, and neither is anything beneath it."""
    source_group = _course_level_group(tag, course_run)
    archived_leaf = _child_group(source_group, name="Archived")
    _criterion(archived_leaf, _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    nested = _child_group(archived_leaf, name="Nested")
    _criterion(nested, _tag_on_both_runs("p2", tag, course_run, destination_run), default_rule_profile)
    live_leaf = _child_group(source_group, name="Live")
    _criterion(live_leaf, _tag_on_both_runs("p3", tag, course_run, destination_run), default_rule_profile)
    archived = source_group if archived_level == "course_level" else archived_leaf
    archived.archived = True
    archived.save()

    _copy(course_run, destination_run)

    if archived_level == "course_level":
        assert not CompetencyCriteriaGroup.objects.filter(course=destination_run).exists()
        assert CompetencyCriterion.objects.count() == 3
    else:
        copied_leaves = _children(_destination_course_level_group(tag, destination_run))
        assert [g.name for g in copied_leaves] == ["Live"]
        assert not _children(copied_leaves[0])
        assert CompetencyCriterion.objects.filter(group__parent__course=destination_run).count() == 1


def test_skips_competency_with_archived_root(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A competency whose root is archived gets no groups on the destination run."""
    source_group = _course_level_group(tag, course_run)
    _criterion(_child_group(source_group), _tag_on_both_runs("p1", tag, course_run, destination_run),
               default_rule_profile)
    root = source_group.parent
    assert root is not None
    root.archived = True
    root.save()

    result = _copy(course_run, destination_run)

    assert result == CompetencyCriteriaCopyResult(0, 0)
    assert not CompetencyCriteriaGroup.objects.filter(course=destination_run).exists()


def test_other_runs_sharing_the_root_are_not_copied(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Only the source run's branch of a shared root is copied; another run's branch stays as it was."""
    other_run = CourseRun.objects.create(catalog_course=course_run.catalog_course, run_code="Summer2026")
    source_leaf = _child_group(_course_level_group(tag, course_run), name="Source leaf")
    _criterion(source_leaf, _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    other_leaf = _child_group(_course_level_group(tag, other_run), name="Other leaf")
    _criterion(other_leaf, _tag_on_both_runs("p2", tag, other_run, destination_run), default_rule_profile)
    other_groups = list(CompetencyCriteriaGroup.objects.filter(pk__in=[other_leaf.id, other_leaf.parent_id])
                        .order_by("id").values())
    other_criteria = list(CompetencyCriterion.objects.filter(group=other_leaf).order_by("id").values())

    result = _copy(course_run, destination_run)

    assert result == CompetencyCriteriaCopyResult(2, 1)
    copied_leaves = _children(_destination_course_level_group(tag, destination_run))
    assert [g.name for g in copied_leaves] == ["Source leaf"]
    copied = CompetencyCriterion.objects.select_related("object_tag").get(group=copied_leaves[0])
    assert copied.object_tag.object_id == str(DESTINATION_KEY.make_usage_key("problem", "p1"))
    assert list(CompetencyCriteriaGroup.objects.filter(pk__in=[other_leaf.id, other_leaf.parent_id])
                .order_by("id").values()) == other_groups
    assert list(CompetencyCriterion.objects.filter(group=other_leaf).order_by("id").values()) == other_criteria


def test_group_with_one_in_force_criterion_is_still_copied(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A group with no criteria of its own is copied when one in-force criterion sits further down."""
    source_group = _course_level_group(tag, course_run)
    outer = _child_group(source_group, name="Outer")
    inner = _child_group(outer, name="Inner")
    _criterion(inner, _tag_on_both_runs("live", tag, course_run, destination_run), default_rule_profile)
    _criterion(inner, _tag_on_both_runs("gone", tag, course_run, destination_run), default_rule_profile,
               archived=True)

    _copy(course_run, destination_run)

    copied_outer = _children(_destination_course_level_group(tag, destination_run))
    assert [g.name for g in copied_outer] == ["Outer"]
    copied_inner = _children(copied_outer[0])
    assert [g.name for g in copied_inner] == ["Inner"]
    assert copied_inner[0].criteria.count() == 1


def test_group_with_no_in_force_criteria_is_not_created(
    course_run: CourseRun,
    destination_run: CourseRun,
    tag: Tag,
    second_tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Groups with only archived criteria, or none, are not created, nor is a course-level group left empty."""
    source_group = _course_level_group(tag, course_run)
    archived_only = _child_group(source_group, name="Archived only")
    _criterion(archived_only, _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile,
               archived=True)
    _child_group(_child_group(source_group, name="Empty"), name="Empty nested")
    live = _child_group(source_group, name="Live")
    _criterion(live, _tag_on_both_runs("p2", tag, course_run, destination_run), default_rule_profile)
    second_leaf = _child_group(_course_level_group(second_tag, course_run))
    _criterion(second_leaf, _tag_on_both_runs("p3", second_tag, course_run, destination_run), default_rule_profile,
               archived=True)

    _copy(course_run, destination_run)

    assert [g.name for g in _children(_destination_course_level_group(tag, destination_run))] == ["Live"]
    assert not CompetencyCriteriaGroup.objects.filter(tag=second_tag, course=destination_run).exists()


def test_skips_criteria_of_skipped_taxonomy(
    course_run: CourseRun,
    destination_run: CourseRun,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A competency in a skipped taxonomy gets nothing on the destination run, even without destination tags."""
    skipped_taxonomy = CompetencyTaxonomy.objects.create(name="Skipped", export_id="skipped-v1")
    skipped_tag = Tag.objects.create(taxonomy=skipped_taxonomy, value="Skipped Competency")
    _criterion(_child_group(_course_level_group(tag, course_run)),
               _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    _criterion(_child_group(_course_level_group(skipped_tag, course_run)),
               _object_tag(course_run, "p2", skipped_tag), default_rule_profile)

    result = _copy(course_run, destination_run, skipped_taxonomy_ids=[skipped_taxonomy.id])

    assert result == CompetencyCriteriaCopyResult(2, 1)
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, course=destination_run).exists()
    assert not CompetencyCriteriaGroup.objects.filter(tag=skipped_tag, course=destination_run).exists()
    assert not CompetencyCriterion.objects.filter(object_tag__taxonomy=skipped_taxonomy,
                                                  group__parent__course=destination_run).exists()


def test_missing_destination_object_tag_raises_and_rolls_back(
    course_run: CourseRun,
    destination_run: CourseRun,
    tag: Tag,
    second_tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A source criterion with no matching destination ObjectTag fails the whole copy."""
    _criterion(_child_group(_course_level_group(tag, course_run)),
               _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    _criterion(_child_group(_course_level_group(second_tag, course_run)),
               _object_tag(course_run, "p2", second_tag), default_rule_profile)
    before = _snapshot()

    with pytest.raises(ObjectTag.DoesNotExist):
        _copy(course_run, destination_run)

    assert _snapshot() == before
    assert not CompetencyCriteriaGroup.objects.filter(course=destination_run).exists()


def test_failure_after_writes_rolls_back_everything(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A failure after groups and a criterion are written leaves no rows and no history behind."""
    leaf = _child_group(_course_level_group(tag, course_run))
    _criterion(leaf, _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    second = _criterion(leaf, _tag_on_both_runs("p2", tag, course_run, destination_run), default_rule_profile)
    # QuerySet.update() skips model validation, so the copy's save() is the first to reject this payload.
    CompetencyCriterion.objects.filter(pk=second.pk).update(
        rule_profile=None, rule_type_override=RuleType.GRADE, rule_payload_override={"bogus": 1},
    )
    before = _snapshot()
    history_before = (_history_count("CompetencyCriteriaGroup"), _history_count("CompetencyCriterion"))

    with pytest.raises(ValidationError):
        _copy(course_run, destination_run)

    assert _snapshot() == before
    assert (_history_count("CompetencyCriteriaGroup"), _history_count("CompetencyCriterion")) == history_before


def test_skips_competency_already_on_destination(
    course_run: CourseRun,
    destination_run: CourseRun,
    tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second copy finds the competency already on the destination run, creates nothing, and logs a warning."""
    _criterion(_child_group(_course_level_group(tag, course_run)),
               _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    first = _copy(course_run, destination_run)
    counts = (CompetencyCriteriaGroup.objects.count(), CompetencyCriterion.objects.count())

    with caplog.at_level(logging.WARNING, logger="openedx_learning.applets.cbe.api"):
        second = _copy(course_run, destination_run)

    assert first == CompetencyCriteriaCopyResult(2, 1)
    assert second == CompetencyCriteriaCopyResult(0, 0)
    assert (CompetencyCriteriaGroup.objects.count(), CompetencyCriterion.objects.count()) == counts
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert tag.value in warnings[0]
    assert str(SOURCE_KEY) in warnings[0]
    assert str(DESTINATION_KEY) in warnings[0]


def test_copy_records_history_and_counts_rows(
    course_run: CourseRun,
    destination_run: CourseRun,
    tag: Tag,
    second_tag: Tag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """Each created group and criterion, across competencies, gets one history row and is counted in the result."""
    leaf = _child_group(_course_level_group(tag, course_run))
    _criterion(leaf, _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    _criterion(_child_group(leaf), _tag_on_both_runs("p2", tag, course_run, destination_run), default_rule_profile)
    _criterion(_child_group(_course_level_group(second_tag, course_run)),
               _tag_on_both_runs("p3", second_tag, course_run, destination_run), default_rule_profile)

    result = _copy(course_run, destination_run)

    copied_course_group = _destination_course_level_group(tag, destination_run)
    copied_groups = [copied_course_group, *_children(copied_course_group)]
    copied_groups += _children(copied_groups[1])
    second_course_group = _destination_course_level_group(second_tag, destination_run)
    copied_groups += [second_course_group, *_children(second_course_group)]
    copied_criteria = list(CompetencyCriterion.objects.filter(group__in=copied_groups))
    assert len(copied_groups) == 5
    assert len(copied_criteria) == 3
    assert result == CompetencyCriteriaCopyResult(len(copied_groups), len(copied_criteria))
    group_ids = sorted(group.id for group in copied_groups)
    criterion_ids = sorted(criterion.id for criterion in copied_criteria)
    assert _history_ids("CompetencyCriteriaGroup", group_ids) == group_ids
    assert _history_ids("CompetencyCriterion", criterion_ids) == criterion_ids


def test_source_rows_unchanged(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """The copy leaves every source group and criterion, and their history, as it was."""
    leaf = _child_group(_course_level_group(tag, course_run), name="Leaf", logic_operator=LogicOperator.AND)
    _criterion(leaf, _tag_on_both_runs("p1", tag, course_run, destination_run), default_rule_profile)
    _criterion(leaf, _tag_on_both_runs("p2", tag, course_run, destination_run), None,
               rule_type_override=RuleType.GRADE, rule_payload_override=OVERRIDE_PAYLOAD)
    source_groups, source_criteria = _snapshot()
    source_group_ids = {row["id"] for row in source_groups}
    source_criterion_ids = {row["id"] for row in source_criteria}
    history_before = (
        _history_ids("CompetencyCriteriaGroup", source_group_ids),
        _history_ids("CompetencyCriterion", source_criterion_ids),
    )

    _copy(course_run, destination_run)

    groups_after, criteria_after = _snapshot()
    assert [row for row in groups_after if row["id"] in source_group_ids] == source_groups
    assert [row for row in criteria_after if row["id"] in source_criterion_ids] == source_criteria
    history_after = (
        _history_ids("CompetencyCriteriaGroup", source_group_ids),
        _history_ids("CompetencyCriterion", source_criterion_ids),
    )
    assert history_after == history_before


def _copy_select_count(source: CourseRun, destination: CourseRun) -> int:
    """Return how many SELECT queries copying `source` onto `destination` makes."""
    with CaptureQueriesContext(connection) as context:
        _copy(source, destination)
    return sum(1 for query in context.captured_queries if query["sql"].lstrip().upper().startswith("SELECT"))


def test_select_count_does_not_grow_with_depth(
    course_run: CourseRun, destination_run: CourseRun, tag: Tag, default_rule_profile: CompetencyRuleProfile,
) -> None:
    """A tree four groups deep takes as many SELECTs to copy as a flat tree of the same size."""
    flat_group = _course_level_group(tag, course_run)
    for index in range(4):
        _criterion(_child_group(flat_group), _tag_on_both_runs(f"flat{index}", tag, course_run, destination_run),
                   default_rule_profile)
    deep_source = create_course_run_for_modulestore_course_with(
        CourseKey.from_string("course-v1:Org1+Python100+Summer2026"), title="Summer",
    )
    deep_destination = create_course_run_for_modulestore_course_with(
        CourseKey.from_string("course-v1:Org1+Python100+Winter2027"), title="Winter",
    )
    deep_group = _course_level_group(tag, deep_source)
    for index in range(4):
        deep_group = _child_group(deep_group)
        _criterion(deep_group, _tag_on_both_runs(f"deep{index}", tag, deep_source, deep_destination),
                   default_rule_profile)

    flat_selects = _copy_select_count(course_run, destination_run)
    deep_selects = _copy_select_count(deep_source, deep_destination)

    assert flat_selects == deep_selects
    deepest_on_destination = {"group__parent__parent__parent__parent__course": deep_destination}
    assert CompetencyCriterion.objects.filter(**deepest_on_destination).count() == 1
