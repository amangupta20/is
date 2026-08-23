"""Classification of transient Open WebUI pasted-text files."""

import re

# Open WebUI converts large text pastes into files named Pasted_Text_<timestamp>.txt.
_PASTED_TEXT_PATTERN = re.compile(r"^pasted_text_.+\.txt$", re.IGNORECASE)


def is_transient_pasted_file(filename: str) -> bool:
    """Return whether a native filename is an auto-generated large-text paste."""
    return bool(_PASTED_TEXT_PATTERN.match((filename or "").strip()))
