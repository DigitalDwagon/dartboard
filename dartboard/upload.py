import hashlib
import json
import logging
import os.path
import shutil
import threading
from pathlib import Path
import re
import time

import internetarchive as ia
from internetarchive import Item

from dartboard import cache
from dartboard.config import config
from dartboard.items import UploaderMeta
from dartboard.__version__ import version

# the number of times that an item (keyed by identifier) has failed to upload
item_failures: dict[str, int] = {}
item_failures_lock: threading.Lock = threading.Lock()

def _failed_item(identifier: str):
    with item_failures_lock:
        if config.max_retries < 0 or item_failures.get(identifier, 0) < config.max_retries:
            return

        logging.error(f"Item {identifier} has failed to upload {item_failures.get(identifier, 0)} times. Marking it failed!")

    staging_dir = Path(config.staging_directory)
    failure_dir = Path(config.failure_directory)
    item_dir = staging_dir / identifier

    if not item_dir.exists() or not item_dir.is_dir():
        raise RuntimeError("Trying to fail an item, but the item path does not exist or is not a directory?")

    _ = shutil.move(item_dir, failure_dir / item_dir.name)


def _ia_upload(item: Item, files: dict[str, str], metadata: dict[str, str | list[str]], headers: dict[str, str]) -> None:
    """Upload wrapper"""
    if config.dry_run:
        logging.info(f"Dry run - skipping upload of files {files}")
        return

    for remote_name, local_path in files.items():
        try:
            exists = os.path.exists(local_path)
            size = os.path.getsize(local_path) if exists else None
        except Exception as ex:
            exists = False
            size = None
            logging.debug(f"Failed to stat file {local_path}: {ex}")
        logging.debug(f"Upload mapping -> remote='{remote_name}' local='{local_path}' exists={exists} size={size}")

    try:
        result = item.upload(files=files,
                             metadata=metadata,
                             queue_derive=False,
                             headers=headers,
                             verbose=True,
                             delete=config.delete_after_upload,
                             access_key=config.s3_key,
                             secret_key=config.s3_secret)
    except Exception:
        with item_failures_lock:
            identifier = str(item.identifier)
            item_failures[identifier] = item_failures.get(identifier, 0) + 1
        _failed_item(identifier)

        logging.exception("Exception raised during item.upload()")
        raise

    # Safely log result/responses
    try:
        if isinstance(result, (list, tuple)):
            for r in result:
                try:
                    status = getattr(r, "status_code", None)
                    text = getattr(r, "text", None)
                    logging.info(f"Upload response: status={status} len_text={(len(text) if text else 0)}")
                except Exception:
                    logging.debug(f"Upload response (repr): {repr(r)}")
        else:
            # Some versions return a dict or other object
            try:
                logging.info("Result of upload: %s", json.dumps(result, indent=4))
            except Exception:
                logging.info(f"Result of upload (repr): {repr(result)}")
    except Exception:
        logging.debug("Failed to log upload result cleanly", exc_info=True)

def _handle_uploaded_file(itempath: Path, filepath: str | Path, identifier: str) -> None:
    # filepath may be an absolute path (normal uploads) or a relative path
    # (cleanup calls pass just the filename like "__ia_meta.json"). Resolve it
    # relative to the itempath when necessary.
    fp = Path(filepath)
    src_path = fp if fp.is_absolute() else (itempath / fp)

    if config.delete_after_upload:
        if config.dry_run:
            logging.info(f"Dry run - skipping delete of {src_path}")
            return
        try:
            src_path.unlink()
            logging.info(f"Deleted file {src_path} after upload.")
        except FileNotFoundError:
            logging.debug(f"File to delete not found: {src_path}")
        return

    # compute relative path and destination
    rel = os.path.relpath(str(src_path), str(itempath))
    done_path = Path(config.done_directory) / identifier / rel
    done_dir = done_path.parent
    if not done_dir.exists():
        logging.info(f"Creating directory {done_dir}...")
        if not config.dry_run:
            done_dir.mkdir(parents=True, exist_ok=True)
        else:
            logging.info(f"\tDry run - skipping directory creation")
    logging.info(f"Moving {src_path} to {done_path} after upload...")
    if config.dry_run:
        logging.info(f"\tDry run - skipping move")
        return
    try:
        _ = shutil.move(str(src_path), str(done_path))
    except FileNotFoundError:
        # log at debug so we don't spam ERROR for expected missing cleanup files
        logging.debug(f"File to move not found: {src_path}")


