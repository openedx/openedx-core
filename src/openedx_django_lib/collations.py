"""
This module has collation-related code to allow us to attach collation settings
to specific fields on a per-database-vendor basis. This used by the ``fields``
module in order to specify field types have have normalized behavior between
SQLite and MySQL (see fields.py for more details).
"""
from django.db import models


class MultiCollationMixin:
    """
    Mixin to enable multiple, database-vendor-specific collations.

    This should be mixed into new subclasses of CharField and TextField, since
    they're the only Field types that store text data.
    """

    def __init__(self, *args, db_collations=None, db_collation=None, **kwargs):  # pylint: disable=unused-argument
        """
        Init like any field but add db_collations and disallow db_collation

        The ``db_collations`` param should be a dict of vendor names to
        collations, like::

          {
            'msyql': 'utf8mb4_bin',
            'sqlite': 'BINARY'
          }

        It is an error to pass in a CharField-style ``db_collation``. I
        originally wanted to use this attribute name, but I needed to preserve
        it for Django 3.2 compatibility (see the ``db_collation`` method
        docstring for details).
        """

        super().__init__(*args, **kwargs)
        self.db_collations = db_collations or {}

    def db_parameters(self, connection):
        """
        Return database parameters for this field. This adds collation info.

        We examine this field's ``db_collations`` attribute and return the
        collation that maps to ``connection.vendor``. This will typically be
        'mysql' or 'sqlite'.
        """
        db_params = models.Field.db_parameters(self, connection)

        # Now determine collation based on DB vendor (e.g. 'sqlite', 'mysql')
        if connection.vendor in self.db_collations:
            db_params["collation"] = self.db_collations[connection.vendor]

        return db_params

    def deconstruct(self):
        """
        How to serialize our Field for the migration file.

        For our mixin fields, this is just doing what the field's superclass
        would do and then tacking on our custom ``db_collations`` dict data.
        """
        name, path, args, kwargs = super().deconstruct()
        if self.db_collations:
            kwargs["db_collations"] = self.db_collations
        return name, path, args, kwargs
