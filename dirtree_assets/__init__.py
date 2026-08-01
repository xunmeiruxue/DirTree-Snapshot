"""Load cached HTML report assets."""

import re
from functools import lru_cache
from importlib.resources import files

_PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


@lru_cache(maxsize=None)
def load_text(name: str) -> str:
    return files(__package__).joinpath(name).read_text(encoding="utf-8")


def render(name: str, **values: str) -> str:
    template = load_text(name)
    return _PLACEHOLDER.sub(
        lambda match: values.get(match.group(1), match.group(0)),
        template,
    )
