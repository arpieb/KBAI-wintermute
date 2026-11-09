"""Namespaced ontology registry: class and property definitions from RDFS/OWL ontology packs.

Types are named by CURIE ("schema:Course", "wm:CourseSurvey"). Each pack is an RDF source plus the
prefixes it defines; packs are parsed with rdflib on first use and cached as JSON (full IRIs, so a
cache stays valid whatever other packs are loaded alongside it). Schema.org is the base: classes
with no known parent hang off schema:Thing, so every node can be found by a "what is X" query.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

import requests

log = logging.getLogger(__name__)

ONTOLOGY_DATA = Path(__file__).resolve().parent.parent / "data" / "ontologies"
CACHE_FORMAT = 1

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
SCHEMA_IRIS = ("https://schema.org/", "http://schema.org/")

CLASS_TYPES = {RDFS + "Class", OWL + "Class"}
PROPERTY_TYPES = {RDF + "Property", OWL + "ObjectProperty", OWL + "DatatypeProperty", OWL + "AnnotationProperty"}
DOMAIN_PREDICATES = {RDFS + "domain"} | {iri + "domainIncludes" for iri in SCHEMA_IRIS}
RANGE_PREDICATES = {RDFS + "range"} | {iri + "rangeIncludes" for iri in SCHEMA_IRIS}
DATATYPE_MARKERS = {iri + "DataType" for iri in SCHEMA_IRIS}
# Generic roots of other vocabularies, folded into the registry root.
ROOT_ALIASES = {OWL + "Thing", RDFS + "Resource"}

DEFAULT_PREFIX = "schema"
ROOT = "schema:Thing"

# Used when the Schema.org pack can't be loaded, so question-word filters ("who" -> Person) still work.
STUB_CLASSES = ["Thing", "Person", "Place", "Organization", "Event", "CreativeWork"]


@dataclass(frozen=True)
class OntologyPack:
    name: str
    # prefix -> [canonical namespace IRI, *alternate IRIs]
    prefixes: dict[str, list[str]]
    # URL, or a path relative to wintermute/data/ontologies/ (or absolute).
    source: str
    # rdflib parser format; guessed from the source's extension if None.
    format: str | None = None
    # Part of the cache key; pin remote sources so stored labels don't drift.
    version: str = ""
    # Packs this one aligns to; they're loaded first.
    requires: tuple[str, ...] = ()

    @property
    def cache_name(self) -> str:
        digest = hashlib.sha1(f"{CACHE_FORMAT}|{self.source}|{self.version}".encode()).hexdigest()[:12]
        return f"{self.name}-{digest}.json"


@dataclass(frozen=True)
class OntClass:
    curie: str
    parents: tuple[str, ...] = ()
    equivalents: tuple[str, ...] = ()
    label: str = ""
    comment: str = ""
    pack: str = ""
    datatype: bool = False


@dataclass(frozen=True)
class OntProperty:
    curie: str
    domains: tuple[str, ...] = ()
    ranges: tuple[str, ...] = ()
    parents: tuple[str, ...] = ()
    label: str = ""
    comment: str = ""
    pack: str = ""


@dataclass
class _RawPack:
    """A pack's definitions as extracted from RDF, keyed by full IRI."""

    classes: dict[str, dict] = field(default_factory=dict)
    properties: dict[str, dict] = field(default_factory=dict)


