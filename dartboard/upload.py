import hashlib
import json
import logging
import os.path
from pathlib import Path
import re
import time

import internetarchive as ia
from internetarchive import Item

from dartboard import cache
from dartboard.config import config
from dartboard.items import UploaderMeta
from dartboard.__version__ import version



def _ia_upload(item: Item, files: dict[str, str], metadata: dict[str, str | list[str]], headers: dict[str, str]) -> None:
    if config.dry_run:
        logging.info(f"Dry run - skipping upload of files {files}")
        return

    result = item.upload(files=files,
                metadata=metadata,
                queue_derive=False,
                headers=headers,
                verbose=True,
                delete=config.delete_after_upload,
                access_key=config.s3_key,
                secret_key=config.s3_secret)

    logging.info("Result of upload: " + json.dumps(result, indent=4)) # TODO: debug print

def _handle_uploaded_file(itempath: str, filepath: str)-> None:
    if config.delete_after_upload:
        if config.dry_run:
            logging.log(logging.INFO, f"Dry run - skipping delete of {filepath}")
            return
        os.remove(filepath)
        logging.info(f"File {filepath} not deleted after upload. Upload failed?")

    done_path = os.path.join(config.done_directory, os.path.relpath(filepath, itempath))
    done_dir = os.path.dirname(done_path)
    if not os.path.exists(done_dir):
        logging.log(logging.INFO, f"Creating directory {done_dir}...")
        if not config.dry_run:
            os.makedirs(done_dir)
        else:
            logging.log(logging.INFO, f"\tDry run - skipping directory creation")
    logging.log(logging.INFO, f"Moving {filepath} to {done_path} after upload...")
    if config.dry_run:
        logging.log(logging.INFO, f"\tDry run - skipping move")
        return
    os.rename(filepath, done_path)


def upload(path: str) -> bool:
    path = os.path.normpath(path)
    logging.log(logging.INFO, f"Uploading {path}...")

    identifier: str | None = get_identifier(path)
    if not identifier:
        return False
    logging.log(logging.INFO, f"Identifier: {identifier} - this item will try to upload to https://archive.org/details/{identifier}")

    if not has_files_to_upload(path):
        logging.log(logging.INFO, f"Directory has no files to upload.")
        return False

    metadata, settings = load_meta_info(path)
    item = cache.get_item(identifier)

    if not metadata and not item.exists:
        logging.log(logging.ERROR, f"{identifier} does not exist on IA and doesn't have a metadata file. Cannot upload.")
        return False

    if settings.set_upload_state:
        metadata["upload-state"] = "uploading"

    logging.log(logging.INFO, f"Metadata: {json.dumps(metadata, indent=4)}")

    # absolute path on disk -> relative path in IA item
    files_to_upload: dict[str, str] = get_files_to_upload(path, item)
    uploaded_files: dict[str, str] = {}

    logging.log(logging.INFO, f"Files to upload: {json.dumps(files_to_upload, indent=4)}")

    headers: dict[str, str] = {}
    if settings.send_size_hint:
        headers["x-archive-size-hint"] = str(get_size(files_to_upload))

    while True:
        filepath, destination = files_to_upload.popitem()
        logging.log(logging.INFO, f"{identifier} - uploading {filepath} to {destination}...")

        if uploaded_files:
            # cannot send size hint after the first file is uploaded
            headers.pop("x-archive-size-hint", None)

        # TODO - what happens when an upload fails? we MUST NOT call _handle_uploaded_file on items that failed to upload
        _ia_upload(item, {destination: filepath}, metadata, headers)
        _handle_uploaded_file(path, filepath)

        uploaded_files[filepath] = destination

        # if no files to upload, check disk for more files
        if not files_to_upload:
            logging.log(logging.INFO, f"Checking if any additional files have been added...")
            files_to_upload = get_files_to_upload(path, item, uploaded_files)
            logging.log(logging.INFO, f"Found {len(files_to_upload)} additional files to upload.")
            if not files_to_upload:
                break

    logging.log(logging.INFO, f"Uploaded files: {json.dumps(uploaded_files, indent=4)}")


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
        logging.log(logging.INFO, f"Updating metadata for {identifier}...")
        logging.log(logging.INFO, f"Metadata changes: {json.dumps(metadata_changes, indent=4)}")
        item.modify_metadata(metadata_changes)

    if settings.derive:
        # TODO: check that there is no queued/running derive task already
        logging.log(logging.INFO, f"Deriving {identifier}...")
        tasks = ia.get_tasks(identifier, {"cmd":"derive.php", "history":"0"}, archive_session=cache.session)
        if tasks:
            logging.log(logging.INFO, f"-> Found a derive task ({tasks.pop().task_id}) already running for {identifier} - see https://archive.org/history/{identifier}")
        else:
            ""
            item.derive()

    # Now that we are done, the item meta and uploader meta should be cleaned up with the uploaded files
    _handle_uploaded_file(path, "__ia_meta.json")
    _handle_uploaded_file(path, "__uploader_meta.json")

    if os.path.exists(path) and os.path.isdir(path) and any(Path(path).iterdir()):
        # Clean up empty leftover directory
        os.rmdir(path)

    logging.log(logging.INFO, f"Success! Upload complete - {identifier} is now available at https://archive.org/details/{identifier}")
    return True


def get_size(files: dict[str, str]) -> int:
    size = 0
    for file in files:
        size += os.path.getsize(file)
    return size

def get_files_to_upload(path: str, item: ia.Item, uploaded_files: dict[str, str] | None = None) -> dict[str, str]:
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link
            if os.path.islink(fp):
                continue

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
        # skip if already uploaded
        if uploaded_files and path in uploaded_files:
            files.pop(path, None)
            continue

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

            if settings.set_scanner:
                if "scanner" not in metadata or not metadata["scanner"]:
                    metadata["scanner"] = []
                if isinstance(metadata["scanner"], str):
                    metadata["scanner"] = [metadata["scanner"]]
                metadata["scanner"].append(f"dartboard (v{version})")
    except FileNotFoundError:
        pass

    try:
        with open(os.path.join(path, "__uploader_meta.json"), "r") as meta_file:
            settings_raw = meta_file.read()
            if settings_raw:
                settings: UploaderMeta =  UploaderMeta.from_json(settings_raw)
    except FileNotFoundError:
        pass



    return metadata, settings

def wait_for_item(identifier: str) -> bool:
    # "borrowed" and modified from the wikiteam3 uploader
    item = ia.get_item(identifier)
    tries = 400
    for tries_left in range(tries, 0, -1):
        if item.exists:
            return True

        logging.log(logging.INFO, msg=f"Waiting for the item to be created... ({tries_left} tries left)  ...")
        if tries < 395:
            logging.log(logging.INFO, msg=f"Is IA overloaded? Still waiting for item to be created ({tries_left} tries left)  ...")
        time.sleep(30)
        item = ia.get_item(identifier)

    if not item.exists:
        logging.log(logging.ERROR, msg=f"IA overloaded, the item is still not ready after {400 * 30} seconds")
    return False