"""
These are stub models necessary to ensure a smooth migration for
openedx-platform apps that were built to have foreign keys to these models.
"""
from django.db import models


class LearningPackage(models.Model):
    id = models.AutoField(primary_key=True)


class PublishableEntity(models.Model):
    pass


class DraftChangeLog(models.Model):
    pass


class DraftChangeLogRecord(models.Model):
    pass


class Container(models.Model):
    publishable_entity = models.OneToOneField(
        PublishableEntity, on_delete=models.CASCADE, primary_key=True
    )
