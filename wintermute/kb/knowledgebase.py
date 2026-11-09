"""Knowledge base facade: topic lookup, frame resolution and conversation context persistence."""

from __future__ import annotations

import json
import logging
from typing import Any

from wintermute.config import Settings
from wintermute.kb import loaders
from wintermute.kb.graph import Graph
from wintermute.kb.lookup import DBpediaLookup, GoogleKGLookup
from wintermute.kb.ontology import OntologyRegistry
from wintermute.kb.ontology_packs import packs_for
from wintermute.models import FNFrame, FNFrameElement, FNLexicalUnit, Thing

log = logging.getLogger(__name__)

# Frame relations walked "up" when looking for a common ancestor frame. The 2016 Cypher asked for
# Inheritance|Reframing_Mapping, but FrameNet spells it "ReFraming_Mapping", so in practice only
# Inheritance was ever traversed. That effective behavior is preserved here.
ANCESTOR_RELATIONS = ["Inheritance"]
MAX_ANCESTOR_DEPTH = 30

# Bumped when the stored representation changes in a way that needs a rebuild. Format 2: Thing
# labels are CURIEs ("schema:Course") and Thing IDs hash only the most specific type.
KB_FORMAT = "kb_format=2"


# KBStatus key prefix recording which ontology packs the stored labels were computed with.
ONTOLOGY_STATUS_PREFIX = "ontology="


class KBFormatError(RuntimeError):
    pass


