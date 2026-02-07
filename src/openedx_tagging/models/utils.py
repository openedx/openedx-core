"""
Utilities for tagging and taxonomy models
"""
from django.db import connection as db_connection
from django.db.models import Aggregate, CharField, TextField
from django.db.models.expressions import Combinable, Func

RESERVED_TAG_CHARS = [
    '\t',  # Used in the database to separate tag levels in the "lineage" field
           # e.g. lineage="Earth\tNorth America\tMexico\tMexico City"
    ' > ',  # Used in the search index and Instantsearch frontend to separate tag levels
            # e.g. tags_level3="Earth > North America > Mexico > Mexico City"
    ';',   # Used in CSV exports to separate multiple tags from the same taxonomy
           # e.g. languages-v1: en;es;fr
]
TAGS_CSV_SEPARATOR = RESERVED_TAG_CHARS[2]


class ConcatNull(Func):  # pylint: disable=abstract-method
    """
    Concatenate two arguments together. Like normal SQL but unlike Django's
    "Concat", if either argument is NULL, the result will be NULL.
    """

    function = "CONCAT"

    def as_sqlite(self, compiler, connection, **extra_context):
        """ SQLite doesn't have CONCAT() but has a concatenation operator """
        return super().as_sql(
            compiler,
            connection,
            template="%(expressions)s",
            arg_joiner=" || ",
            **extra_context,
        )


class StringAgg(Aggregate, Combinable):
    """
    Aggregate function that collects the values of some column across all rows,
    and creates a string by concatenating those values, with a specified separator.

    This version supports PostgreSQL (STRING_AGG), MySQL (GROUP_CONCAT), and SQLite.
    """
    # Default function is for MySQL (GROUP_CONCAT)
    function = 'GROUP_CONCAT'
    template = '%(function)s(%(distinct)s%(expressions)s)'

    def __init__(self, expression, distinct=False, delimiter=',', **extra):
        self.delimiter = delimiter
        # Handle the distinct option and output type
        distinct_str = 'DISTINCT ' if distinct else ''

        extra.update({
            'distinct': distinct_str,
            'output_field': CharField(),
        })

        # Check the database backend (PostgreSQL, MySQL, or SQLite)
        if 'postgresql' in db_connection.vendor.lower():
            self.function = 'STRING_AGG'
            self.template = "%(function)s(%(distinct)s%(expressions)s::TEXT, '%(delimiter)s')"
            extra.update({
                "delimiter": self.delimiter,
                "output_field": TextField(),
            })

        # Initialize the parent class with the necessary parameters
        super().__init__(
            expression,
            **extra,
        )

    # Implementing abstract methods from Combinable
    def __rand__(self, other):
        return self._combine(other, 'AND', False)

    def __ror__(self, other):
        return self._combine(other, 'OR', False)

    def __rxor__(self, other):
        return self._combine(other, 'XOR', False)
