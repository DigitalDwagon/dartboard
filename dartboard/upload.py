import hashlib
import json
import logging
import os.path
import re
import time

import internetarchive
from internetarchive import get_tasks, get_session, ArchiveSession

from dartboard.config import Config
from dartboard.items import UploaderMeta
from dartboard.__version__ import version

item_cache: dict[str, bool] = {}

def upload(config: Config, path: str):
    path = os.path.normpath(path)
    logging.log(logging.INFO, f"Uploading {path}...")

    identifier: str | None = get_identifier(path)
    if not identifier:
        return False
    logging.log(logging.INFO, f"Identifier: {identifier} - this item will try to upload to https://archive.org/details/{identifier}")

    if not has_files_to_upload(path):
        logging.log(logging.INFO, f"Directory has no files to upload.")
        return False

    if identifier in item_cache and not item_cache[identifier]:
        logging.log(logging.INFO, f"{identifier} does not exist (cached) and doesn't have a metadata file. Cannot upload.")


    session: ArchiveSession = get_session({"s3": {"access": config.s3_key, "secret": config.s3_secret}})
    item = internetarchive.get_item(identifier, archive_session=session)
    metadata, settings = load_meta_info(path)
    item_cache[identifier] = item.exists

    if not metadata and not item.exists:
        logging.log(logging.ERROR, f"{identifier} does not exist and doesn't have a metadata file. Cannot upload.")
        return False

    if settings.set_upload_state:
        metadata["upload-state"] = "uploading"

    logging.log(logging.INFO, f"Metadata: {json.dumps(metadata, indent=4)}")

    files_to_upload: dict[str, str] = get_files_to_upload(path, item)

    logging.log(logging.INFO, f"Found files:")
    for filepath, destination in files_to_upload.items():
        logging.log(logging.INFO, f"    {filepath} -> {destination}")

    if not files_to_upload.items():
        logging.log(logging.INFO, f"No files to upload!")
        return True

    uploaded_files: dict[str, str] = {}

    headers: dict[str, str] = {}
    if settings.send_size_hint:
        headers["x-archive-size-hint"] = str(get_size(files_to_upload))

    while True:
        filepath, destination = files_to_upload.popitem()

        logging.log(logging.INFO, f"{identifier} - uploading {filepath} to {destination}...")

        if config.dry_run:
            logging.log(logging.INFO, f"Dry run - skipping upload")
        else:
            _ = item.upload(files={destination: filepath},
                        metadata=metadata,
                        queue_derive=False,
                        headers=headers,
                        verbose=True,
                        access_key=config.s3_key,
                        secret_key=config.s3_secret
                        )
            uploaded_files[filepath] = destination

        # if no files to upload, check disk for more files
        if not files_to_upload:
            if has_files_to_upload(path, uploaded_files):
                logging.log(logging.INFO, f"Checking for more files to upload...")
                files_to_upload = get_files_to_upload(path, item)
                # remove already uploaded files from the list
                for filepath in uploaded_files:
                    if filepath in files_to_upload:
                        files_to_upload.pop(filepath, None)
            else:
                break

    for filepath, destination in uploaded_files.items():
        # move files to done directory or delete as per config
        if config.delete_after_upload:
            logging.log(logging.INFO, f"Deleting {filepath} after upload...")
            if not config.dry_run:
                os.remove(filepath)
        else:
            done_path = os.path.join(config.done_directory, os.path.relpath(filepath, path))
            done_dir = os.path.dirname(done_path)
            if not os.path.exists(done_dir):
                logging.log(logging.INFO, f"Creating directory {done_dir}...")
                if not config.dry_run:
                    os.makedirs(done_dir)
            logging.log(logging.INFO, f"Moving {filepath} to {done_path} after upload...")
            if not config.dry_run:
                os.rename(filepath, done_path)


    """


    for filepath, destination in files_to_upload.items():
        headers = {}

        if settings.send_size_hint and not uploaded_files:
            headers["x-archive-size-hint"] = str(get_size(files_to_upload))

        logging.log(logging.INFO, f"Uploading {filepath} to {identifier}...")
        if config.dry_run:
            logging.log(logging.INFO, f"Dry run - skipping upload")
            continue
        item.upload(files={destination: filepath},
                    metadata=metadata,
                    queue_derive=False,
                    headers=headers,
                    verbose=True,
                    access_key=config.s3_key,
                    secret_key=config.s3_secret
                    )
"""
    # Final upload completions after all files are uploaded - update the metadata, run derives, etc
    if config.dry_run:
        logging.log(logging.INFO, f"Dry run - exiting early! Can't update metadata or derive an item that does not exist.")
        return True

    if not wait_for_item(identifier):
        return False

    if settings.set_upload_state:
        metadata["upload-state"] = "uploaded"

    item = internetarchive.get_item(identifier, archive_session=session)
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
        logging.log(logging.INFO, f"Updating metadata for {identifier}...")
        logging.log(logging.INFO, f"Metadata changes: {json.dumps(metadata_changes, indent=4)}")
        item.modify_metadata(metadata_changes)

    if settings.derive:
        # TODO: check that there is no queued/running derive task already
        logging.log(logging.INFO, f"Deriving {identifier}...")
        tasks = get_tasks(identifier, {"cmd":"derive.php", "history":"0"}, archive_session=session)
        if tasks:
            logging.log(logging.INFO, f"-> Found a derive task ({tasks.pop().task_id}) already running for {identifier} - see https://archive.org/history/{identifier}")
        else:
            ""
            item.derive()

    logging.log(logging.INFO, f"Success! Upload complete - {identifier} is now available at https://archive.org/details/{identifier}")



    return True


