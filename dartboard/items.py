import dataclasses

from dataclasses_json import LetterCase, dataclass_json, DataClassJsonMixin, config


@dataclasses.dataclass
class UploaderMeta(DataClassJsonMixin):
    # Set the configuration via class variable instead of @dataclass_json
    dataclass_json_config = config(letter_case=LetterCase.CAMEL)["dataclasses_json"]

    set_scanner: bool = True
    set_upload_state: bool = False
    send_size_hint: bool = False
    derive: bool = True