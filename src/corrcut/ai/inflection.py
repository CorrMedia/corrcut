"""English inflection helpers for swear-list surface forms."""

from __future__ import annotations


def inflected_forms(key: str, *, plural: bool) -> set[str]:
    """
    Generate common English surface forms for a lexicon key.

    Explicit ``forms`` in JSON still override or extend this set at load time.
    """
    base = key.strip().lower()
    if not base or not plural:
        return {base} if base else set()

    forms = {base}

    if base.endswith("y") and len(base) > 2 and base[-2] not in "aeiou":
        forms.add(base[:-1] + "ies")
    elif base.endswith(("s", "sh", "ch", "x", "z")):
        forms.add(base + "es")
    else:
        forms.add(base + "s")

    if base.endswith("man") and len(base) > 3:
        forms.add(base[:-3] + "men")

    return forms
