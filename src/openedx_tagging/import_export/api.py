"""
Import/export API functions

A modular implementation has been followed for both functionalities.

Import
------------

In this functionality we have the following pipeline with the following classes:

Parser.parse_import() -> TagImportPlan.generate_actions() -> [ImportActions]
-> TagImportPlan.plan() -> TagImportPlan.execute()

Parsers are in charge of reading the input file,
making the respective verifications of its format and returning a list of TagItems.
You need to create parser for each format that the system will accept.
For more information see parsers.py

TagImportPlan receives a list of TagItems. With this, it generates each
action that will be executed in the import.
Each Action are in charge of verifying and executing specific
and simple operations in the database, such as creating or rename tag.
For more information see actions.py

In each action it is verified if there are no errors or inconsistencies
with taxonomy tags or with previous actions.
In the end, TagImportPlan contains all actions and possible errors.
You can run `plan()` to see the actions and errors or you can run `execute()`
to execute each action.

Export
----------

The export only uses Parsers. Calls the respective function and
returns a string with the data.


TODO for next versions
---------
- Function to force clean the status of an import task, or a way to avoid
  a lock: a task not in SUCCESS or ERROR due to something unexpected
  (ex. server crash)
- Join/reduce actions on TagImportPlan. See `generate_actions()`
"""
from __future__ import annotations

import time
from typing import BinaryIO

from django.utils.translation import gettext as _

from ..models import TagImportTask, TagImportTaskState, Taxonomy
from .import_plan import TagImportPlan, TagImportTask
from .parsers import ParserFormat, get_parser


def import_tags(
    taxonomy: Taxonomy,
    file: BinaryIO,
    parser_format: ParserFormat,
    replace=False,
    plan_only=False,
) -> tuple[bool, TagImportTask, TagImportPlan | None]:
    """
    Execute the necessary actions to import the tags from `file`

    You can read the docstring of the top for more info about the
    modular architecture.

    It creates an TagImportTask to keep logs of the execution
    of each import step and the current status.
    There can only be one task in progress at a time per taxonomy

    Set `replace` to True to delete all not readed Tag of the given taxonomy.
    Ex. Given a taxonomy with `tag_1`, `tag_2` and `tag_3`. If there is only `tag_1`
    in the file (regardless of action), then `tag_2` and `tag_3` will be deleted
    if `replace=True`

    Set `plan_only` to True to only generate the actions and not execute them.
    """
    global_start_time = time.time()
    _import_validations(taxonomy)

    # Checks that exists only one task import in progress at a time per taxonomy
    if not _check_unique_import_task(taxonomy):
        raise ValueError(
            _(
                "There is an import task running. "
                "Only one task per taxonomy can be created at a time."
            )
        )

    # Creating import task
    task = TagImportTask.create(taxonomy)

    try:
        # Start of parsing

        # Get the parser and parse the file
        start_time = time.time()
        task.log_parser_start()
        parser = get_parser(parser_format)
        tags, errors = parser.parse_import(file)

        # Check if there are errors in the parse
        if errors:
            task.handle_parser_errors(errors)
            return False, task, None

        end_time = time.time()
        elapsed_time = end_time - start_time
        elapsed_time = round(elapsed_time, 5)
        task.log_parser_end(elapsed_time)

        # End of parsing

        # Start of generate actions

        start_time = time.time()

        task.log_start_planning()
        tag_import_plan = TagImportPlan(taxonomy)
        tag_import_plan.generate_actions(tags, replace)

        end_time = time.time()
        elapsed_time = end_time - start_time
        elapsed_time = round(elapsed_time, 5)
        task.log_plan(tag_import_plan, elapsed_time)

        # End of generate actions

        if tag_import_plan.errors:
            task.handle_plan_errors()
            return False, task, tag_import_plan

        if not plan_only:
            # Start of execute
            start_time = time.time()
            task.log_start_execute()
            tag_import_plan.execute(task)
            end_time = time.time()
            elapsed_time = end_time - start_time
            elapsed_time = round(elapsed_time, 5)
            task.log_end_execute(elapsed_time)
            # End of execute

        global_end_time = time.time()
        global_elapsed_time = global_end_time - global_start_time
        global_elapsed_time = round(global_elapsed_time, 5)

        task.end_success(global_elapsed_time)

        return True, task, tag_import_plan
    except Exception as exception:  # pylint: disable=broad-exception-caught
        # Log any exception
        task.log_exception(exception)
        return False, task, None


def get_last_import_status(taxonomy: Taxonomy) -> TagImportTaskState:
    """
    Get status of the last import task of the given taxonomy
    """
    task = _get_last_import_task(taxonomy)
    if task is None:
        raise ValueError("No import task was created yet.")
    return TagImportTaskState(task.status)


def get_last_import_log(taxonomy: Taxonomy) -> str:
    """
    Get logs of the last import task of the given taxonomy
    """
    task = _get_last_import_task(taxonomy)
    if task is None:
        raise ValueError("No import task was created yet.")
    return task.log


def export_tags(taxonomy: Taxonomy, output_format: ParserFormat) -> str:
    """
    Returns a string with all tag data of the given taxonomy
    """
    parser = get_parser(output_format)
    return parser.export(taxonomy)


def _check_unique_import_task(taxonomy: Taxonomy) -> bool:
    """
    Verifies if there is another in progress import task for the
    given taxonomy
    """
    last_task = _get_last_import_task(taxonomy)
    if not last_task:
        return True
    return (
        last_task.status in {
            TagImportTaskState.SUCCESS.value,
            TagImportTaskState.ERROR.value
        }
    )


def _get_last_import_task(taxonomy: Taxonomy) -> TagImportTask | None:
    """
    Get the last import task for the given taxonomy
    """
    return (
        TagImportTask.objects.filter(taxonomy=taxonomy)
        .order_by("-creation_date")
        .first()
    )


def _import_validations(taxonomy: Taxonomy):
    """
    Validates if the taxonomy is allowed to import tags
    """
    taxonomy = taxonomy.cast()
    if taxonomy.allow_free_text:
        raise ValueError(
            _(
                "Invalid taxonomy ({id}): You cannot import a free-form taxonomy."
            ).format(id=taxonomy.id)
        )

    if taxonomy.system_defined:
        raise ValueError(
            _(
                "Invalid taxonomy ({id}): You cannot import a system-defined taxonomy."
            ).format(id=taxonomy.id)
        )
