"""
Utilities for tagging and taxonomy models
"""

RESERVED_TAG_CHARS = [
    '\t',  # Used in the database to separate tag levels in the "lineage" field
           # e.g. lineage="Earth\tNorth America\tMexico\tMexico City\t"
    ' > ',  # Used in the search index and Instantsearch frontend to separate tag levels
            # e.g. tags_level3="Earth > North America > Mexico > Mexico City"
    ';',   # Used in CSV exports to separate multiple tags from the same taxonomy
           # e.g. languages-v1: en;es;fr
]
TAGS_CSV_SEPARATOR = RESERVED_TAG_CHARS[2]

TAG_EXTERNAL_ID_MAX_LENGTH = 255


def tag_external_id_candidate(value: str, attempt: int = 1) -> str:
    """
    Generate a candidate ``external_id`` for a tag from its ``value``.
    """
    if attempt == 1:
        return value.strip()[:TAG_EXTERNAL_ID_MAX_LENGTH].strip()
    suffix = f"-{attempt}"
    return value.strip()[: TAG_EXTERNAL_ID_MAX_LENGTH - len(suffix)].strip() + suffix
