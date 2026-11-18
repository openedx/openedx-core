"""
Backfill CompetencyCriteriaGroup.subtree_course for existing groups.
"""
from django.db import migrations

# Keeps each pk__in list under SQLite's historical 999-variable limit.
BACKFILL_BATCH_SIZE = 500


def backfill_subtree_courses(apps, schema_editor):
    """
    Set subtree_course on every group: its own course if it has one, else its parent's subtree course.

    Groups above any course stay null. The historical model has no custom save(), so the derivation is
    applied here.
    """
    CompetencyCriteriaGroup = apps.get_model("openedx_learning", "CompetencyCriteriaGroup")
    children_by_parent = {}
    own_course = {}
    roots = []
    for group_id, parent_id, course_id in CompetencyCriteriaGroup.objects.values_list("id", "parent_id", "course_id"):
        own_course[group_id] = course_id
        if parent_id is None:
            roots.append(group_id)
        else:
            children_by_parent.setdefault(parent_id, []).append(group_id)

    group_ids_by_course = {}
    pending = [(group_id, None) for group_id in roots]
    while pending:
        group_id, inherited_course_id = pending.pop()
        subtree_course_id = own_course[group_id] if own_course[group_id] is not None else inherited_course_id
        if subtree_course_id is not None:
            group_ids_by_course.setdefault(subtree_course_id, []).append(group_id)
        pending.extend((child_id, subtree_course_id) for child_id in children_by_parent.get(group_id, ()))

    for course_id, group_ids in group_ids_by_course.items():
        for start in range(0, len(group_ids), BACKFILL_BATCH_SIZE):
            CompetencyCriteriaGroup.objects.filter(pk__in=group_ids[start:start + BACKFILL_BATCH_SIZE]).update(
                subtree_course_id=course_id
            )


class Migration(migrations.Migration):

    dependencies = [
        ("openedx_learning", "0018_criteria_group_subtree_course"),
    ]

    operations = [
        migrations.RunPython(backfill_subtree_courses, migrations.RunPython.noop),
    ]
