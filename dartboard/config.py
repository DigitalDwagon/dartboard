import dataclasses
import json
import os
import re

@dataclasses.dataclass
class Config:
    s3_key: str = ""
    s3_secret: str = ""

    staging_directory: str = "./dartboard-staging"
    working_directory: str = "./dartboard-uploading"
    done_directory: str = "./dartboard-done"

    dry_run: bool = False
    delete_after_upload: bool = False

    start_delay: int = 5 # In daemon mode, allow some time for additional file changes to be made before trying to start the upload.
    max_retries: int = 3


config: Config = Config()

def load_config(config_path: str) -> None:
    config_dict = {}

    if os.path.exists(config_path):
        with open(config_path, "r") as config_file:
            config_dict = json.loads(config_file.read().replace("\n", ""))
    elif os.path.exists(os.path.expanduser("~/.config/internetarchive/ia.ini")):
        with open(os.path.expanduser("~/.config/internetarchive/ia.ini"), "r") as f:
            credentials = f.read()
            config_dict = {
                "s3_key": re.search("access = ([^\n]+)", credentials).group(1),
                "s3_secret": re.search("secret = ([^\n]+)", credentials).group(1)
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