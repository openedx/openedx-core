from django.db import models

from openedx_learning.lib.fields import (
    MultiCollationTextField,
    case_insensitive_char_field,
    immutable_uuid_field,
    key_field,
    manual_date_time_field,
)

class LearningPackage(models.Model):
    id = models.AutoField(primary_key=True)

class PublishableEntity(models.Model):
    pass

class DraftChangeLog(models.Model):
    pass

class Container(models.Model):
    pass
