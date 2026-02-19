"""
These models are the most basic pieces of content we support. Think of them as
the simplest building blocks to store data with. They need to be composed into
more intelligent data models to be useful.
"""
from __future__ import annotations

from functools import cache, cached_property
from logging import getLogger

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.base import File
from django.core.files.storage import Storage
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.module_loading import import_string

from openedx_django_lib.fields import (
    MultiCollationTextField,
    case_insensitive_char_field,
    hash_field,
    manual_date_time_field,
)
from openedx_django_lib.managers import WithRelationsManager

from ..publishing.models import LearningPackage

logger = getLogger()

__all__ = [
    "MediaType",
    "Content",
]


@cache
def get_storage() -> Storage:
    """
    Return the Storage instance for our Content file persistence.

    This will first search for an OPENEDX_LEARNING config dictionary and return
    a Storage subclass based on that configuration.

    If there is no value for the OPENEDX_LEARNING setting, we return the default
    MEDIA storage class. TODO: Should we make it just error instead?
    """
    # TODO: Document & rename this setting (https://github.com/openedx/openedx-core/issues/481)
    config_dict = getattr(settings, 'OPENEDX_LEARNING', {})

    if 'MEDIA' in config_dict:
        storage_cls = import_string(config_dict['MEDIA']['BACKEND'])
        options = config_dict['MEDIA'].get('OPTIONS', {})
        return storage_cls(**options)

    raise ImproperlyConfigured(
        "Cannot access file storage: Missing the OPENEDX_LEARNING['MEDIA'] "
        "setting, which should have a storage BACKEND and OPTIONS values for "
        "a Storage subclass. These files should be stored in a location that "
        "is NOT publicly accessible to browsers (so not in the MEDIA_ROOT)."
    )


class MediaType(models.Model):
    """
    Stores Media types for use by Content models.

    This is the same as MIME types (the IANA renamed MIME Types to Media Types).
    We don't pre-populate this table, so APIs that add Content must ensure that
    the desired Media Type exists.

    Media types are written as {type}/{sub_type}+{suffix}, where suffixes are
    seldom used. Examples:

    * application/json
    * text/css
    * image/svg+xml
    * application/vnd.openedx.xblock.v1.problem+xml

    We have this as a separate model (instead of a field on Content) because:

    1. We can save a lot on storage and indexing for Content if we're just
       storing foreign key references there, rather than the entire content
       string to be indexed. This is especially relevant for our (long) custom
       types like "application/vnd.openedx.xblock.v1.problem+xml".
    2. These values can occasionally change. For instance, "text/javascript" vs.
       "application/javascript". Also, we will be using a fair number of "vnd."
       style of custom content types, and we may want the flexibility of
       changing that without having to worry about migrating millions of rows of
       Content.
    """
    # We're going to have many foreign key references from Content into this
    # model, and we don't need to store those as 8-byte BigAutoField, as is the
    # default for this app. It's likely that a SmallAutoField would work, but I
    # can just barely imagine using more than 32K Media types if we have a bunch
    # of custom "vnd." entries, or start tracking suffixes and parameters. Which
    # is how we end up at the 4-byte AutoField.
    id = models.AutoField(primary_key=True)

    # Media types are denoted as {type}/{sub_type}+{suffix}. We currently do not
    # support parameters.

    # Media type, e.g. "application", "text", "image". Per RFC 4288, this can be
    # at most 127 chars long and is case insensitive. In practice, it's almost
    # always written in lowercase.
    type = case_insensitive_char_field(max_length=127, blank=False, null=False)

    # Media sub-type, e.g. "json", "css", "png". Per RFC 4288, this can be at
    # most 127 chars long and is case insensitive. In practice, it's almost
    # always written in lowercase.
    sub_type = case_insensitive_char_field(max_length=127, blank=False, null=False)

    # Suffix, like "xml" (e.g. "image/svg+xml"). Usually blank. I couldn't find
    # an RFC description of the length limit, and 127 is probably excessive. But
    # this table should be small enough where it doesn't really matter.
    suffix = case_insensitive_char_field(max_length=127, blank=True, null=False)

    class Meta:
        constraints = [
            # Make sure all (type + sub_type + suffix) combinations are unique.
            models.UniqueConstraint(
                fields=[
                    "type",
                    "sub_type",
                    "suffix",
                ],
                name="oel_contents_uniq_t_st_sfx",
            ),
        ]

    def __str__(self) -> str:
        base = f"{self.type}/{self.sub_type}"
        if self.suffix:
            return f"{base}+{self.suffix}"
        return base


