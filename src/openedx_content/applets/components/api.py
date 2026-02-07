"""
Components API (warning: UNSTABLE, in progress API)

These functions are often going to be simple-looking write operations, but there
is bookkeeping logic needed across multiple models to keep state consistent. You
can read from the models directly for various queries if necessary–we do this in
the Django Admin for instance. But you should NEVER mutate this app's models
directly, since there might be other related models that you may not know about.

Please look at the models.py file for more information about the kinds of data
are stored in this app.
"""
from __future__ import annotations

import mimetypes
from datetime import datetime
from enum import StrEnum, auto
from logging import getLogger
from pathlib import Path
from uuid import UUID

from django.db.models import Q, QuerySet
from django.db.transaction import atomic
from django.http.response import HttpResponse, HttpResponseNotFound

from ..contents import api as contents_api
from ..publishing import api as publishing_api
from .models import Component, ComponentType, ComponentVersion, ComponentVersionContent

# The public API that will be re-exported by openedx_content.api
# is listed in the __all__ entries below. Internal helper functions that are
# private to this module should start with an underscore. If a function does not
# start with an underscore AND it is not in __all__, that function is considered
# to be callable only by other apps in the authoring package.
__all__ = [
    "get_or_create_component_type",
    "get_or_create_component_type_by_entity_key",
    "create_component",
    "create_component_version",
    "create_next_component_version",
    "create_component_and_version",
    "get_component",
    "get_component_by_key",
    "get_component_by_uuid",
    "get_component_version_by_uuid",
    "component_exists_by_key",
    "get_collection_components",
    "get_components",
    "create_component_version_content",
    "look_up_component_version_content",
    "AssetError",
    "get_redirect_response_for_component_asset",
]


logger = getLogger()


def get_or_create_component_type(namespace: str, name: str) -> ComponentType:
    """
    Get the ID of a ComponentType, and create if missing.

    Caching Warning: Be careful about putting any caching decorator around this
    function (e.g. ``lru_cache``). It's possible that incorrect cache values
    could leak out in the event of a rollback–e.g. new types are introduced in
    a large import transaction which later fails. You can safely cache the
    results that come back from this function with a local dict in your import
    process instead.#
    """
    component_type, _created = ComponentType.objects.get_or_create(
        namespace=namespace,
        name=name,
    )
    return component_type


def get_or_create_component_type_by_entity_key(entity_key: str) -> tuple[ComponentType, str]:
    """
    Get or create a ComponentType based on a full entity key string.

    The entity key is expected to be in the format
    ``"{namespace}:{type_name}:{local_key}"``. This function will parse out the
    ``namespace`` and ``type_name`` parts and use those to get or create the
    ComponentType.

    Raises ValueError if the entity_key is not in the expected format.
    """
    try:
        namespace, type_name, local_key = entity_key.split(':', 2)
    except ValueError as exc:
        raise ValueError(
            f"Invalid entity_key format: {entity_key!r}. "
            "Expected format: '{namespace}:{type_name}:{local_key}'"
        ) from exc
    return get_or_create_component_type(namespace, type_name), local_key


def create_component(
    learning_package_id: int,
    /,
    component_type: ComponentType,
    local_key: str,
    created: datetime,
    created_by: int | None,
    *,
    can_stand_alone: bool = True,
) -> Component:
    """
    Create a new Component (an entity like a Problem or Video)
    """
    key = f"{component_type.namespace}:{component_type.name}:{local_key}"
    with atomic():
        publishable_entity = publishing_api.create_publishable_entity(
            learning_package_id,
            key,
            created,
            created_by,
            can_stand_alone=can_stand_alone
        )
        component = Component.objects.create(
            publishable_entity=publishable_entity,
            learning_package_id=learning_package_id,
            component_type=component_type,
            local_key=local_key,
        )
    return component


def create_component_version(
    component_pk: int,
    /,
    version_num: int,
    title: str,
    created: datetime,
    created_by: int | None,
) -> ComponentVersion:
    """
    Create a new ComponentVersion
    """
    with atomic():
        publishable_entity_version = publishing_api.create_publishable_entity_version(
            component_pk,
            version_num=version_num,
            title=title,
            created=created,
            created_by=created_by,
        )
        component_version = ComponentVersion.objects.create(
            publishable_entity_version=publishable_entity_version,
            component_id=component_pk,
        )
    return component_version


