"""The attribute registry (docs/model.md, "Attributes"). The first migration seeds the same
rows; a test checks the two agree."""

from __future__ import annotations

from dataclasses import dataclass


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
    # Reserved: no values, so no claims, until we agree what it distinguishes that `network`
    # doesn't (natural vs manufactured gas, crude vs products). Migration 6 has the note.
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
