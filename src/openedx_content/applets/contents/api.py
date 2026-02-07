"""
Low Level Contents API (warning: UNSTABLE, in progress API)

Please look at the models.py file for more information about the kinds of data
are stored in this app.
"""
from __future__ import annotations

from datetime import datetime
from logging import getLogger

from django.core.files.base import ContentFile
from django.db.transaction import atomic

from openedx_django_lib.fields import create_hash_digest

from .models import Content, MediaType

# The public API that will be re-exported by openedx_content.api
# is listed in the __all__ entries below. Internal helper functions that are
# private to this module should start with an underscore. If a function does not
# start with an underscore AND it is not in __all__, that function is considered
# to be callable only by other apps in the authoring package.
__all__ = [
    "get_or_create_media_type",
    "get_content",
    "get_content_info_headers",
    "get_or_create_text_content",
    "get_or_create_file_content",
]


log = getLogger()


def get_or_create_media_type(mime_type: str) -> MediaType:
    """
    Return the MediaType.id for the desired mime_type string.

    If it is not found in the database, a new entry will be created for it. This
    lazy-writing means that MediaType entry IDs will *not* be the same across
    different server instances, and apps should not assume that will be the
    case. Even if we were to preload a bunch of common ones, we can't anticipate
    the different XBlocks that will be installed in different server instances,
    each of which will use their own MediaType.

    Caching Warning: Be careful about putting any caching decorator around this
    function (e.g. ``lru_cache``). It's possible that incorrect cache values
    could leak out in the event of a rollback–e.g. new types are introduced in
    a large import transaction which later fails. You can safely cache the
    results that come back from this function with a local dict in your import
    process instead.
    """
    if "+" in mime_type:
        base, suffix = mime_type.split("+")
    else:
        base = mime_type
        suffix = ""

    main_type, sub_type = base.split("/")
    media_type, _created = MediaType.objects.get_or_create(
        type=main_type,
        sub_type=sub_type,
        suffix=suffix,
    )

    return media_type


def get_content(content_id: int, /) -> Content:
    """
    Get a single Content object by its ID.

    Content is always attached to something when it's created, like to a
    ComponentVersion. That means the "right" way to access a Content is almost
    always going to be via those relations and not via this function. But I
    include this function anyway because it's tiny to write and it's better than
    someone using a get_or_create_* function when they really just want to get.
    """
    return Content.objects.get(id=content_id)


def get_or_create_text_content(
    learning_package_id: int,
    media_type_id: int,
    /,
    text: str,
    created: datetime,
    create_file: bool = False,
) -> Content:
    """
    Get or create a Content entry with text data stored in the database.

    Use this when you want to create relatively small chunks of text that need
    to be accessed quickly, especially if you're pulling back multiple rows at
    once. For example, this is the function to call when storing OLX for a
    component XBlock like a ProblemBlock.

    This function will *always* create a text entry in the database. In addition
    to this, if you specify ``create_file=True``, it will also save a copy of
    that text data to the file storage backend. This is useful if we want to let
    that file be downloadable by browsers in the LMS at some point.

    If you want to create a large text file, or want to create a text file that
    doesn't need to be stored in the database, call ``create_file_content``
    instead of this function.
    """
    text_as_bytes = text.encode('utf-8')
    hash_digest = create_hash_digest(text_as_bytes)

    with atomic():
        try:
            content = Content.objects.get(
                learning_package_id=learning_package_id,
                media_type_id=media_type_id,
                hash_digest=hash_digest,
            )
        except Content.DoesNotExist:
            content = Content(
                learning_package_id=learning_package_id,
                media_type_id=media_type_id,
                hash_digest=hash_digest,
                created=created,
                size=len(text_as_bytes),
                text=text,
                has_file=create_file,
            )
            content.full_clean()
            content.save()

            if create_file:
                content.write_file(ContentFile(text_as_bytes))

        return content


def get_or_create_file_content(
    learning_package_id: int,
    media_type_id: int,
    /,
    data: bytes,
    created: datetime,
) -> Content:
    """
    Get or create a Content with data stored in a file storage backend.

    Use this function to store non-text data, large data, or data where low
    latency access is not necessary. Also use this function (or
    ``get_or_create_text_content`` with ``create_file=True``) to store any
    Content that you want to be downloadable by browsers in the LMS, since the
    static asset serving system will only work with file-backed Content.
    """
    hash_digest = create_hash_digest(data)
    with atomic():
        try:
            content = Content.objects.get(
                learning_package_id=learning_package_id,
                media_type_id=media_type_id,
                hash_digest=hash_digest,
            )
        except Content.DoesNotExist:
            content = Content(
                learning_package_id=learning_package_id,
                media_type_id=media_type_id,
                hash_digest=hash_digest,
                created=created,
                size=len(data),
                text=None,
                has_file=True,
            )
            content.full_clean()
            content.save()

            content.write_file(ContentFile(data))

        return content


def get_content_info_headers(content: Content) -> dict[str, str]:
    """
    Return HTTP headers that are specific to this Content.

    This currently only consists of the Content-Type and ETag. These values are
    safe to cache.
    """
    return {
        "Content-Type": str(content.media_type),
        "Etag": content.hash_digest,
    }


def get_redirect_headers(
    stored_file_path: str,
    public: bool = False,
    max_age: int | None = None,
) -> dict[str, str]:
    """
    Return a dict of headers for file redirect and caching.

    This is a separate function from get_content_info_headers because the URLs
    returned in these headers produced by this function should never be put into
    the backend Django cache (redis/memcached). The `stored_file_path` location
    *is* cacheable though–that's the actual storage location for the resource,
    and not a link that could potentially expire.

    TODO: We need to add support for short-lived URL generation from the
    stored_file_path.
    """
    if public:
        # If an asset is public, then let it be cached by the reverse-proxy and
        # CDN, but do require that it be revalidated after the suggested max
        # age. This would help us do things like take a URL that was mistakenly
        # made public and make it require authentication. Fortunately, checking
        # that the content is up to date is a cheap operation, since it just
        # requires examining the Etag.
        cache_directive = "must-revalidate"

        # Default to an hour of caching, to make it easier to tighten access
        # later on.
        max_age = max_age or (5 * 60)
    else:
        # If an asset is meant to be private, that means this response should
        # not be cached by either the reverse-proxy or any CDN–it's only ever
        # cached on the user's browser. This is what you'd use for very granular
        # permissions checking, e.g. "only let them see this image if they have
        # access to the Component it's associated with". Note that we're not
        # doing ``Vary: Cookie`` because that would fill the reverse-proxy and
        # CDN caches with a lot of redundant entries.
        cache_directive = "private"

        # This only stays on the user's browser, so cache for a whole day. This
        # is okay to do because Content data is typically immutable–i.e. if an
        # asset actually changes, the user should be directed to a different URL
        # for it.
        max_age = max_age or (60 * 60 * 24)

    return {
        "Cache-Control": f"max-age={max_age}, {cache_directive}",
        "X-Accel-Redirect": stored_file_path,
    }
