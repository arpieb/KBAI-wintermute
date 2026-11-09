"""Built-in ontology packs. Settings.ontologies (WINTERMUTE_ONTOLOGIES) picks which ones load."""

from __future__ import annotations

from collections.abc import Iterable

from wintermute.kb.ontology import SCHEMA_IRIS, OntologyPack

SCHEMA_ORG_VERSION = "30.1"

PACKS: dict[str, OntologyPack] = {
    "schema": OntologyPack(
        name="schema",
        prefixes={"schema": list(SCHEMA_IRIS)},
        source=f"https://schema.org/version/{SCHEMA_ORG_VERSION}/schemaorg-current-https.jsonld",
        format="json-ld",
        version=SCHEMA_ORG_VERSION,
    ),
    # Wintermute's own extensions to Schema.org (wintermute/data/ontologies/wintermute.ttl).
    "wm": OntologyPack(
        name="wm",
        prefixes={"wm": ["urn:wintermute:"]},
        source="wintermute.ttl",
        requires=("schema",),
    ),
}

DEFAULT_PACKS = ("schema", "wm")


def packs_for(names: Iterable[str]) -> list[OntologyPack]:
    unknown = [n for n in names if n not in PACKS]
    if unknown:
        raise ValueError(f"Unknown ontology pack(s): {', '.join(unknown)} (available: {', '.join(PACKS)})")
    return [PACKS[n] for n in names]
