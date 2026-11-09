"""Ontology packs: the built-in catalog, plus extra packs declared in a TOML manifest.

Which packs load is decided by, in order: WINTERMUTE_ONTOLOGIES, the manifest's `load` list, then
DEFAULT_PACKS. See docs/ontology-packs.md.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from wintermute.kb.ontology import SCHEMA_IRIS, OntologyPack

if TYPE_CHECKING:
    from wintermute.config import Settings

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

PACK_KEYS = {"prefixes", "source", "format", "version", "requires"}


class ManifestError(ValueError):
    pass


def load_manifest(path: Path) -> tuple[dict[str, OntologyPack], list[str] | None]:
    """Parse an ontology manifest. Returns its packs and its `load` list (None if it has none).

    Relative local sources are resolved against the manifest's directory.
    """
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ManifestError(f"Can't read ontology manifest {path}: {e}") from e

    unknown = set(data) - {"load", "packs"}
    if unknown:
        raise ManifestError(f"{path}: unknown top-level key(s) {', '.join(sorted(unknown))}")
    load = data.get("load")
    if load is not None and not (isinstance(load, list) and all(isinstance(n, str) for n in load)):
        raise ManifestError(f"{path}: `load` must be a list of pack names")

    packs: dict[str, OntologyPack] = {}
    for name, spec in data.get("packs", {}).items():
        where = f"{path}: [packs.{name}]"
        if name in PACKS:
            raise ManifestError(f"{where} redefines the built-in pack {name!r}")
        if not isinstance(spec, dict):
            raise ManifestError(f"{where} must be a table")
        bad = set(spec) - PACK_KEYS
        if bad:
            raise ManifestError(f"{where}: unknown key(s) {', '.join(sorted(bad))}")
        if "source" not in spec:
            raise ManifestError(f"{where} needs a `source`")
        source = str(spec["source"])
        if not source.startswith(("http://", "https://")) and not Path(source).expanduser().is_absolute():
            source = str((path.parent / source).resolve())
        prefixes = {p: [iris] if isinstance(iris, str) else list(iris) for p, iris in spec.get("prefixes", {}).items()}
        packs[name] = OntologyPack(
            name=name,
            prefixes=prefixes,
            source=source,
            format=spec.get("format"),
            version=str(spec.get("version", "")),
            requires=tuple(spec.get("requires", ())),
        )
    return packs, load


def packs_for(names: Iterable[str], extra: dict[str, OntologyPack] | None = None) -> list[OntologyPack]:
    available = {**PACKS, **(extra or {})}
    names = list(names)
    unknown = [n for n in names if n not in available]
    if unknown:
        raise ValueError(f"Unknown ontology pack(s): {', '.join(unknown)} (available: {', '.join(available)})")
    return [available[n] for n in names]


def select_packs(settings: Settings) -> list[OntologyPack]:
    """The packs an agent should load, from its settings and (optional) manifest."""
    extra: dict[str, OntologyPack] = {}
    load = None
    if settings.ontology_manifest:
        extra, load = load_manifest(settings.ontology_manifest)
    names = settings.ontologies or load or list(DEFAULT_PACKS)
    return packs_for(names, extra)
