import logging
import time

import internetarchive as ia

from datetime import datetime
from internetarchive import ArchiveSession, Item

# item identifier: (item object, time last checked)
items: dict[str, tuple[Item, datetime]] = {}

session: ArchiveSession | None = None

# TTL for cached items in seconds
CACHE_TTL_SECONDS = 60 * 60  # 1 hour
MAX_WAIT_FOR_ITEM_TRIES = 400

def get_item(identifier: str, *, cache: bool = True) -> Item:
    """Get the cached item object for an identifier, or fetch it if not cached"""
    now = datetime.now()
    if identifier in items and cache:
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

def wait_for_item(identifier: str) -> Item:
    """Wait for an archive.org item to be created, and then return it"""
    # Inspired by wikiteam3
    tries = MAX_WAIT_FOR_ITEM_TRIES
    while tries > 0:
        tries -= 1
        item = get_item(identifier, cache=False)
        if item.exists:
            return item

        logging.info(f"Waiting for item {identifier} to be created ({tries} tries left)...")
        time.sleep(30)

    raise RuntimeError(f"Is the Internet Archive overloaded? Item {identifier} not created after {MAX_WAIT_FOR_ITEM_TRIES} tries.")