class OntologyRegistry:
    ROOT = ROOT

    def __init__(self, packs: Iterable[OntologyPack], cache_dir: Path | None = None, timeout: float = 10.0):
        self.packs: list[OntologyPack] = []
        self.prefixes: dict[str, list[str]] = {}
        self.classes: dict[str, OntClass] = {}
        self.properties: dict[str, OntProperty] = {}
        self._ancestors: dict[str, list[str]] = {}
        self._stubbed: set[str] = set()

        packs = _dependency_order(list(packs))
        for pack in packs:
            self._bind(pack)
        # Longest namespace first, so nested namespaces compact to the most specific prefix.
        self._namespaces = sorted(
            ((iri, prefix) for prefix, iris in self.prefixes.items() for iri in iris), key=lambda x: -len(x[0])
        )

        raw: list[tuple[OntologyPack, _RawPack]] = []
        for pack in packs:
            loaded = _load_pack(pack, cache_dir, timeout)
            if loaded is None:
                log.warning("Ontology pack %r unavailable; its types will have shallow labels", pack.name)
                if pack.name == DEFAULT_PREFIX:
                    loaded = _stub_schema_pack()
                    self._stubbed.add(pack.name)
                else:
                    continue
            self.packs.append(pack)
            raw.append((pack, loaded))
        self._build(raw)

    @property
    def fingerprint(self) -> str:
        """Identifies the loaded packs and versions; stored labels are valid for one fingerprint."""
        def tag(p: OntologyPack) -> str:
            return "stub" if p.name in self._stubbed else p.cache_name.rsplit("-", 1)[-1].removesuffix(".json")

        return ",".join(f"{p.name}@{tag(p)}" for p in self.packs)

    # --- Names -----------------------------------------------------------------------------

    def _bind(self, pack: OntologyPack) -> None:
        for prefix, iris in pack.prefixes.items():
            existing = self.prefixes.get(prefix)
            if existing is not None and existing[0] != iris[0]:
                raise ValueError(f"Prefix {prefix!r} is bound to {existing[0]}, pack {pack.name!r} wants {iris[0]}")
            self.prefixes[prefix] = list(dict.fromkeys((existing or []) + list(iris)))

    def expand(self, curie: str) -> str:
        prefix, _, local = curie.partition(":")
        if prefix not in self.prefixes:
            raise KeyError(f"Unknown prefix {prefix!r}")
        return self.prefixes[prefix][0] + local

    def compact(self, iri: str) -> str | None:
        """CURIE for a full IRI, or None if no loaded prefix covers it."""
        if iri in ROOT_ALIASES:
            return ROOT
        for ns, prefix in self._namespaces:
            if iri.startswith(ns) and len(iri) > len(ns):
                return f"{prefix}:{iri[len(ns):]}"
        return None

    def curie(self, name: str) -> str | None:
        """Normalize a bare name, CURIE or IRI to CURIE form, without requiring the type to be known."""
        if "://" in name or name.startswith("urn:"):
            return self.compact(name)
        if ":" in name:
            return name if name.partition(":")[0] in self.prefixes else None
        return f"{DEFAULT_PREFIX}:{name}"

    def resolve(self, name: str) -> str | None:
        """CURIE of a known class or property, from a bare name (default namespace), CURIE or IRI."""
        curie = self.curie(name)
        return curie if curie in self else None

    def __contains__(self, name: object) -> bool:
        return name in self.classes or name in self.properties

    # --- Classes ---------------------------------------------------------------------------

    def ancestors(self, name: str) -> list[str]:
        """Root-first ancestry of a class, including itself (last) and any equivalent classes.

        Unknown classes get [ROOT, itself], so labels survive a pack that failed to load.
        """
        curie = self.curie(name) or name
        if curie not in self._ancestors:
            if curie not in self.classes:
                return [ROOT] if curie == ROOT else [ROOT, curie]
            out: list[str] = []
            seen: set[str] = set()

            def visit(n: str) -> None:
                if n in seen:
                    return
                group = self._groups.get(n, (n,))
                seen.update(group)
                for member in group:
                    for parent in self.classes[member].parents if member in self.classes else ():
                        visit(parent)
                out.extend(m for m in group if m != n)
                out.append(n)

            visit(curie)
            out = [ROOT] + [n for n in out if n != ROOT]
            self._ancestors[curie] = out
        return list(self._ancestors[curie])

    def labels_for(self, names: Iterable[str]) -> list[str]:
        """Merged ancestry of several types, skipping datatypes.

        Types are merged shallowest first (ties keep their order), so the most specific type ends up
        last, which is what Thing IDs and Thing.from_record key on. Remote sources list types in no
        particular order (DBpedia gives Alan Turing Animal, Person, Scientist, Species, ...).
        """
        chains = sorted((self.ancestors(name) for name in names), key=len)
        merged: list[str] = []
        for chain in chains:
            for label in chain:
                if label not in merged and not self.is_datatype(label):
                    merged.append(label)
        return merged or [ROOT]

    def is_a(self, name: str, ancestor: str) -> bool:
        return (self.curie(ancestor) or ancestor) in self.ancestors(name)

    def is_datatype(self, curie: str) -> bool:
        cls = self.classes.get(curie)
        return bool(cls and cls.datatype)

    # --- Properties ------------------------------------------------------------------------

    def properties_of(self, name: str) -> list[OntProperty]:
        """Properties whose domain includes the class or any of its ancestors."""
        lineage = set(self.ancestors(name))
        return [p for p in self.properties.values() if lineage.intersection(p.domains)]

    # --- Building --------------------------------------------------------------------------

    def _known(self, iris: Iterable[str], known: set[str]) -> tuple[str, ...]:
        out = []
        for iri in iris:
            curie = self.compact(iri)
            if curie in known and curie not in out:
                out.append(curie)
        return tuple(out)

    def _build(self, raw: list[tuple[OntologyPack, _RawPack]]) -> None:
        class_iris: dict[str, tuple[str, dict]] = {}
        prop_iris: dict[str, tuple[str, dict]] = {}
        for pack, data in raw:
            for iri, d in data.classes.items():
                curie = self.compact(iri)
                if curie and curie not in class_iris:
                    class_iris[curie] = (pack.name, d)
            for iri, d in data.properties.items():
                curie = self.compact(iri)
                if curie and curie not in prop_iris:
                    prop_iris[curie] = (pack.name, d)

        known = set(class_iris)
        # Equivalence groups (union-find over owl:equivalentClass, both directions).
        parent: dict[str, str] = {c: c for c in known}

        def find(c: str) -> str:
            while parent[c] != c:
                parent[c] = parent[parent[c]]
                c = parent[c]
            return c

        for curie, (_, d) in class_iris.items():
            for other in self._known(d.get("equivalents", ()), known):
                parent[find(other)] = find(curie)
        groups: dict[str, list[str]] = {}
        for c in sorted(known):
            groups.setdefault(find(c), []).append(c)
        self._groups = {c: tuple(g) for g in groups.values() if len(g) > 1 for c in g}

        datatypes = {c for c, (_, d) in class_iris.items() if d.get("datatype")}
        for curie, (pack, d) in class_iris.items():
            parents = tuple(p for p in self._known(d.get("parents", ()), known) if p != curie)
            # Rootless classes (including other vocabularies' top classes) hang off the registry root.
            if not parents and curie != ROOT and ROOT in known:
                parents = (ROOT,)
            equivalents = tuple(m for m in self._groups.get(curie, ()) if m != curie)
            self.classes[curie] = OntClass(curie, parents, equivalents, d.get("label", ""), d.get("comment", ""), pack)

        # Datatypes: marked directly, or subclasses of one (schema:URL -> schema:Text).
        for curie in known:
            if datatypes.intersection(self.ancestors(curie)):
                self.classes[curie] = replace(self.classes[curie], datatype=True)
        self._ancestors.clear()

        known_props = set(prop_iris)
        for curie, (pack, d) in prop_iris.items():
            self.properties[curie] = OntProperty(
                curie,
                self._known(d.get("domains", ()), known),
                self._known(d.get("ranges", ()), known),
                tuple(p for p in self._known(d.get("parents", ()), known_props) if p != curie),
                d.get("label", ""),
                d.get("comment", ""),
                pack,
            )


