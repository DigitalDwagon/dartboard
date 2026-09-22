import argparse
import json
import logging
import os.path
import re
import time
import internetarchive as ia

from watchdog.observers import Observer

from dartboard import cache
import dartboard
from dartboard.__version__ import version
from dartboard.config import Config, config, load_config
from dartboard.upload import upload
from dartboard.watch import UploadEventHandler

def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s | %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')
    logging.info("Dartboard is loading...")


    parser = argparse.ArgumentParser(description=f"dartboard (v{version})")
    _ = parser.add_argument("--config-path", dest="config_path", type=str, default="config.json",
                        help="Path to the config file (default: ./config.json)")
    _ = parser.add_argument("path", type=str, nargs="?", default=None,
                        help="Path to the directory to upload")
    _ = parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Run the upload process without actually uploading anything")
    _ = parser.add_argument("--daemon", action="store_true",
                        help="Run dartboard in daemon mode")
    args = parser.parse_args()

    if args.daemon and args.path:
        print("Can't use --daemon and a path at the same time!")
        exit(1)
    if args.path is None and not args.daemon:
        parser.print_help()
        exit(1)
    if args.dry_run and args.daemon:
        print("Can't use --daemon and --dry-run at the same time!")
        exit(1)

    load_config(args.config_path)

    if args.dry_run:
        config.dry_run = True

    cache.session = ia.get_session({"s3": {"access": config.s3_key, "secret": config.s3_secret}})

    if args.path and not args.daemon:
        upload(args.path)
    if args.daemon:
        event_handler = UploadEventHandler()
        observer = Observer()
        exppath = os.path.abspath(config.staging_directory)
        print(exppath)
        _ = observer.schedule(event_handler, exppath, recursive=True)
        observer.start()
        try:
            while True:
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()




if __name__ == "__main__":
    main()