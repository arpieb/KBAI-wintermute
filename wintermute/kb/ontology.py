"""Namespaced ontology registry: class and property definitions from RDFS/OWL ontology packs.

Types are named by CURIE ("schema:Course", "wm:CourseSurvey"). Each pack is an RDF source plus the
prefixes it defines; packs are parsed with rdflib on first use and cached as JSON (full IRIs, so a
cache stays valid whatever other packs are loaded alongside it). Schema.org is the base: classes
with no known parent hang off schema:Thing, so every node can be found by a "what is X" query.

A pack's rdfs:subClassOf / owl:equivalentClass triples about classes outside its own namespaces
are kept as alignment links and applied to whichever packs define those classes. A pack with no
prefixes of its own is a pure alignment pack.
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
CACHE_FORMAT = 2

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
    # Part of a remote pack's cache key; pin remote sources so stored labels don't drift. (Local
    # files are keyed by their contents.)
    version: str = ""
    # Packs this one aligns to; they're loaded first.
    requires: tuple[str, ...] = ()

    @property
    def is_remote(self) -> bool:
        return self.source.startswith(("http://", "https://"))


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
    # [subject, "subClassOf" | "equivalentClass", object] about classes outside the pack's namespaces.
    links: list[list[str]] = field(default_factory=list)


class OntologyRegistry:
    ROOT = ROOT

    def __init__(self, packs: Iterable[OntologyPack], cache_dir: Path | None = None, timeout: float = 10.0):
        self.packs: list[OntologyPack] = []
        self.prefixes: dict[str, list[str]] = {}
        self.classes: dict[str, OntClass] = {}
        self.properties: dict[str, OntProperty] = {}
        self._ancestors: dict[str, list[str]] = {}
        self._stubbed: set[str] = set()
        # Packs that were selected but couldn't be loaded (and had no fallback).
        self.unavailable: list[str] = []
        # Alignment links each pack contributed that connected two loaded classes.
        self.applied_links: dict[str, int] = {}
        self._keys: dict[str, str] = {}

        packs = _dependency_order(list(packs))
        for pack in packs:
            self._bind(pack)
        # Longest namespace first, so nested namespaces compact to the most specific prefix.
        self._namespaces = sorted(
            ((iri, prefix) for prefix, iris in self.prefixes.items() for iri in iris), key=lambda x: -len(x[0])
        )

        raw: list[tuple[OntologyPack, _RawPack]] = []
        for pack in packs:
            loaded, key = _load_pack(pack, cache_dir, timeout)
            if loaded is None:
                log.warning("Ontology pack %r unavailable; its types will have shallow labels", pack.name)
                if pack.name == DEFAULT_PREFIX:
                    loaded, key = _stub_schema_pack(), "stub"
                    self._stubbed.add(pack.name)
                else:
                    self.unavailable.append(pack.name)
                    continue
            self._keys[pack.name] = key
            self.packs.append(pack)
            raw.append((pack, loaded))
        self._build(raw)

    @property
    def fingerprint(self) -> str:
        """Identifies the loaded packs and their contents; stored labels are valid for one fingerprint."""
        return ",".join(f"{p.name}@{self._keys[p.name]}" for p in self.packs)

    def is_stubbed(self, pack_name: str) -> bool:
        return pack_name in self._stubbed

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

    def why(self, name: str, ancestor: str) -> list[tuple[str, str, str]] | None:
        """Shortest chain of (class, "subClassOf" | "equivalentClass", class) steps from a class to an
        ancestor, or None if it isn't one. [] means they're the same class."""
        start, goal = self.curie(name) or name, self.curie(ancestor) or ancestor
        if goal not in self.ancestors(start):
            return None
        if start not in self.classes:  # unknown type: only the implicit root link
            return [] if start == goal else [(start, "subClassOf", goal)]
        came_from: dict[str, tuple[str, str, str] | None] = {start: None}
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node == goal:
                steps = []
                while came_from[node] is not None:
                    steps.append(came_from[node])
                    node = came_from[node][0]
                return steps[::-1]
            cls = self.classes.get(node)
            if cls is None:
                continue
            for rel, others in (("equivalentClass", cls.equivalents), ("subClassOf", cls.parents)):
                for other in others:
                    if other not in came_from:
                        came_from[other] = (node, rel, other)
                        queue.append(other)
        return None

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
        extra_parents: dict[str, list[str]] = {}
        equivalences: list[tuple[str, str]] = []
        for pack, data in raw:
            self.applied_links[pack.name] = 0
            for subj, rel, obj in data.links:
                a, b = self.compact(subj), self.compact(obj)
                if a in known and b in known and a != b:
                    self.applied_links[pack.name] += 1
                    if rel == "equivalentClass":
                        equivalences.append((a, b))
                    else:
                        extra_parents.setdefault(a, []).append(b)
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
        for a, b in equivalences:
            parent[find(b)] = find(a)
        groups: dict[str, list[str]] = {}
        for c in sorted(known):
            groups.setdefault(find(c), []).append(c)
        self._groups = {c: tuple(g) for g in groups.values() if len(g) > 1 for c in g}

        datatypes = {c for c, (_, d) in class_iris.items() if d.get("datatype")}
        for curie, (pack, d) in class_iris.items():
            parents = self._known(d.get("parents", ()), known)
            parents = tuple(dict.fromkeys(p for p in parents + tuple(extra_parents.get(curie, ())) if p != curie))
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


def _digest(*parts: str) -> str:
    return hashlib.sha1("|".join([str(CACHE_FORMAT), *parts]).encode()).hexdigest()[:12]


def _load_pack(pack: OntologyPack, cache_dir: Path | None, timeout: float) -> tuple[_RawPack | None, str]:
    """Load a pack from cache or source. Returns the extracted definitions and the pack's cache key."""
    text = None
    try:
        if pack.is_remote:
            key = _digest(pack.source, pack.version)
        else:
            # Local files are keyed by content, so edits take effect (and trigger a relabel).
            text = _local_path(pack.source).read_text()
            key = _digest(pack.prefixes.__repr__(), text)
    except OSError as e:
        log.warning("Could not load ontology pack %r from %s (%s)", pack.name, pack.source, e)
        return None, ""

    cache_file = cache_dir / "ontologies" / f"{pack.name}-{key}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            return _RawPack(data["classes"], data["properties"], data["links"]), key
        except (ValueError, KeyError) as e:
            log.warning("Ignoring unreadable ontology cache %s (%s)", cache_file, e)

    try:
        if text is None:
            resp = requests.get(pack.source, timeout=timeout)
            resp.raise_for_status()
            text = resp.text
        loaded = _extract(text, pack)
    except Exception as e:  # requests and rdflib raise a variety of errors
        log.warning("Could not load ontology pack %r from %s (%s)", pack.name, pack.source, e)
        return None, ""

    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"classes": loaded.classes, "properties": loaded.properties, "links": loaded.links})
        )
    return loaded, key


def _local_path(source: str) -> Path:
    path = Path(source).expanduser()
    return path if path.is_absolute() else ONTOLOGY_DATA / path


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
    # Class links asserted about other vocabularies' classes (including equivalences stated from the
    # other side, e.g. "schema:Person owl:equivalentClass dbo:Person" in the dbo pack).
    for rel in ("subClassOf", "equivalentClass"):
        pred = URIRef((RDFS if rel == "subClassOf" else OWL) + rel)
        for s, o in g.subject_objects(pred):
            if isinstance(s, URIRef) and isinstance(o, URIRef) and not ours(s):
                raw.links.append([str(s), rel, str(o)])
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
