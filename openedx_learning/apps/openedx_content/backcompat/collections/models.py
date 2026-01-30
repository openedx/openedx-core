"""
These are stub models necessary to ensure a smooth migration for
openedx-platform apps that were built to have foreign keys to these models.
"""
from django.db import models


class Collection(models.Model):
    id = models.AutoField(primary_key=True)
