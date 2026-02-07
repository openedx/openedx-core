"""
Utilities for the API
"""
from typing import Optional, Type

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication  # type: ignore[import]
from edx_rest_framework_extensions.auth.session.authentication import (  # type: ignore[import]
    SessionAuthenticationAllowInactiveUser,
)
from rest_framework.request import Request


def view_auth_classes(func_or_class):
    """
    Function and class decorator that abstracts the authentication classes for api views.
    """
    def _decorator(func_or_class):
        """
        Requires either OAuth2 or Session-based authentication;
        are the same authentication classes used on edx-platform
        """
        func_or_class.authentication_classes = (
            JwtAuthentication,
            SessionAuthenticationAllowInactiveUser,
        )
        return func_or_class
    return _decorator(func_or_class)


class UserPermissionsHelper:
    """
    Provides helper methods for serializing user permissions.
    """
    @property
    def _request(self) -> Request:
        """
        Returns the current request.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def _model(self) -> Type:
        """
        Returns the model used when checking permissions.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def app_label(self) -> Type:
        """
        Returns the app_label for the model used when checking permissions.
        """
        return self._model._meta.app_label

    @property
    def model_name(self) -> Type:
        """
        Returns the name of the model used when checking permissions.
        """
        return self._model._meta.model_name

    def _get_permission_name(self, action: str) -> str:
        """
        Returns the fully-qualified permission name corresponding to the current model and `action`.
        """
        assert action in ("add", "view", "change", "delete")
        return f'{self.app_label}.{action}_{self.model_name}'

    def _can(self, perm_name: str, instance=None) -> Optional[bool]:
        """
        Does the current `request.user` have the given `perm` on the `instance` object?

        Returns None if no permissions were requested.
        Returns True if they may.
        Returns False if they may not.
        """
        request = self._request
        assert request and request.user
        return request.user.has_perm(perm_name, instance)

    def get_can_add(self, _instance=None) -> Optional[bool]:
        """
        Returns True if the current user is allowed to add new instances.

        Note: we omit the actual instance from the permissions check; most tagging models prefer this.
        """
        perm_name = self._get_permission_name('add')
        return self._can(perm_name)

    def get_can_view(self, instance) -> Optional[bool]:
        """
        Returns True if the current user is allowed to view/see this instance.
        """
        perm_name = self._get_permission_name('view')
        return self._can(perm_name, instance)

    def get_can_change(self, instance) -> Optional[bool]:
        """
        Returns True if the current user is allowed to edit/change this instance.
        """
        perm_name = self._get_permission_name('change')
        return self._can(perm_name, instance)

    def get_can_delete(self, instance) -> Optional[bool]:
        """
        Returns True if the current user is allowed to delete this instance.
        """
        perm_name = self._get_permission_name('change')
        return self._can(perm_name, instance)
