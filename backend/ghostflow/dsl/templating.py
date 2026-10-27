"""Minimal, non-evaluating templates: `{{inputs.<name>}}` is the only supported form."""

import re
from collections.abc import Mapping

_REFERENCE = re.compile(r"\{\{\s*inputs\.([a-z][a-z0-9_]{0,39})\s*\}\}")


class TemplateError(ValueError):
    pass


def template_references(value: str) -> set[str]:
    names = set(_REFERENCE.findall(value))
    leftover = _REFERENCE.sub("", value)
    if "{{" in leftover or "}}" in leftover:
        raise TemplateError("Only '{{inputs.<name>}}' templates are supported")
    return names


def render(value: str, inputs: Mapping[str, str]) -> str:
    template_references(value)
    return _REFERENCE.sub(lambda m: inputs[m.group(1)], value)
