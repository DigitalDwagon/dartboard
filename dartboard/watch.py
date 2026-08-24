import concurrent.futures
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent

from dartboard.upload import upload

in_progress_items: list[str] = []

class UploadEventHandler(FileSystemEventHandler):
    def __init__(self, config):
        print("made")
        self.config = config
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    def on_any_event(self, event: FileSystemEvent) -> None:
        print(event)
        self.item_directory_for_file(event)
        self.executor.submit()


    def item_directory_for_file(self, event: FileSystemEvent) -> Path:
        path = event.dest_path
        if not path:
            path = event.src_path
        path = Path(path).resolve()
        staging = Path(self.config.staging_directory).resolve()
        relative = path.relative_to(staging)

        return staging / relative.parts[0]

