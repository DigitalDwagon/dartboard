import dataclasses

@dataclasses.dataclass
class Config:
    s3_key: str = ""
    s3_secret: str = ""

    staging_directory: str = "dartboard-staging"
    working_directory: str = "dartboard-uploading"
    done_directory: str = "dartboard-done"

    dry_run: bool = False
