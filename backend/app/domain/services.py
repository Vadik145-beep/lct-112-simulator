"""Which services are notified for an incident type under a set of flags.

``service_rules`` of an incident type is a list of rules built from the classifier columns
(see ``app.importers.classifier``)::

    {"service": "103", "when": ["injured"], "notify": true,  "value": "Травма"}
    {"service": "103", "when": ["not_on_site"], "notify": false, "value": "нет реагирования"}

A rule applies when every flag in ``when`` is chosen (an empty ``when`` always applies).
The result is the union of services from applying positive rules minus services that an
applying negative rule («нет реагирования») removes. Pure function, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def resolve_services(rules: Iterable[Mapping], flags: Iterable[str] = ()) -> list[str]:
    chosen = set(flags)
    notified: dict[str, None] = {}
    removed: set[str] = set()
    for rule in rules:
        if not set(rule.get("when", ())).issubset(chosen):
            continue
        service = rule["service"]
        if rule.get("notify", True):
            notified.setdefault(service)
        else:
            removed.add(service)
    return [service for service in notified if service not in removed]


def available_flags(rules: Iterable[Mapping]) -> list[str]:
    """Flags that change the notification list of this type (offered on the survey card)."""
    flags: dict[str, None] = {}
    for rule in rules:
        for flag in rule.get("when", ()):
            flags.setdefault(flag)
    return list(flags)
