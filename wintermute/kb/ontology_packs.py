"""Built-in ontology packs. Settings.ontologies (WINTERMUTE_ONTOLOGIES) picks which ones load."""

from __future__ import annotations

from collections.abc import Iterable

from wintermute.kb.ontology import SCHEMA_IRIS, OntologyPack

SCHEMA_ORG_VERSION = "30.1"

# DBpedia ontology snapshots are published to github.com/dbpedia/ontology-tracker; pin a commit.
# (The Databus/Archivo download hosts and mappings.dbpedia.org were unreachable as of 2026-11.)
DBPEDIA_ONTOLOGY_VERSION = "2026.07.22-010553"
DBPEDIA_ONTOLOGY_COMMIT = "cba9f43dfa7746098075ae220480cf1b413e6e2f"

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
    # DBpedia ontology (~790 classes). Aligned to Schema.org by its own owl:equivalentClass triples
    # (dbo:City = schema:City, dbo:Person = schema:Person, ...), so DBpedia results typed only with
    # dbo: classes still match "who"/"where" filters.
    "dbo": OntologyPack(
        name="dbo",
        prefixes={"dbo": ["http://dbpedia.org/ontology/"]},
        source=(
            f"https://raw.githubusercontent.com/dbpedia/ontology-tracker/{DBPEDIA_ONTOLOGY_COMMIT}"
            "/databus/dbpedia/ontology/dbo-snapshots/dbo-snapshots.ttl"
        ),
        format="turtle",
        version=DBPEDIA_ONTOLOGY_VERSION,
        requires=("schema",),
    ),
}

DEFAULT_PACKS = ("schema", "wm", "dbo")


def packs_for(names: Iterable[str]) -> list[OntologyPack]:
    unknown = [n for n in names if n not in PACKS]
    if unknown:
        raise ValueError(f"Unknown ontology pack(s): {', '.join(unknown)} (available: {', '.join(PACKS)})")
    return [PACKS[n] for n in names]