def upload(path: Path) -> bool:
    logging.info(f"Uploading {path}...")

    identifier: str | None = get_identifier(path)
    if not identifier:
        return False
    logging.info(f"Identifier: {identifier} - this item will try to upload to https://archive.org/details/{identifier}")

    metadata, settings = load_meta_info(path)
    item = cache.get_item(identifier)

    if not metadata and not item.exists:
        logging.error(f"{identifier} does not exist on IA and doesn't have a metadata file. Cannot upload.")
        return False

    if settings.set_upload_state:
        metadata["upload-state"] = "uploading"

    logging.info(f"Metadata: {json.dumps(metadata, indent=4)}")

    # absolute path on disk -> relative path in IA item
    files_to_upload: dict[str, str] = get_files_to_upload(path, item)
    uploaded_files: dict[str, str] = {}

    if not files_to_upload:
        logging.info(f"Directory has no files to upload.")
        return False

    logging.info(f"Files to upload: {json.dumps(files_to_upload, indent=4)}")

    headers: dict[str, str] = {}
    if settings.send_size_hint:
        headers["x-archive-size-hint"] = str(get_size(files_to_upload))

    while True:
        filepath, destination = files_to_upload.popitem()
        logging.info(f"{identifier} - uploading {filepath} to {destination}...")

        if uploaded_files:
            # cannot send size hint after the first file is uploaded
            headers.pop("x-archive-size-hint", None)

        # TODO - what happens when an upload fails? we MUST NOT call _handle_uploaded_file on items that failed to upload
        _ia_upload(item, {destination: filepath}, metadata, headers)
        _handle_uploaded_file(path, filepath, identifier)

        uploaded_files[filepath] = destination

        # if no files to upload, check disk for more files
        if not files_to_upload:
            logging.info(f"Checking if any additional files have been added...")
            files_to_upload = get_files_to_upload(path, item, uploaded_files)
            logging.info(f"Found {len(files_to_upload)} additional files to upload.")
            if not files_to_upload:
                break

    logging.info(f"Uploaded files: {json.dumps(uploaded_files, indent=4)}")

    # Final upload completions after all files are uploaded - update the metadata, run derives, etc
    if config.dry_run:
        logging.info(f"Dry run - exiting early! Can't update metadata or derive an item that does not exist.")
        return True

    if not wait_for_item(identifier):
        return False

    if settings.set_upload_state:
        metadata["upload-state"] = "uploaded"

    item = ia.get_item(identifier, archive_session=cache.session)
    metadata_changes = {}
    item_metadata = item.metadata
    # diff the metadata
    # keys that are in metadata but not in item_metadata or keys that are present in both, but have different values
    # ignore keys that are only in item_metadata
    #print(item_metadata)
    for key in metadata.keys():
        if key == "description" and "<a" in metadata[key]:
            # TODO: IA sanitizes description HTML (eg. with nofollow) so this will result in us always updating the description field...
            # For now we just skip it
            continue

        if key not in item_metadata:
            metadata_changes[key] = metadata[key]
            continue

        if isinstance(item_metadata[key], list) or isinstance(metadata[key], list):
            item_value = item_metadata[key] if isinstance(item_metadata[key], list) else [item_metadata[key]]
            metadata_value = metadata[key] if isinstance(metadata[key], list) else [metadata[key]]
            value = list(set(item_value) | set(metadata_value))

            if len(value) > len(item_value):
                metadata_changes[key] = value
            continue

        if item_metadata[key] != metadata[key]:
            metadata_changes[key] = metadata[key]


    # if there are changes, update the metadata
    if metadata_changes:
        logging.info(f"Updating metadata for {identifier}...")
        logging.info(f"Metadata changes: {json.dumps(metadata_changes, indent=4)}")
        item.modify_metadata(metadata_changes)

    if settings.derive:
        # TODO: check that there is no queued/running derive task already
        logging.info(f"Deriving {identifier}...")
        tasks = ia.get_tasks(identifier, {"cmd":"derive.php", "history":"0"}, archive_session=cache.session)
        if tasks:
            logging.info(f"-> Found a derive task ({tasks.pop().task_id}) already running for {identifier} - see https://archive.org/history/{identifier}")  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] the IA library sets .task_id directly off the API response without defining it as a param, causing this pyright error
        else:
            _ = item.derive()

    # Now that we are done, the item meta and uploader meta should be cleaned up with the uploaded files
    try:
        _handle_uploaded_file(path, Path("__ia_meta.json"), identifier)
        _handle_uploaded_file(path, Path("__uploader_meta.json"), identifier)
    except Exception:
        logging.exception("Exception cleaning up __ files: ")

    # Remove any empty directories left behind (including the base directory if empty)
    logging.info("Cleaning up empty directories...")
    if config.dry_run:
        logging.info("\tDry run - skipping directory removals")
    else:
        # Walk bottom-up so child directories are removed before parents
        for dirpath, _, _ in os.walk(str(path), topdown=False):
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    logging.info(f"Removed empty directory {dirpath}")
            except Exception:
                logging.debug(f"Failed to remove directory {dirpath}", exc_info=True)

    logging.info(f"Success! Upload complete - {identifier} is now available at https://archive.org/details/{identifier}")
    return True


