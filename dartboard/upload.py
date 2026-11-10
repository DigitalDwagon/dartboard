import hashlib
import json
import logging
import os
import re
import shutil
from marshal import version
from pathlib import Path

import requests

import dartboard
from dartboard import cache
from dartboard.config import config
from dartboard.items import UploaderMeta


class DartboardItem:
    def _detect_identifier(self) -> str:
        """
        :return: the identifier of the item, raises ValueError if it is not a valid Internet Archive identifier
        """
        identifier: str = self.path.name
        if not identifier:
            raise ValueError(f"{self.path} does not have an identifier")

        # identifier validity: https://archive.org/developers/metadata-schema/index.html#archive-org-identifiers
        if not re.match(r"^[a-zA-Z0-9-_.]{5,100}$", identifier):
            raise ValueError(f"{identifier} is not a valid identifier. Identifiers should be 5-100 characters long and can only contain letters, numbers, dashes, underscores, and periods.")

        return identifier

    def __init__(self, *, path: Path):
        if not path.is_dir():
            raise ValueError(f"Provided item path ({path}) is not a directory.")

        self.path: Path = path
        self.identifier: str = self._detect_identifier()

        self.item_exists: bool = False
        if not config.fast:
            self.item_exists = cache.get_item(self.identifier).exists

    def _move_file(self, file: Path, parent: Path):
        """
        Move a file to a given location, preserving item directory structure. For example, if file is
        "dartboard-staging/IDENTIFIER/a/b/c.txt" and parent is "dartboard-done/", the file would move to
        "dartboard-done/IDENTIFIER/a/b/c.txt"
        :param file: The file to move
        :param parent: The new parent directory of the file
        :return:
        """
        rel_path = file.relative_to(self.path)
        destination = parent / rel_path

        if config.dry_run:
            logging.info(f"Dry run: Skipped moving {file} to {destination}")
            return

        logging.info(f"Moving {str(file)} to {str(destination)}")
        _ = shutil.move(str(file), str(destination))

    def _fail_file(self, file: Path):
        """
        Marks a file as failed. In daemon mode, this moves it to the failed files directory. Otherwise, the upload is
        stopped.
        :param file: The file being failed
        """
        if not config.daemon_mode:
            raise RuntimeError(f"A file was failed: {file}")

        logging.error(f"A file was failed: {file}")
        self._move_file(file=file, parent=Path(config.failure_directory))

    def _finish_file(self, file: Path):
        if config.dry_run:
            logging.info(f"Dry run: Not cleaning up {file}")
            return
        if config.delete_after_upload:
            logging.info(f"Deleting finished file {str(file)}")
            file.unlink()
            return
        if config.daemon_mode:
            self._move_file(file, Path(config.done_directory))

    def _get_files_to_upload(self) -> dict[str, str]:
        """
        :return: a dict [absolute path on disk: relative path inside Internet Archive item] of files to be uploaded
        """
        files: dict[str, str] = {}
        for dirpath, _, filenames in os.walk(self.path):
            for f in filenames:
                fp = Path(dirpath) / f
                # skip if it is symbolic link
                if fp.is_symlink():
                    continue

                # get the relative path
                rel_path = fp.relative_to(self.path).as_posix()
                abs_path = str(fp.resolve())
                files[abs_path] = rel_path

        # remove __ia_meta.json and __uploader_meta.json from the list
        for filepath, destination in list(files.items()):
            if destination in ("__ia_meta.json", "__uploader_meta.json"):
                _ = files.pop(filepath, None)

        if self.item_exists:
            return self._remove_existing_ia_files(files)

        return files

    def _remove_existing_ia_files(self, files: dict[str, str]):
        """
        Take a dict of files to upload, and remove any files already existing in the file with matching hashes
        :param files: The files to check against the IA item
        :return:
        """
        for filepath, destination in list(files.items()):
            ia_file = next((f for f in cache.get_item(self.identifier).files if f.get("name") == destination), None)

            if ia_file:
                md5_remote = ia_file.get("md5")
                if not md5_remote:
                    logging.error(
                        f"{destination} already exists in {self.identifier}. No md5 to compare, failing file...")
                    self._fail_file(Path(filepath))
                    _ = files.pop(filepath, None)
                    continue

                try:
                    with open(filepath, "rb") as f:
                        md5_local = hashlib.file_digest(f, "md5").hexdigest()
                except Exception:
                    logging.error(f"Failed to read local file {filepath} for hashing", exc_info=True)
                    raise

                if md5_remote == md5_local:
                    logging.info(f"{destination} already exists in {self.identifier}. Skipping...")
                    # TODO the file should be treated like an uploaded file (since it was!)
                    _ = files.pop(filepath, None)
                    continue
                else:
                    logging.error(
                        f"{destination} already exists in {self.identifier}, but the hashes don't match. Failing file...")
                    logging.error(f"local file: {md5_local}")
                    logging.error(f"remote file: {md5_remote}")
                    self._fail_file(Path(filepath))
                    _ = files.pop(filepath, None)
                    continue

        return files

    def _load_meta_info(self) -> tuple[dict[str, str | list[str]], UploaderMeta]:
        metadata = {}
        settings: UploaderMeta | None = None

        try:
            meta_path = self.path / "__uploader_meta.json"
            if meta_path.exists():
                with meta_path.open("r") as meta_file:
                    settings_raw = meta_file.read()
                    if settings_raw:
                        settings = UploaderMeta.from_json(settings_raw)
        except FileNotFoundError:
            pass

        try:
            with open(self.path / "__ia_meta.json", "r") as meta_file:
                raw_metadata = meta_file.read()
                if raw_metadata:
                    metadata = json.loads(raw_metadata)
        except FileNotFoundError:
            pass

        if not settings:
            settings = UploaderMeta()

        # make metadata changes that depend on settings
        if settings.set_scanner:
            if "scanner" not in metadata or not metadata["scanner"]:
                metadata["scanner"] = []
            if isinstance(metadata["scanner"], str):
                metadata["scanner"] = [metadata["scanner"]]
            metadata["scanner"].append(f"dartboard (v{version})")

        if settings.set_upload_state:
            metadata["upload_state"] = "uploading"

        return metadata, settings

    def _calculate_size_hint(self, files: dict[str, str]) -> int:
        """
        Calculate the size of the files in the IA item
        :param files: The files to calculate the size of
        :return:
        """
        total: int = 0
        for local_path, remote_path in list(files.items()):
            total += os.path.getsize(local_path)
        return total

    def _cleanup_empty_directories(self):
        """
        Cleans up any empty directories left behind on the disk, including the item path itself if empty.
        """
        for dirpath, dirnames, _ in os.walk(self.path, topdown=False):
            directories = [Path(dirpath) / name for name in dirnames]
            directories.append(Path(dirpath))

            for directory in directories:
                if directory.is_symlink() or not directory.is_dir():
                    continue
                if any(directory.iterdir()):
                    continue

                if config.dry_run:
                    logging.info(f"Dry run: Skipped removing empty directory {directory}")
                    continue

                logging.info(f"Cleanup: Removing empty directory {directory}")
                directory.rmdir()


    def _get_metadata_changes(self, metadata: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
        item = cache.get_item(self.identifier) # no cache=False here since waiting for the item will get a fresh one into the cache already
        item_metadata = item.metadata
        metadata_changes: dict[str, str | list[str]] = {}

        # diff the metadata
        # keys that are in metadata but not in item_metadata or keys that are present in both, but have different values
        # ignore keys that are only in item_metadata
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
                    metadata_changes[key] = value  # pyright: ignore[reportArgumentType] TODO
                continue

            if item_metadata[key] != metadata[key]:
                metadata_changes[key] = metadata[key]
        return metadata_changes

    def upload(self):
        logging.info(f"Uploading {self.path}...")
        logging.info(f"Identifier: {self.identifier} - this item will try to upload to https://archive.org/details/{self.identifier}")

        metadata, settings = self._load_meta_info()
        if not self.item_exists:
            if not metadata:
                raise RuntimeError(f"No metadata found for {self.identifier} and the item doesn't already exist. Can't upload.")
            if not "mediatype" in metadata or not "collection" in metadata:
                raise RuntimeError(f"{self.identifier} doesn't exist, and mediatype or collection metadata is missing. Can't upload.")
        logging.info(f"Metadata: {json.dumps(metadata, indent=4)}") # TODO is this worth keeping?

        files_to_upload = self._get_files_to_upload()
        headers: dict[str, str | list[str]] = {}

        if settings.send_size_hint and not self.item_exists:
            headers["x-archive-size-hint"] = str(self._calculate_size_hint(files_to_upload))

        item = cache.get_item(self.identifier)
        for index, (local_path, remote_path) in enumerate(list(files_to_upload.items())):
            logging.info(f"Uploading {local_path}...")

            if config.dry_run:
                logging.info(f"Dry run, not uploading {local_path}...")
                continue

            try:
                result = item.upload(
                    files={remote_path: local_path},
                    metadata=metadata,
                    headers=headers,
                    queue_derive=settings.derive and index == len(files_to_upload) - 1,
                )

                for resp in result:
                    if not isinstance(resp, requests.Response):
                        continue
                    resp.raise_for_status() # TODO is this necessary, or will the library raise for us?
            except Exception as e:
                logging.exception(f"Failed to upload {local_path}: {e}")
                raise e

            self._finish_file(Path(local_path))

        # Yay, upload complete! Post upload tasks...
        if not config.fast:
            # Fast uploads assume there are no after the fact metadata changes, etc.
            if settings.set_upload_state:
                metadata["upload_state"] = "uploaded"

            item = cache.wait_for_item(self.identifier)
            metadata_changes = self._get_metadata_changes(metadata)
            if metadata_changes:
                logging.info(f"Updating metadata for {self.identifier}...")
                logging.info(f"Metadata changes: {json.dumps(metadata_changes, indent=4)}")
                _ = item.modify_metadata(metadata_changes) # TODO will the library raise for status for us?


        if (self.path / "__ia_meta.json").exists():
            self._finish_file(self.path / "__ia_meta.json")
        if (self.path / "__uploader_meta.json").exists():
            self._finish_file(self.path / "__uploader_meta.json")
        self._cleanup_empty_directories()

        logging.info(f"Success! Upload complete - {self.identifier} is now available at https://archive.org/details/{self.identifier}")