def create_next_component_version(
    component_pk: int,
    /,
    content_to_replace: dict[str, int | None | bytes],
    created: datetime,
    title: str | None = None,
    created_by: int | None = None,
    *,
    force_version_num: int | None = None,
    ignore_previous_content: bool = False,
) -> ComponentVersion:
    """
    Create a new ComponentVersion based on the most recent version.

    Args:
        component_pk (int): The primary key of the Component to version.
        content_to_replace (dict): Mapping of file keys to Content IDs,
            None (for deletion), or bytes (for new file content).
        created (datetime): The creation timestamp for the new version.
        title (str, optional): Title for the new version. If None, uses the previous version's title.
        created_by (int, optional): User ID of the creator.
        force_version_num (int, optional): If provided, overrides the automatic version number increment and sets
            this version's number explicitly. Use this if you need to restore or import a version with a specific
            version number, such as during data migration or when synchronizing with external systems.
        ignore_previous_content (bool): If True, do not copy over content from the previous version.

    Returns:
        ComponentVersion: The newly created ComponentVersion instance.

    A very common pattern for making a new ComponentVersion is going to be "make
    it just like the last version, except changing these one or two things".
    Before calling this, you should create any new contents via the contents
    API or send the content bytes as part of ``content_to_replace`` values.

    The ``content_to_replace`` dict is a mapping of strings representing the
    local path/key for a file, to ``Content.id`` or content bytes values. Using
    `None` for a value in this dict means to delete that key in the next version.

    Make sure to wrap the function call on a atomic statement:
    ``with transaction.atomic():``

    It is okay to mark entries for deletion that don't exist. For instance, if a
    version has ``a.txt`` and ``b.txt``, sending a ``content_to_replace`` value
    of ``{"a.txt": None, "c.txt": None}`` will remove ``a.txt`` from the next
    version, leave ``b.txt`` alone, and will not error–even though there is no
    ``c.txt`` in the previous version. This is to make it a little more
    convenient to remove paths (e.g. due to deprecation) without having to
    always check for its existence first.

    Why use force_version_num?
        Normally, the version number is incremented automatically from the latest version. If you need to set a specific
        version number (for example, when restoring from backup, importing legacy data, or synchronizing with another
        system), use force_version_num to override the default behavior.

    Why not use create_component_version?
        The main reason is that we want to reuse the logic to create a static file component from a dictionary.

    TODO: Have to add learning_downloadable info to this when it comes time to
          support static asset download.
    """
    # This needs to grab the highest version_num for this Publishable Entity.
    # This will often be the Draft version, but not always. For instance, if
    # an entity was soft-deleted, the draft would be None, but the version_num
    # should pick up from the last edited version. Likewise, a Draft might get
    # reverted to an earlier version, but we want the latest version_num when
    # creating the next version.
    component = Component.objects.get(pk=component_pk)
    last_version = component.versioning.latest
    if last_version is None:
        next_version_num = 1
        title = title or ""
    else:
        next_version_num = last_version.version_num + 1
        if title is None:
            title = last_version.title

    if force_version_num is not None:
        next_version_num = force_version_num

    with atomic():
        publishable_entity_version = publishing_api.create_publishable_entity_version(
            component_pk,
            version_num=next_version_num,
            title=title,
            created=created,
            created_by=created_by,
        )
        component_version = ComponentVersion.objects.create(
            publishable_entity_version=publishable_entity_version,
            component_id=component_pk,
        )
        # First copy the new stuff over...
        for key, content_pk_or_bytes in content_to_replace.items():
            # If the content_pk is None, it means we want to remove the
            # content represented by our key from the next version. Otherwise,
            # we add our key->content_pk mapping to the next version.
            if content_pk_or_bytes is not None:
                if isinstance(content_pk_or_bytes, bytes):
                    file_path, file_content = key, content_pk_or_bytes
                    media_type_str, _encoding = mimetypes.guess_type(file_path)
                    # We use "application/octet-stream" as a generic fallback media type, per
                    # RFC 2046: https://datatracker.ietf.org/doc/html/rfc2046
                    media_type_str = media_type_str or "application/octet-stream"
                    media_type = contents_api.get_or_create_media_type(media_type_str)
                    content = contents_api.get_or_create_file_content(
                        component.learning_package.id,
                        media_type.id,
                        data=file_content,
                        created=created,
                    )
                    content_pk = content.pk
                else:
                    content_pk = content_pk_or_bytes
                ComponentVersionContent.objects.create(
                    content_id=content_pk,
                    component_version=component_version,
                    key=key,
                )

        if ignore_previous_content:
            return component_version

        # Now copy any old associations that existed, as long as they aren't
        # in conflict with the new stuff or marked for deletion.
        last_version_content_mapping = ComponentVersionContent.objects \
                                                              .filter(component_version=last_version)
        for cvrc in last_version_content_mapping:
            if cvrc.key not in content_to_replace:
                ComponentVersionContent.objects.create(
                    content_id=cvrc.content_id,
                    component_version=component_version,
                    key=cvrc.key,
                )

        return component_version


