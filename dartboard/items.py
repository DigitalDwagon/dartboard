import dataclasses

import dataclasses_json
from dataclasses_json import LetterCase, dataclass_json


@dataclasses.dataclass
@dataclass_json(letter_case=LetterCase.CAMEL)
class UploaderMeta:
    set_scanner : bool = True
    set_upload_state: bool = False
    send_size_hint: bool = False
    derive: bool = True