def _dependency_order(packs: list[OntologyPack]) -> list[OntologyPack]:
    by_name = {p.name: p for p in packs}
    ordered: list[OntologyPack] = []
    visiting: set[str] = set()

    def visit(pack: OntologyPack) -> None:
        if pack in ordered:
            return
        if pack.name in visiting:
            raise ValueError(f"Ontology pack dependency cycle at {pack.name!r}")
        visiting.add(pack.name)
        for dep in pack.requires:
            if dep not in by_name:
                raise ValueError(f"Ontology pack {pack.name!r} requires {dep!r}, which isn't selected")
            visit(by_name[dep])
        visiting.discard(pack.name)
        ordered.append(pack)

    for pack in packs:
        visit(pack)
    return ordered


# --- Loading -------------------------------------------------------------------------------


def _load_pack(pack: OntologyPack, cache_dir: Path | None, timeout: float) -> _RawPack | None:
    cache_file = cache_dir / "ontologies" / pack.cache_name if cache_dir else None
    if cache_file and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            return _RawPack(data["classes"], data["properties"])
        except (ValueError, KeyError) as e:
            log.warning("Ignoring unreadable ontology cache %s (%s)", cache_file, e)

    try:
        text = _read_source(pack.source, timeout)
        loaded = _extract(text, pack)
    except Exception as e:  # rdflib raises a variety of parser errors
        log.warning("Could not load ontology pack %r from %s (%s)", pack.name, pack.source, e)
        return None

    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"classes": loaded.classes, "properties": loaded.properties}))
    return loaded