def create_component_and_version(  # pylint: disable=too-many-positional-arguments
    learning_package_id: int,
    /,
    component_type: ComponentType,
    local_key: str,
    title: str,
    created: datetime,
    created_by: int | None = None,
    *,
    can_stand_alone: bool = True,
) -> tuple[Component, ComponentVersion]:
    """
    Create a Component and associated ComponentVersion atomically
    """
    with atomic():
        component = create_component(
            learning_package_id,
            component_type,
            local_key,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        component_version = create_component_version(
            component.pk,
            version_num=1,
            title=title,
            created=created,
            created_by=created_by,
        )
        return (component, component_version)


def get_component(component_pk: int, /) -> Component:
    """
    Get Component by its primary key.

    This is the same as the PublishableEntity's ID primary key.
    """
    return Component.with_publishing_relations.get(pk=component_pk)


def get_component_by_key(
    learning_package_id: int,
    /,
    namespace: str,
    type_name: str,
    local_key: str,
) -> Component:
    """
    Get a Component by its unique (namespace, type, local_key) tuple.
    """
    return Component.with_publishing_relations \
                    .get(
                        learning_package_id=learning_package_id,
                        component_type__namespace=namespace,
                        component_type__name=type_name,
                        local_key=local_key,
                    )


def get_component_by_uuid(uuid: UUID) -> Component:
    return Component.with_publishing_relations.get(publishable_entity__uuid=uuid)


def get_component_version_by_uuid(uuid: UUID) -> ComponentVersion:
    return (
        ComponentVersion
        .objects
        .select_related(
            "component",
            "component__learning_package",
        )
        .get(publishable_entity_version__uuid=uuid)
    )


def component_exists_by_key(
    learning_package_id: int,
    /,
    namespace: str,
    type_name: str,
    local_key: str
) -> bool:
    """
    Return True/False for whether a Component exists.

    Note that a Component still exists even if it's been soft-deleted (there's
    no current Draft version for it), or if it's been unpublished.
    """
    try:
        _component = Component.objects.only('pk', 'component_type').get(
            learning_package_id=learning_package_id,
            component_type__namespace=namespace,
            component_type__name=type_name,
            local_key=local_key,
        )
        return True
    except Component.DoesNotExist:
        return False


def get_components(  # pylint: disable=too-many-positional-arguments
    learning_package_id: int,
    /,
    draft: bool | None = None,
    published: bool | None = None,
    namespace: str | None = None,
    type_names: list[str] | None = None,
    draft_title: str | None = None,
    published_title: str | None = None,
) -> QuerySet[Component]:
    """
    Fetch a QuerySet of Components for a LearningPackage using various filters.

    This method will pre-load all the relations that we need in order to get
    info from the Component's draft and published versions, since we'll be
    referencing these a lot.
    """
    qset = Component.with_publishing_relations \
                    .filter(learning_package_id=learning_package_id) \
                    .order_by('pk')

    if draft is not None:
        qset = qset.filter(publishable_entity__draft__version__isnull=not draft)
    if published is not None:
        qset = qset.filter(publishable_entity__published__version__isnull=not published)
    if namespace is not None:
        qset = qset.filter(component_type__namespace=namespace)
    if type_names is not None:
        qset = qset.filter(component_type__name__in=type_names)
    if draft_title is not None:
        qset = qset.filter(
            Q(publishable_entity__draft__version__title__icontains=draft_title) |
            Q(local_key__icontains=draft_title)
        )
    if published_title is not None:
        qset = qset.filter(
            Q(publishable_entity__published__version__title__icontains=published_title) |
            Q(local_key__icontains=published_title)
        )

    return qset


def get_collection_components(
    learning_package_id: int,
    collection_key: str,
) -> QuerySet[Component]:
    """
    Returns a QuerySet of Components relating to the PublishableEntities in a Collection.

    Components have a one-to-one relationship with PublishableEntity, but the reverse may not always be true.
    """
    return Component.objects.filter(
        learning_package_id=learning_package_id,
        publishable_entity__collections__key=collection_key,
    ).order_by('pk')


def look_up_component_version_content(
    learning_package_key: str,
    component_key: str,
    version_num: int,
    key: Path,
) -> ComponentVersionContent:
    """
    Look up ComponentVersionContent by human readable keys.

    Can raise a django.core.exceptions.ObjectDoesNotExist error if there is no
    matching ComponentVersionContent.

    This API call was only used in our proof-of-concept assets media server, and
    I don't know if we wantto make it a part of the public interface.
    """
    queries = (
        Q(component_version__component__learning_package__key=learning_package_key)
        & Q(component_version__component__publishable_entity__key=component_key)
        & Q(component_version__publishable_entity_version__version_num=version_num)
        & Q(key=key)
    )
    return ComponentVersionContent.objects \
                                  .select_related(
                                      "content",
                                      "content__media_type",
                                      "component_version",
                                      "component_version__component",
                                      "component_version__component__learning_package",
                                  ).get(queries)


def create_component_version_content(
    component_version_id: int,
    content_id: int,
    /,
    key: str,
) -> ComponentVersionContent:
    """
    Add a Content to the given ComponentVersion

    We don't allow keys that would be absolute paths, e.g. ones that start with
    '/'. Storing these causes headaches with building relative paths and because
    of mismatches with things that expect a leading slash and those that don't.
    So for safety and consistency, we strip off leading slashes and emit a
    warning when we do.
    """
    if key.startswith('/'):
        logger.warning(
            "Absolute paths are not supported: "
            f"removed leading '/' from ComponentVersion {component_version_id} "
            f"content key: {repr(key)} (content_id: {content_id})"
        )
        key = key.lstrip('/')

    cvrc, _created = ComponentVersionContent.objects.get_or_create(
        component_version_id=component_version_id,
        content_id=content_id,
        key=key,
    )
    return cvrc


class AssetError(StrEnum):
    """Error codes related to fetching ComponentVersion assets."""
    ASSET_PATH_NOT_FOUND_FOR_COMPONENT_VERSION = auto()
    ASSET_HAS_NO_DOWNLOAD_FILE = auto()


def _get_component_version_info_headers(component_version: ComponentVersion) -> dict[str, str]:
    """
    These are the headers we can derive based on a valid ComponentVersion.

    These headers are intended to ease development and debugging, by showing
    where this static asset is coming from. These headers will work even if
    the asset path does not exist for this particular ComponentVersion.
    """
    component = component_version.component
    learning_package = component.learning_package
    return {
        # Component
        "X-Open-edX-Component-Key": component.publishable_entity.key,
        "X-Open-edX-Component-Uuid": component.uuid,
        # Component Version
        "X-Open-edX-Component-Version-Uuid": component_version.uuid,
        "X-Open-edX-Component-Version-Num": str(component_version.version_num),
        # Learning Package
        "X-Open-edX-Learning-Package-Key": learning_package.key,
        "X-Open-edX-Learning-Package-Uuid": learning_package.uuid,
    }


def get_redirect_response_for_component_asset(
    component_version_uuid: UUID,
    asset_path: Path,
    public: bool = False,
) -> HttpResponse:
    """
    ``HttpResponse`` for a reverse-proxy to serve a ``ComponentVersion`` asset.

    :param component_version_uuid: ``UUID`` of the ``ComponentVersion`` that the
        asset is part of.

    :param asset_path: Path to the asset being requested.

    :param public: Is this asset going to be made available without auth checks?
        If ``True``, this will return an ``HttpResponse`` that can be cached in
        a CDN and shared across many clients.

    **Response Codes**

    If the asset exists for this ``ComponentVersion``, this function will return
    an ``HttpResponse`` with a status code of ``200``.

    If the specified asset does not exist for this ``ComponentVersion``, or if
    the ``ComponentVersion`` itself does not exist, the response code will be
    ``404``.

    This function does not do auth checking of any sort. It will never return
    a ``401`` or ``403`` response code. That is by design. Figuring out who is
    making the request and whether they have permission to do so is the
    responsiblity of whatever is calling this function.

    **Metadata Headers**

    The ``HttpResponse`` returned by this function will have headers describing
    the asset and the ``ComponentVersion`` it belongs to (if it exists):

    * ``Content-Type``
    * ``Etag`` (this will be the asset's hash digest)
    * ``X-Open-edX-Component-Key``
    * ``X-Open-edX-Component-Uuid``
    * ``X-Open-edX-Component-Version-Uuid``
    * ``X-Open-edX-Component-Version-Num``
    * ``X-Open-edX-Learning-Package-Key``
    * ``X-Open-edX-Learning-Package-Uuid``

    **Asset Redirection**

    For performance reasons, the ``HttpResponse`` object returned by this
    function does not contain the actual content data of the asset. It requires
    an appropriately configured reverse proxy server that handles the
    ``X-Accel-Redirect`` header (both Caddy and Nginx support this).

    .. warning::
        If you add any headers here, you may need to add them in the "media"
        service container's reverse proxy configuration. In Tutor, this is a
        Caddyfile. All non-standard HTTP headers should be prefixed with
        ``X-Open-edX-``.
    """
    # Helper to generate error header messages.
    def _error_header(error: AssetError) -> dict[str, str]:
        return {"X-Open-edX-Error": str(error)}

    # Check: Does the ComponentVersion exist?
    try:
        component_version = (
            ComponentVersion
            .objects
            .select_related("component", "component__learning_package")
            .get(publishable_entity_version__uuid=component_version_uuid)
        )
    except ComponentVersion.DoesNotExist:
        # No need to add headers here, because no ComponentVersion was found.
        logger.error(f"Asset Not Found: No ComponentVersion with UUID {component_version_uuid}")
        return HttpResponseNotFound()

    # At this point we know that the ComponentVersion exists, so we can build
    # those headers...
    info_headers = _get_component_version_info_headers(component_version)

    # Check: Does the ComponentVersion have the requested asset (Content)?
    try:
        cv_content = component_version.componentversioncontent_set.get(key=asset_path)
    except ComponentVersionContent.DoesNotExist:
        logger.error(f"ComponentVersion {component_version_uuid} has no asset {asset_path}")
        info_headers.update(
            _error_header(AssetError.ASSET_PATH_NOT_FOUND_FOR_COMPONENT_VERSION)
        )
        return HttpResponseNotFound(headers=info_headers)

    # Check: Does the Content have a downloadable file, instead of just inline
    # text? It's easy for us to grab this content and stream it to the user
    # anyway, but we're explicitly not doing so because streaming large text
    # fields from the database is less scalable, and we don't want to encourage
    # that usage pattern.
    content = cv_content.content
    if not content.has_file:
        logger.error(
           f"ComponentVersion {component_version_uuid} has asset {asset_path}, "
           "but it is not downloadable (has_file=False)."
        )
        info_headers.update(
            _error_header(AssetError.ASSET_HAS_NO_DOWNLOAD_FILE)
        )
        return HttpResponseNotFound(headers=info_headers)

    # At this point, we know that there is valid Content that we want to send.
    # This adds Content-level headers, like the hash/etag and content type.
    info_headers.update(contents_api.get_content_info_headers(content))

    # Recompute redirect headers (reminder: this should never be cached).
    redirect_headers = contents_api.get_redirect_headers(content.path, public)
    logger.info(
        "Asset redirect (uncached metadata): "
        f"{component_version_uuid}/{asset_path} -> {redirect_headers}"
    )

    return HttpResponse(headers={**info_headers, **redirect_headers})
