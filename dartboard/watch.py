import concurrent.futures
import logging
import threading
import time
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path
from typing import override

from watchdog.events import FileSystemEventHandler, FileSystemEvent

from dartboard.config import config
from dartboard.upload import upload

in_progress_items: set[str] = set()

lock = threading.Lock()

def find_item_folder(modified_file: str) -> Path | None:
    """
    Return the item folder inside staging_directory that contains modified_file.
    Examples:
        staging='/a/b/c', modified='/a/b/c/d' -> '/a/b/c/d'
        staging='/a/b/c', modified='/a/b/c/d/e/f.txt' -> '/a/b/c/d'
    Returns None if:
        - modified_file is not under staging_directory
        - modified_file is the same file as staging_directory
        - the calculated item folder does not exist
        - the calculated item folder is not a directory
    """
    staging = Path(config.staging_directory).resolve(strict=False)
    modified = Path(modified_file).resolve(strict=False)

    try:
        rel = modified.relative_to(staging)
    except ValueError:
        # modified_file not in staging_directory
        return None

    # If identical (relative '.'), no item folder exists
    if rel == Path('.') or len(rel.parts) == 0:
        return None

    # staging/<first_component_of_relative_path>
    item_path = (staging / rel.parts[0]).resolve(strict=False)

    if not item_path.exists() or not item_path.is_dir():
        return None
    return item_path


def scan_staging_directory(executor: ThreadPoolExecutor) -> None:
    """Scan staging directory for items and submit any that haven't been processed yet."""
    staging = Path(config.staging_directory).resolve(strict=False)

    if not staging.exists() or not staging.is_dir():
        return

    for item_path in staging.iterdir():
        if item_path.is_dir():
            executor.submit(submit_upload, item_path)


def submit_upload(item_path: Path) -> None:
    with lock:
        if str(item_path.absolute()) in in_progress_items:
            return
        in_progress_items.add(str(item_path.absolute()))

    retries = 0
    while item_path.exists() and item_path.is_dir() and retries < 2:
        retries += 1
        # Do this in a loop to pick up files uploaded between the end of the "file upload" part of the upload starting
        # while the metadata changes, derive task, etc are being completed.
        # Limited to 2x in case there is some infinite loop somewhere.
        logging.info(f"Starting upload for {item_path} in {config.start_delay}s...")
        time.sleep(config.start_delay)
        result = upload(item_path.absolute())

        if not result:
            raise RuntimeError(f"Upload for {item_path!r} failed.")

    with lock:
        in_progress_items.remove(str(item_path.absolute()))

class UploadEventHandler(FileSystemEventHandler):
    def __init__(self):
        self.executor: ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

        self._stop_event: threading.Event = threading.Event()
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        scan_staging_directory(self.executor)


    def _scan_loop(self):
        while not self._stop_event.wait(config.scan_interval):
            scan_staging_directory(self.executor)

    @override
    def on_any_event(self, event: FileSystemEvent) -> None:
        path = event.dest_path
        if not path:
            path = event.src_path
        item_folder = find_item_folder(path)
        if not item_folder:
            return
        _ = self.executor.submit(submit_upload, item_folder)
        # self.executor.submit(upload())