class Content(models.Model):
    """
    This is the most primitive piece of content data.

    This model serves to lookup, de-duplicate, and store text and files. A piece
    of Content is identified purely by its data, the media type, and the
    LearningPackage it is associated with. It has no version or file name
    metadata associated with it. It exists to be a dumb blob of data that higher
    level models like ComponentVersions can assemble together.

    # In-model Text vs. File

    That being said, the Content model does have some complexity to accomodate
    different access patterns that we have in our app. In particular, it can
    store data in two ways: the ``text`` field and a file (``has_file=True``)
    A Content object must use at least one of these methods, but can use both if
    it's appropriate.

    Use the ``text`` field when:
    * the content is a relatively small (< 50K, usually much less) piece of text
    * you want to do be able to query up update across many rows at once
    * low, predictable latency is important

    Use file storage when:
    * the content is large, or not text-based
    * you want to be able to serve the file content directly to the browser

    The high level tradeoff is that ``text`` will give you faster access, and
    file storage will give you a much more affordable and scalable backend. The
    backend used for files will also eventually allow direct browser download
    access, whereas the ``text`` field will not. But again, you can use both at
    the same time if needed.

    # Association with a LearningPackage

    Content is associated with a specific LearningPackage. Doing so allows us to
    more easily query for how much storge space a specific LearningPackage
    (likely a library) is using, and to clean up unused data.

    When we get to borrowing Content across LearningPackages, it's likely that
    we will want to copy them. That way, even if the originating LearningPackage
    is deleted, it won't break other LearningPackages that are making use if it.

    # Media Types, and file duplication

    Content is almost 1:1 with the files that it pushes to a storage backend,
    but not quite. The file locations are generated purely as a product of the
    LearningPackage UUID and the Content's ``hash_digest``, but Content also
    takes into account the ``media_type``.

    For example, say we had a Content with the following data:

        ["hello", "world"]

    That is legal syntax for both JSON and YAML. If you want to attach some
    YAML-specific metadata in a new model, you could make it 1:1 with the
    Content that matched the "application/yaml" media type. The YAML and JSON
    versions of this data would be two separate Content rows that would share
    the same ``hash_digest`` value. If they both stored a file, they would be
    pointing to the same file location. If they only used the ``text`` field,
    then that value would be duplicated across the two separate Content rows.

    The alternative would have been to associate media types at the level where
    this data was being added to a ComponentVersion, but that would have added
    more complexity. Right now, you could make an ImageContent 1:1 model that
    analyzed images and created metatdata entries for them (dimensions, GPS)
    without having to understand how ComponentVerisons work.

    This is definitely an edge case, and it's likely the only time collisions
    like this will happen in practice is with blank files. It also means that
    using this table to measure disk usage may be slightly inaccurate when used
    in a LearningPackage with collisions–though we expect to use numbers like
    that mostly to get a broad sense of usage and look for major outliers,
    rather than for byte-level accuracy (it wouldn't account for the non-trivial
    indexing storage costs either).

    # Immutability

    From the outside, Content should appear immutable. Since the Content is
    looked up by a hash of its data, a change in the data means that we should
    look up the hash value of that new data and create a new Content if we don't
    find a match.

    That being said, the Content model has different ways of storing that data,
    and that is mutable. We could decide that a certain type of Content should
    be optimized to store its text in the table. Or that a content type that we
    had previously only stored as text now also needs to be stored on in the
    file storage backend so that it can be made available to be downloaded.
    These operations would be done as data migrations.

    # Extensibility

    Third-party apps are encouraged to create models that have a OneToOneField
    relationship with Content. For instance, an ImageContent model might join
    1:1 with all Content that has image/* media types, and provide additional
    metadata for that data.
    """
    # Max size of the file.
    MAX_FILE_SIZE = 50_000_000

    # 50K is our limit for text data, like OLX. This means 50K *characters*,
    # not bytes. Since UTF-8 encodes characters using as many as 4 bytes, this
    # could be as much as 200K of data if we had nothing but emojis.
    MAX_TEXT_LENGTH = 50_000

    objects: models.Manager[Content] = WithRelationsManager('media_type')

    learning_package = models.ForeignKey(LearningPackage, on_delete=models.CASCADE)

    # What is the Media type (a.k.a. MIME type) of this data?
    media_type = models.ForeignKey(MediaType, on_delete=models.PROTECT)

    # This is the size of the file in bytes. This can be different than the
    # character length of a text file, since UTF-8 encoding can use anywhere
    # between 1-4 bytes to represent any given character.
    size = models.PositiveBigIntegerField(
        validators=[MaxValueValidator(MAX_FILE_SIZE)],
    )

    # This hash value may be calculated using create_hash_digest from the
    # openedx.lib.fields module. When storing text, we hash the UTF-8
    # encoding of that text value, regardless of whether we also write it to a
    # file or not. When storing just a file, we hash the bytes in the file.
    hash_digest = hash_field()

    # Do we have file data stored for this Content in our file storage backend?
    # We use has_file instead of a FileField because it's more space efficient.
    # The location of a Content's file data is derivable from the Learning
    # Package's UUID and the hash of the Content. There's no need to waste that
    # space to encode it in every row.
    has_file = models.BooleanField()

    # The ``text`` field contains the text representation of the Content, if
    # it is available. A blank value means means that we are storing text for
    # this Content, and that text happens to be an empty string. A null value
    # here means that we are not storing any text here, and the Content exists
    # only in file form. It is an error for ``text`` to be None and ``has_file``
    # to be False, since that would mean we haven't stored data anywhere at all.
    #
    text = MultiCollationTextField(
        blank=True,
        null=True,
        max_length=MAX_TEXT_LENGTH,
        # We don't really expect to ever sort by the text column, but we may
        # want to do case-insensitive searches, so it's useful to have a case
        # and accent insensitive collation.
        db_collations={
            "sqlite": "NOCASE",
            "mysql": "utf8mb4_unicode_ci",
        }
    )

    # This should be manually set so that multiple Content rows being set in
    # the same transaction are created with the same timestamp. The timestamp
    # should be UTC.
    created = manual_date_time_field()

    @cached_property
    def mime_type(self) -> str:
        """
        The IANA media type (a.k.a. MIME type) of the Content, in string form.

        MIME types reference:
          https://developer.mozilla.org/en-US/docs/Web/HTTP/Basics_of_HTTP/MIME_types
        """
        return str(self.media_type)

    @cached_property
    def path(self):
        """
        Logical path at which this content is stored (or would be stored).

        This path is relative to OPENEDX_LEARNING['MEDIA'] configured storage
        root. This file may not exist because has_file=False, or because we
        haven't written the file yet (this is the method we call when trying to
        figure out where the file *should* go).
        """
        return f"content/{self.learning_package.uuid}/{self.hash_digest}"

    def os_path(self):
        """
        The full OS path for the underlying file for this Content.

        This will not be supported by all Storage class types.

        This will return ``None`` if there is no backing file (has_file=False).
        """
        try:
            if self.has_file:
                return get_storage().path(self.path)
        except NotImplementedError:
            logger.warning("Storage backend does not support path()")
        return None

    def read_file(self) -> File:
        """
        Get a File object that has been open for reading.

        We intentionally don't expose an `open()` call where callers can open
        this file in write mode. Writing a Content file should happen at most
        once, and the logic is not obvious (see ``write_file``).

        At the end of the day, the caller can close the returned File and reopen
        it in whatever mode they want, but we're trying to gently discourage
        that kind of usage.
        """
        return get_storage().open(self.path, 'rb')

    def write_file(self, file: File) -> None:
        """
        Write file contents to the file storage backend.

        This function does nothing if the file already exists. Note that Content
        is supposed to be immutable, so this should normally only be called once
        for a given Content row.
        """
        storage = get_storage()

        # There are two reasons why a file might already exist even if the the
        # Content row is new:
        #
        # 1. We tried adding the file earlier, but an error rolled back the
        # state of the database. The file storage system isn't covered by any
        # sort of transaction semantics, so it won't get rolled back.
        #
        # 2. The Content is of a different MediaType. The same exact bytes can
        # be two logically separate Content entries if they are different file
        # types. This lets other models add data to Content via 1:1 relations by
        # ContentType (e.g. all SRT files). This is definitely an edge case.
        #
        # 3. Similar to (2), but only part of the file was written before an
        # error occurred. This seems unlikely, but possible if the underlying
        # storage engine writes in chunks.
        if storage.exists(self.path) and storage.size(self.path) == file.size:
            return
        storage.save(self.path, file)

    def file_url(self) -> str:
        """
        This will sometimes be a time-limited signed URL.
        """
        return get_storage().url(self.path)

    def clean(self):
        """
        Make sure we're actually storing *something*.

        If this Content has neither a file or text data associated with it,
        it's in a broken/useless state and shouldn't be saved.
        """
        if (not self.has_file) and (self.text is None):
            raise ValidationError(
                f"Content {self.pk} with hash {self.hash_digest} must either "
                "set a string value for 'text', or it must set has_file=True "
                "(or both)."
            )

    class Meta:
        constraints = [
            # Make sure we don't store duplicates of this raw data within the
            # same LearningPackage, unless they're of different MIME types.
            models.UniqueConstraint(
                fields=[
                    "learning_package",
                    "media_type",
                    "hash_digest",
                ],
                name="oel_content_uniq_lc_media_type_hash_digest",
            ),
        ]
        indexes = [
            # LearningPackage (reverse) Size Index:
            #   * Find the largest Content entries.
            models.Index(
                fields=["learning_package", "-size"],
                name="oel_content_idx_lp_rsize",
            ),
        ]
        verbose_name = "Content"
        verbose_name_plural = "Contents"