def get_size(files: dict[str, str]) -> int:
    size = 0
    for filepath in files.keys():
        try:
            size += os.path.getsize(filepath)
        except OSError:
            logging.debug(f"Failed to stat file {filepath}", exc_info=True)
    return size


def get_files_to_upload(path: Path, item: Item, uploaded_files: dict[str, str] | None = None) -> dict[str, str]:
    files: dict[str, str] = {}
    for dirpath, _, filenames in os.walk(str(path)):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link
            if os.path.islink(fp):
                continue

            # get the relative path
            rel_path = os.path.relpath(fp, str(path)).replace(os.path.sep, "/")
            abs_path = os.path.abspath(fp)
            files[abs_path] = rel_path

    # remove __ia_meta.json and __uploader_meta.json from the list
    for filepath, destination in list(files.items()):
        if destination in ("__ia_meta.json", "__uploader_meta.json"):
            _ = files.pop(filepath, None)

    if not item.exists:
        return files

    for filepath, destination in list(files.items()):
        # skip if already uploaded
        if uploaded_files and filepath in uploaded_files:
            _ = files.pop(filepath, None)
            continue

        ia_file = next((f for f in item.files if f.get("name") == destination), None)

        if ia_file:
            md5_remote = ia_file.get("md5")
            if not md5_remote:
                # TODO - this *should* never happen, but what to do here? Probably should just fail the upload
                #        since IA files should always have a hash
                logging.info(f"{destination} already exists in {str(item.identifier)}. Skipping (no md5 to compare)...")
                _ = files.pop(filepath, None)
                continue

            try:
                with open(filepath, "rb") as f:
                    # TODO - don't read the whole file at once, these are big files
                    md5_local = hashlib.md5(f.read()).hexdigest()
            except Exception:
                logging.debug(f"Failed to read local file {filepath} for hashing", exc_info=True)
                raise

            if md5_remote == md5_local:
                logging.info(f"{destination} already exists in {str(item.identifier)}. Skipping...")
                # remove the file from the list
                _ = files.pop(filepath, None)
                continue
            else:
                raise Exception(f"{destination} already exists in {str(item.identifier)}, but the hashes don't match.")

    return files


def get_identifier(path: Path) -> str | None:
    if not path.is_dir():
        logging.error(f"{path} is not a directory")
        return None

    identifier: str = path.name

    if not identifier:
        logging.error(f"{path} does not have an identifier")
        return None

    # identifier validity: https://archive.org/developers/metadata-schema/index.html#archive-org-identifiers
    if not re.match(r"^[a-zA-Z0-9-_.]{5,100}$", identifier):
        logging.error(f"{identifier} is not a valid identifier. Identifiers should be 5-100 characters long and can only contain letters, numbers, dashes, underscores, and periods.")
        return None

    return identifier

def load_meta_info(path: Path) -> tuple[dict[str, str | list[str]], UploaderMeta]:
    metadata = {}
    settings: UploaderMeta | None = None

    try:
        meta_path = Path(path) / "__uploader_meta.json"
        if meta_path.exists():
            with meta_path.open("r") as meta_file:
                settings_raw = meta_file.read()
                if settings_raw:
                    settings = UploaderMeta.from_json(settings_raw)
    except FileNotFoundError:
        pass

    try:
        with open(path / "__ia_meta.json", "r") as meta_file:
            raw_metadata = meta_file.read()
            if raw_metadata:
                metadata = json.loads(raw_metadata)

            if settings and settings.set_scanner:
                if "scanner" not in metadata or not metadata["scanner"]:
                    metadata["scanner"] = []
                if isinstance(metadata["scanner"], str):
                    metadata["scanner"] = [metadata["scanner"]]
                metadata["scanner"].append(f"dartboard (v{version})")
    except FileNotFoundError:
        pass

    if not settings:
        settings = UploaderMeta()

    return metadata, settings

def wait_for_item(identifier: str) -> bool:
    # "borrowed" and modified from the wikiteam3 uploader
    item = ia.get_item(identifier)
    tries = 400
    for tries_left in range(tries, 0, -1):
        if item.exists:
            return True

        logging.info(msg=f"Waiting for the item to be created... ({tries_left} tries left)  ...")
        if tries_left < 395:
            logging.info(msg=f"Is IA overloaded? Still waiting for item to be created ({tries_left} tries left)  ...")
        time.sleep(30)
        item = ia.get_item(identifier)

    if not item.exists:
        logging.error(msg=f"IA overloaded, the item is still not ready after {400 * 30} seconds")
    return False