class Knowledgebase:
    def __init__(
        self, settings: Settings | None = None, graph: Graph | None = None, ontology: OntologyRegistry | None = None
    ):
        self.settings = settings or Settings()
        self.graph = graph or Graph(self.settings.db_path)
        self._check_format()
        self.ontology = ontology or OntologyRegistry(
            packs_for(self.settings.ontologies), self.settings.cache_dir, self.settings.http_timeout
        )
        self._sync_labels()
        self.dbpedia = DBpediaLookup(self.ontology, self.settings.http_timeout)
        google_key = self.settings.google_api_key if self.settings.google_kg else None
        self.google = GoogleKGLookup(self.ontology, google_key, self.settings.http_timeout)

    def _check_format(self) -> None:
        if self.graph.is_loaded(KB_FORMAT):
            return
        if self.graph.rows("MATCH (t:Thing) RETURN t.ID LIMIT 1"):
            raise KBFormatError(
                f"The knowledge base at {self.settings.db_path} was built by an older version of Wintermute. "
                "Delete it and run `wintermute init` to rebuild it."
            )
        self.graph.mark_loaded(KB_FORMAT)

    def _sync_labels(self) -> None:
        """Re-expand stored labels when the loaded ontology packs change (one KBStatus row per set).

        Labels from a pack that's no longer loaded are kept (they're harmless); remote types that were
        dropped at lookup time because no pack knew them can't be recovered.
        """
        key = f"{ONTOLOGY_STATUS_PREFIX}{self.ontology.fingerprint}"
        if self.graph.is_loaded(key):
            return
        changed = 0
        for row in self.graph.rows("MATCH (t:Thing) RETURN t.ID AS id, t.labels AS labels"):
            labels = self.ontology.labels_for(row["labels"])
            if labels != list(row["labels"]):
                self.graph.execute("MATCH (t:Thing {ID: $id}) SET t.labels = $labels", {"id": row["id"], "labels": labels})
                changed += 1
        if changed:
            log.info("Updated labels on %d things for ontology packs %s", changed, self.ontology.fingerprint)
        self.graph.execute(
            "MATCH (s:KBStatus) WHERE starts_with(s.key, $prefix) DELETE s", {"prefix": ONTOLOGY_STATUS_PREFIX}
        )
        self.graph.mark_loaded(key)

    def initialize(self) -> None:
        """Populate the KB from static sources. Each dataset loads once; see KBStatus."""
        loaders.load_framenet(self, self.settings.framenet_dir)
        loaders.load_course_catalog(self, self.settings.http_timeout)
        loaders.load_course_surveys(self, self.settings.http_timeout)
        loaders.load_specializations(self)  # links to courses, so must run after the catalog

    # --- Things ------------------------------------------------------------------------------

    def save_thing(self, thing: Thing) -> None:
        """Store a thing, expanding its labels to the full canonical ancestry of its types."""
        thing.labels = self.ontology.labels_for(thing.labels)
        self.graph.execute(
            "MERGE (t:Thing {ID: $id}) SET t.name = $name, t.labels = $labels, t.props = $props",
            {"id": thing.id, "name": thing.name, "labels": thing.labels, "props": json.dumps(thing.props)},
        )

    def save_relationship(self, src: Thing, dst: Thing, rel_type: str) -> None:
        """Link two existing things (no-op if either is missing, matching the original MATCH semantics)."""
        self.graph.execute(
            "MATCH (a:Thing {ID: $a}), (b:Thing {ID: $b}) MERGE (a)-[:related {type: $type}]->(b)",
            {"a": src.id, "b": dst.id, "type": rel_type},
        )

    def add_alias(self, alias: str, thing: Thing) -> None:
        self.graph.execute(
            "MERGE (a:Alias {name: $alias}) WITH a MATCH (t:Thing {ID: $id}) MERGE (a)-[:alias_of]->(t)",
            {"alias": alias.lower(), "id": thing.id},
        )

    @staticmethod
    def _thing(node: dict[str, Any]) -> Thing:
        return Thing.from_record(node["ID"], node["name"], list(node["labels"]), json.loads(node["props"] or "{}"))

    def find_topics(self, query: str, label: str = "schema:Thing") -> list[Thing]:
        """Find things by name or alias, falling back to DBpedia then Google, caching what's found."""
        topics = self._find_topics_local(query, label)
        if not topics and self.settings.remote_lookups:
            remote = self.dbpedia.search(query) or self.google.search(query, label)
            for thing in remote:
                self.save_thing(thing)
                self.add_alias(query, thing)
            # Re-query locally so results are filtered by label consistently.
            topics = self._find_topics_local(query, label)
        return topics

    def _find_topics_local(self, query: str, label: str) -> list[Thing]:
        params = {"name": query.lower(), "label": self.ontology.curie(label) or label}
        by_name = self.graph.rows(
            "MATCH (t:Thing) WHERE lower(t.name) = $name AND list_contains(t.labels, $label) RETURN t", params
        )
        by_alias = self.graph.rows(
            "MATCH (:Alias {name: $name})-[:alias_of]->(t:Thing) WHERE list_contains(t.labels, $label) RETURN t", params
        )
        seen, topics = set(), []
        for row in by_alias + by_name:
            if row["t"]["ID"] not in seen:
                seen.add(row["t"]["ID"])
                topics.append(self._thing(row["t"]))
        return topics

    # --- FrameNet ----------------------------------------------------------------------------

    def find_frames(self, lemmata: list[str]) -> list[FNFrame]:
        """Find the most specific frame shared by the frames evoked by the given "lemma.pos" strings."""
        if not lemmata:
            return []
        candidates = self.graph.rows(
            """
            MATCH (n:Frame)-[:frame_element {type: 'Core'}]->(:FE)<-[:valence_unit]-(lu:LU)
            WHERE lu.name IN $lemmata
            RETURN DISTINCT n.ID AS id
            """,
            {"lemmata": lemmata},
        )
        if not candidates:
            return []

        # All ancestor paths (root first) into the candidate frames.
        paths = [
            row["ids"]
            for row in self.graph.rows(
                f"""
                MATCH p = (:Frame)-[:frame_relation*1..{MAX_ANCESTOR_DEPTH} (r, _ | WHERE r.type IN $types)]->(n:Frame)
                WHERE n.ID IN $ids
                RETURN properties(nodes(p), 'ID') AS ids
                """,
                {"types": ANCESTOR_RELATIONS, "ids": [c["id"] for c in candidates]},
            )
        ]
        if not paths:
            return []

        # Of the longest paths, the deepest node they all share is the common ancestor.
        max_len = max(len(p) for p in paths)
        longest = [p for p in paths if len(p) == max_len]
        common = set(longest[0]).intersection(*longest[1:])
        shared = [fid for fid in longest[-1] if fid in common]
        if not shared:
            return []
        return [self._load_frame(shared[-1])]

    def _load_frame(self, frame_id: int) -> FNFrame:
        rows = self.graph.rows(
            """
            MATCH (f:Frame {ID: $fid})-[:frame_element]->(fe:FE)<-[vu:valence_unit]-(lu:LU)
            RETURN f.name AS frame, fe.ID AS fe_id, fe.name AS fe_name, lu.ID AS lu_id, lu.name AS lu_name,
                   vu.GF AS GF, vu.PT AS PT
            """,
            {"fid": frame_id},
        )
        name = rows[0]["frame"] if rows else self.graph.rows("MATCH (f:Frame {ID: $fid}) RETURN f.name AS n", {"fid": frame_id})[0]["n"]
        frame = FNFrame(ID=frame_id, name=name)
        for row in rows:
            fe = frame.fes.setdefault(row["fe_name"], FNFrameElement(ID=row["fe_id"], name=row["fe_name"]))
            lu = fe.lus.setdefault(row["lu_name"], FNLexicalUnit(ID=row["lu_id"], name=row["lu_name"]))
            lu.rels.append({"GF": row["GF"], "PT": row["PT"]})
        return frame

    # --- Conversation contexts ---------------------------------------------------------------

    def load_context(self, context_id: str) -> tuple[list[Thing], dict[str, str]]:
        ctx = self.graph.rows("MATCH (c:Context {ID: $id}) RETURN c.config AS config", {"id": context_id})
        config = json.loads(ctx[0]["config"] or "{}") if ctx else {}
        topics = [
            self._thing(row["t"])
            for row in self.graph.rows("MATCH (:Context {ID: $id})-[:context_uses]->(t:Thing) RETURN t", {"id": context_id})
        ]
        return topics, config

    def save_context(self, context_id: str, topics: list[Thing], config: dict[str, str]) -> None:
        self.graph.execute("MERGE (c:Context {ID: $id}) SET c.config = $config", {"id": context_id, "config": json.dumps(config)})
        for topic in topics:
            self.graph.execute(
                "MATCH (c:Context {ID: $cid}), (t:Thing {ID: $tid}) MERGE (c)-[:context_uses]->(t)",
                {"cid": context_id, "tid": topic.id},
            )

    def close(self) -> None:
        self.graph.close()