def _read_source(source: str, timeout: float) -> str:
    if source.startswith(("http://", "https://")):
        resp = requests.get(source, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    path = Path(source)
    return (path if path.is_absolute() else ONTOLOGY_DATA / path).read_text()


def _extract(text: str, pack: OntologyPack) -> _RawPack:
    """Pull class and property definitions out of an RDF document (rdflib is only needed here)."""
    from rdflib import Graph, Literal, URIRef
    from rdflib.util import guess_format

    g = Graph()
    g.parse(data=text, format=pack.format or guess_format(pack.source) or "turtle")

    def objs(s, preds) -> list[str]:
        return [str(o) for p in preds for o in g.objects(s, URIRef(p)) if isinstance(o, URIRef)]

    def text_of(s, pred: str) -> str:
        values = [o for o in g.objects(s, URIRef(pred)) if isinstance(o, Literal)]
        english = [v for v in values if v.language in (None, "en")]
        return str((english or values or [""])[0])

    namespaces = [iri for iris in pack.prefixes.values() for iri in iris]

    def ours(s) -> bool:
        return isinstance(s, URIRef) and any(str(s).startswith(ns) for ns in namespaces)

    raw = _RawPack()
    for type_iri in CLASS_TYPES:
        for s in g.subjects(URIRef(RDF + "type"), URIRef(type_iri)):
            if not ours(s) or str(s) in raw.classes:
                continue
            raw.classes[str(s)] = {
                "parents": objs(s, [RDFS + "subClassOf"]),
                "equivalents": objs(s, [OWL + "equivalentClass"]),
                "label": text_of(s, RDFS + "label"),
                "comment": text_of(s, RDFS + "comment"),
                "datatype": bool(DATATYPE_MARKERS.intersection(objs(s, [RDF + "type"]))),
            }
    # Equivalences are often asserted from the other side; record them on our classes too.
    for s, o in g.subject_objects(URIRef(OWL + "equivalentClass")):
        if ours(o) and str(o) in raw.classes and isinstance(s, URIRef):
            raw.classes[str(o)]["equivalents"].append(str(s))
    for type_iri in PROPERTY_TYPES:
        for s in g.subjects(URIRef(RDF + "type"), URIRef(type_iri)):
            if not ours(s) or str(s) in raw.properties:
                continue
            raw.properties[str(s)] = {
                "domains": objs(s, DOMAIN_PREDICATES),
                "ranges": objs(s, RANGE_PREDICATES),
                "parents": objs(s, [RDFS + "subPropertyOf"]),
                "label": text_of(s, RDFS + "label"),
                "comment": text_of(s, RDFS + "comment"),
            }
    return raw


def _stub_schema_pack() -> _RawPack:
    ns = SCHEMA_IRIS[0]
    classes = {ns + name: {"parents": [] if name == "Thing" else [ns + "Thing"]} for name in STUB_CLASSES}
    return _RawPack(classes, {})
