import internetarchive as ia

from datetime import datetime
from internetarchive import ArchiveSession, Item

# item identifier: (item object, time last checked)
items: dict[str, tuple[Item, datetime]] = {}

session: ArchiveSession | None = None

# TTL for cached items in seconds
CACHE_TTL_SECONDS = 60 * 60  # 1 hour

def get_item(identifier: str) -> Item:
    """Get the cached item object for an identifier, or fetch it if not cached"""
    now = datetime.now()
    if identifier in items:
        item, last_checked = items[identifier]
        # If we checked within the last hour, return the cached value
        if (now - last_checked).total_seconds() < CACHE_TTL_SECONDS:
            return item

    # If we don't have a cached value or it's stale, fetch the item
    item = ia.get_item(identifier, archive_session=session)
    items[identifier] = (item, now)
    return item

def item_exists(identifier: str) -> bool:
    """Check if an item exists on the Internet Archive (with caching)"""
    return get_item(identifier).exists