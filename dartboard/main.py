import argparse
import json
import logging
import os.path
import re
import time

from watchdog.observers import Observer

from dartboard.__version__ import version
from dartboard.config import Config
from dartboard.upload import upload
from dartboard.watch import UploadEventHandler


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
    parser.add_argument("--daemon", action="store_true",
                        help="Run dartboard in daemon mode")
    args = parser.parse_args()

    if args.daemon and args.path:
        print("Can't use --daemon and a path at the same time!")
        exit(1)
    if args.path is None and not args.daemon:
        parser.print_help()
        exit(1)

    config_dict = {}

    if os.path.exists(args.config_path):
        with open(args.config_path, "r") as config_file:
            config_dict = json.loads(config_file.read().replace("\n", ""))
    elif os.path.exists(os.path.expanduser("~/.config/internetarchive/ia.ini")):
        with open(os.path.expanduser("~/.config/internetarchive/ia.ini"), "r") as f:
            credentials = f.read()
            config_dict = {
                "s3_key": re.search("access = ([^\n]+)", credentials).group(1),
                "s3_secret": re.search("secret = ([^\n]+)", credentials).group(1)
            }
    else:
        print("Couldn't find credentials. Create a config.json, or run \"ia configure\" if you prefer to use the IA CLI")

    config = Config(**config_dict)
    if args.dry_run:
        config.dry_run = True

    if args.path and not args.daemon:
        upload(config, args.path)
    if args.daemon:
        event_handler = UploadEventHandler(config)
        observer = Observer()
        exppath = os.path.abspath(config.staging_directory)
        print(exppath)
        observer.schedule(event_handler, exppath, recursive=True)
        observer.start()
        try:
            while True:
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()




if __name__ == "__main__":
    main()