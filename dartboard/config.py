import dataclasses
import json
import os
import re

@dataclasses.dataclass
class Config:
    s3_key: str = ""
    s3_secret: str = ""

    dry_run: bool = False # Don't upload. Not compatible with daemon mode.
    delete_after_upload: bool = False # Delete files after upload

    # DAEMON MODE SETTINGS:
    staging_directory: str = "./dartboard-staging"
    failure_directory: str = "./dartboard-failed"
    done_directory: str = "./dartboard-done"

    start_delay: int = 5 # Allow some time for additional file changes to be made before trying to start the upload.
    max_retries: int = 3 # Maximum number of tries for each individual file to upload before moving the item to ./dartboard-failed

    scan_interval: int = 3600 # How often to check the disk and start jobs. This will pick up jobs that fail, etc.

config: Config = Config()

def load_config(config_path: str) -> None:
    config_dict = {}

    if os.path.exists(config_path):
        with open(config_path, "r") as config_file:
            config_dict = json.loads(config_file.read().replace("\n", ""))
    elif os.path.exists(os.path.expanduser("~/.config/internetarchive/ia.ini")):
        with open(os.path.expanduser("~/.config/internetarchive/ia.ini"), "r") as f:
            credentials = f.read()
            s3_key = re.search("access = ([^\n]+)", credentials)
            s3_secret = re.search("secret = ([^\n]+)", credentials)

            config_dict = {
                "s3_key": None if not s3_key else s3_key.group(1),
                "s3_secret": None if not s3_secret else s3_secret.group(1)
            }
    else:
        print(
            "Couldn't find credentials. Create a config.json, or run \"ia configure\" if you prefer to use the IA CLI")

    global config
    # Update the existing config object instead of rebinding the name.
    # This ensures modules that did `from dartboard.config import config`
    # see the updated values.
    for key, value in config_dict.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            # ignore unknown keys in the config file
            continue