def get_size(files: dict[str, str]) -> int:
    size = 0
    for file in files:
        size += os.path.getsize(file)
    return size

def get_files_to_upload(path: str, item: internetarchive.Item) -> dict[str, str]:
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link
            if not os.path.islink(fp):
                # get the relative path
                rel_path = os.path.relpath(fp, path)
                rel_path = rel_path.replace(os.path.sep, "/")
                abs_path = os.path.abspath(fp)
                files[abs_path] = rel_path

    # remove __ia_meta.json and __uploader_meta.json from the list
    for path, destination in list(files.items()):
        if destination == "__ia_meta.json" or destination == "__uploader_meta.json":
            files.pop(path, None)

    if not item.exists:
        return files

    for path, destination in list(files.items()):
        ia_file = next((file for file in item.files if file["name"] == destination), None)

        if ia_file:
            if ia_file["md5"] == hashlib.md5(open(path, "rb").read()).hexdigest():
                logging.log(logging.INFO, f"{destination} already exists in {item.identifier}. Skipping...")
                # remove the file from the list
                files.pop(path, None)
                continue
            else:
                raise Exception(f"{destination} already exists in {item.identifier}, but the hashes don't match.")

    return files

def has_files_to_upload(path: str, uploaded_files: dict[str, str] | None = None):
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            if f == "__ia_meta.json" or f == "__uploader_meta.json":
                continue
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link or directory
            if not os.path.islink(fp) and not os.path.isdir(fp):
                if uploaded_files:
                    rel_path = os.path.relpath(fp, path)
                    rel_path = rel_path.replace(os.path.sep, "/")
                    abs_path = os.path.abspath(fp)
                    if abs_path in uploaded_files:
                        continue
                return True
    return False

def get_identifier(path: str) -> str | None:
    if not os.path.isdir(path):
        logging.log(logging.ERROR, f"{path} is not a directory")
        return None

    identifier: str = os.path.basename(path)

    if not identifier:
        logging.log(logging.ERROR, f"{path} does not have an identifier")
        return None

    # identifier validity: https://archive.org/developers/metadata-schema/index.html#archive-org-identifiers
    if not re.match(r"^[a-zA-Z0-9-_.]{5,100}$", identifier):
        logging.log(logging.ERROR, f"{identifier} is not a valid identifier. Identifiers should be 5-100 characters long and can only contain letters, numbers, dashes, underscores, and periods.")
        return None

    return identifier

def load_meta_info(path: str) -> tuple[dict[str, str | list[str]], UploaderMeta]:
    metadata = {}
    settings: UploaderMeta = UploaderMeta()

    try:
        with open(os.path.join(path, "__ia_meta.json"), "r") as meta_file:
            raw_metadata = meta_file.read()
            if raw_metadata:
                metadata = json.loads(raw_metadata)
    except FileNotFoundError:
        pass

    try:
        with open(os.path.join(path, "__uploader_meta.json"), "r") as meta_file:
            settings_raw = meta_file.read()
            if settings_raw:
                settings: UploaderMeta =  UploaderMeta.from_json(settings_raw)
    except FileNotFoundError:
        pass

    if settings.set_scanner:
        if "scanner" not in metadata or not metadata["scanner"]:
            metadata["scanner"] = []
        if isinstance(metadata["scanner"], str):
            metadata["scanner"] = [metadata["scanner"]]
        metadata["scanner"].append(f"dartboard (v{version})")

    return metadata, settings

def wait_for_item(identifier: str) -> bool:
    # "borrowed" and modified from the wikiteam3 uploader
    item = internetarchive.get_item(identifier)
    tries = 400
    for tries_left in range(tries, 0, -1):
        if item.exists:
            return True

        logging.log(logging.INFO, msg=f"Waiting for the item to be created... ({tries_left} tries left)  ...")
        if tries < 395:
            logging.log(logging.INFO, msg=f"Is IA overloaded? Still waiting for item to be created ({tries_left} tries left)  ...")
        time.sleep(30)
        item = internetarchive.get_item(identifier)

    if not item.exists:
        logging.log(logging.ERROR, msg=f"IA overloaded, the item is still not ready after {400 * 30} seconds")
    return False