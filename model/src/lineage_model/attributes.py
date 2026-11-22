"""The attribute registry (docs/model.md, "Attributes"), and what a claim's value may be.
The first migration seeds the same rows; a test checks the two agree. Store checks every
claim against `claim_problem` before writing it (`lineage_store.writes.insert_claim`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Attribute:
    key: str
    value_type: str  # text | quantity | enum | geometry | feature
    cardinality: str  # one | many, at a single instant
    feature_kinds: tuple[str, ...]
    networks: tuple[str, ...] | None = None  # None: any
    enum_values: tuple[str, ...] | None = None


ATTRIBUTES: dict[str, Attribute] = {a.key: a for a in [
    Attribute("name", "text", "one", ("line", "section", "station")),
    Attribute("owner", "text", "many", ("line", "section", "station")),
    Attribute("operator", "text", "one", ("line", "section", "station")),
    # No allowed values yet, so every commodity claim is refused. Values come when we agree what
    # it adds to `network` (natural vs manufactured gas, crude vs products); see docs/model.md.
    Attribute("commodity", "enum", "one", ("line", "section"), None, ()),
    Attribute("status", "enum", "one", ("line", "section", "station", "node", "edge"), None,
              ("proposed", "permitted", "under_construction", "operational", "maintenance", "decommissioned")),
    Attribute("route", "geometry", "one", ("section",)),
    Attribute("location", "geometry", "one", ("station", "node")),
    Attribute("diameter", "quantity", "one", ("section",), ("gas", "oil", "refined", "water")),
    Attribute("voltage", "quantity", "one", ("section",), ("electric",)),
    Attribute("gauge", "quantity", "one", ("section",), ("road",)),
    Attribute("lanes", "quantity", "one", ("section",), ("road",)),
    Attribute("station_kind", "enum", "one", ("station",), None,
              ("pump", "compressor", "substation", "terminal", "intake", "delivery")),
    Attribute("serves", "feature", "many", ("station",)),
    Attribute("flow_direction", "enum", "one", ("edge",), None, ("forward", "reverse", "both")),
]}


_VALUE_FIELD = {"text": "value_text", "enum": "value_text", "quantity": "value_num",
                "geometry": "value_geom", "feature": "value_feature"}


def claim_problem(key: str, feature_kind: str, feature_network: str | None,
                  value: dict[str, Any]) -> str | None:
    """Why a claim of `key` with `value` cannot sit on a feature of this kind and network, in
    words for the caller, or None when it fits. `value` holds the claim's `value_*` fields.
    An attribute the registry does not hold is not judged here."""
    a = ATTRIBUTES.get(key)
    if a is None:
        return None
    if feature_kind not in a.feature_kinds:
        return f"{key} does not apply to a {feature_kind}"
    if a.networks is not None and feature_network not in a.networks:
        return f"{key} does not apply to the {feature_network} network"
    if value.get(_VALUE_FIELD[a.value_type]) is None:
        return f"{key} needs a {a.value_type} value"
    if a.value_type == "enum" and value["value_text"] not in (a.enum_values or ()):
        return f"{value['value_text']} is not a value of {key} ({', '.join(a.enum_values or ())})"
    return None
