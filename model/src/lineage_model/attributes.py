"""The attribute registry (docs/model.md, "Attributes"). The migrations seed the same rows;
a test checks the two agree.

It is also the one set of rules for what a claim's value may be, shared with every client:
`contract()` writes it as JSON (`python -m lineage_model.attributes`), which Studio and its
Agent vendor and check their own writes against. Store enforces the same rules in the
database (`store.claim_fits_attribute`), so a client is never looser than store."""

from __future__ import annotations

import json
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
    units: tuple[str, ...] | None = None  # quantity only; None: any unit, or none
    min_value: float | None = None  # quantity only; the value must be above it


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
    # A bare number is refused: inches and millimetres both appear in the sources.
    Attribute("diameter", "quantity", "one", ("section",), ("gas", "oil", "refined", "water"),
              units=("in", "mm"), min_value=0),
    Attribute("voltage", "quantity", "one", ("section",), ("electric",), min_value=0),
    Attribute("gauge", "quantity", "one", ("section",), ("road",), min_value=0),
    Attribute("lanes", "quantity", "one", ("section",), ("road",), min_value=0),
    Attribute("station_kind", "enum", "one", ("station",), None,
              ("pump", "compressor", "substation", "terminal", "intake", "delivery")),
    Attribute("serves", "feature", "many", ("station",)),
    Attribute("flow_direction", "enum", "one", ("edge",), None, ("forward", "reverse", "both")),
]}


def contract() -> dict[str, Any]:
    """The registry as clients read it: one entry per attribute, keys in registry order."""
    return {
        "attributes": [
            {
                "key": a.key,
                "value_type": a.value_type,
                "cardinality": a.cardinality,
                "feature_kinds": list(a.feature_kinds),
                "networks": list(a.networks) if a.networks is not None else None,
                "enum_values": list(a.enum_values) if a.enum_values is not None else None,
                "units": list(a.units) if a.units is not None else None,
                "min_value": a.min_value,
            }
            for a in ATTRIBUTES.values()
        ]
    }


if __name__ == "__main__":
    print(json.dumps(contract(), indent=2))
