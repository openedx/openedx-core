"""
These are stub models necessary to ensure a smooth migration for
openedx-platform apps that were built to have foreign keys to these models.
"""
from django.db import models


class Component(models.Model):
    publishable_entity = models.OneToOneField(
        'oel_publishing.PublishableEntity', on_delete=models.CASCADE, primary_key=True
    )
