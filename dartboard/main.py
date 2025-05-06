import argparse
import json
import logging

from dartboard.__version__ import version
from dartboard.config import Config
from dartboard.upload import upload

def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s | %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')
    logging.log(logging.INFO, "Dartboard is loading...")


    parser = argparse.ArgumentParser(description=f"dartboard (v{version})")
    parser.add_argument("--config-path", dest="config_path", type=str, default="config.json",
                        help="Path to the config file (default: ./config.json)")
    parser.add_argument("path", type=str, nargs="?", default=None,
                        help="Path to the directory to upload")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Run the upload process without actually uploading anything")
    args = parser.parse_args()

    if args.path is None:
        parser.print_help()
        exit(1)

    with open(args.config_path, "r") as config_file:
        config_dict = json.loads(config_file.read().replace("\n", ""))

    config = Config(**config_dict)
    if args.dry_run:
        config.dry_run = True

    upload(config, args.path)


if __name__ == "__main__":
    main()