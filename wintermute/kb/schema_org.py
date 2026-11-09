"""Schema.org type hierarchy, used to label knowledge nodes with their full ancestry."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

SCHEMA_ORG_JSONLD = "https://schema.org/version/latest/schemaorg-current-https.jsonld"

# Custom extensions not present in Schema.org.
EXTENSIONS = {
    "CourseSurvey": ["CreativeWork"],
}


class SchemaOrg:
    def __init__(self, cache_dir: Path, timeout: float = 10.0):
        self.parents: dict[str, list[str]] = {}
        self._load(cache_dir / "schemaorg-parents.json", timeout)
        for name, parents in EXTENSIONS.items():
            self.parents.setdefault(name, parents)

    def _load(self, cache_file: Path, timeout: float) -> None:
        if cache_file.exists():
            self.parents = json.loads(cache_file.read_text())
            return
        try:
            resp = requests.get(SCHEMA_ORG_JSONLD, timeout=timeout)
            resp.raise_for_status()
            graph = resp.json()["@graph"]
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("Could not load Schema.org hierarchy (%s); labels will be shallow", e)
            return

        for node in graph:
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "rdfs:Class" not in types:
                continue
            sub = node.get("rdfs:subClassOf", [])
            sub = sub if isinstance(sub, list) else [sub]
            name = node["@id"].removeprefix("schema:")
            self.parents[name] = [s["@id"].removeprefix("schema:") for s in sub if s["@id"].startswith("schema:")]

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(self.parents))

    def __contains__(self, name: str) -> bool:
        return name in self.parents

    def labels(self, name: str) -> list[str]:
        """Ancestry of a type, root first and including the type itself, e.g. ["Thing", "Place", "City"]."""
        out: list[str] = []
        seen: set[str] = set()

        def visit(n: str) -> None:
            if n in seen:
                return
            seen.add(n)
            for p in self.parents.get(n, []):
                visit(p)
            out.append(n)

        visit(name)
        if out[0] != "Thing":
            out = ["Thing"] + [n for n in out if n != "Thing"]
        return out

    def labels_for(self, names: list[str]) -> list[str]:
        merged: list[str] = []
        for name in names:
            for label in self.labels(name):
                if label not in merged:
                    merged.append(label)
        return merged or ["Thing"]
