"""Remote topic lookups for subjects not already in the local knowledge base."""

from __future__ import annotations

import logging
import numbers
import time

import requests

from wintermute.kb.schema_org import SchemaOrg
from wintermute.models import Thing

log = logging.getLogger(__name__)

DBPEDIA_SPARQL = "https://dbpedia.org/sparql"
GOOGLE_KG_SEARCH = "https://kgsearch.googleapis.com/v1/entities:search"

# Exact (English) label match, following Wikipedia redirects and skipping disambiguation and
# category pages. Current DBpedia releases carry short dbo:description text rather than abstracts.
DBPEDIA_QUERY = """
SELECT ?s ?label (SAMPLE(?descr) AS ?description) (GROUP_CONCAT(DISTINCT ?type; separator=" ") AS ?types)
WHERE {
  VALUES ?q { %s }
  ?r rdfs:label ?q .
  OPTIONAL { ?r dbo:wikiPageRedirects ?red }
  BIND(COALESCE(?red, ?r) AS ?s)
  FILTER(!STRSTARTS(STR(?s), "http://dbpedia.org/resource/Category:"))
  FILTER NOT EXISTS { ?s dbo:wikiPageDisambiguates ?d }
  ?s rdfs:label ?label . FILTER(lang(?label) = "en")
  OPTIONAL { ?s dbo:description|dbo:abstract|rdfs:comment ?descr . FILTER(lang(?descr) = "en") }
  OPTIONAL {
    ?s rdf:type ?type .
    FILTER(STRSTARTS(STR(?type), "http://schema.org/") || STRSTARTS(STR(?type), "http://dbpedia.org/ontology/"))
  }
}
GROUP BY ?s ?label
LIMIT 5
"""


class LookupFailed(Exception):
    pass


def _scalar_props(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("@") and isinstance(v, (str, numbers.Number))}


def _sparql_literal(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{escaped}"@en'


class DBpediaLookup:
    """DBpedia via its public SPARQL endpoint.

    The endpoint throws occasional transient errors (HTTP 5xx, timeouts), so requests are retried a
    few times. If it's still failing, lookups pause for a cooldown period rather than for the rest of
    the session, and each query is retried as normal afterwards.
    """

    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
    ATTEMPTS = 3
    BACKOFF = 0.5  # seconds, doubled per retry
    COOLDOWN = 60.0  # seconds

    def __init__(self, schema: SchemaOrg, timeout: float):
        self.schema = schema
        self.timeout = timeout
        self.paused_until = 0.0

    def _fetch(self, sparql: str) -> list[dict]:
        """Run a query, retrying transient failures. Raises LookupFailed with a short reason."""
        reason = ""
        for attempt in range(self.ATTEMPTS):
            if attempt:
                time.sleep(self.BACKOFF * 2 ** (attempt - 1))
            try:
                resp = requests.get(
                    DBPEDIA_SPARQL,
                    params={"query": sparql, "format": "json"},
                    headers={"Accept": "application/sparql-results+json"},
                    timeout=self.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                reason = type(e).__name__
                continue
            if resp.status_code in self.RETRY_STATUSES:
                reason = f"HTTP {resp.status_code}"
                continue
            if not resp.ok:
                raise LookupFailed(f"HTTP {resp.status_code}")
            try:
                return resp.json()["results"]["bindings"]
            except (ValueError, KeyError) as e:
                raise LookupFailed(f"unexpected response ({type(e).__name__})") from e
        raise LookupFailed(f"{reason} after {self.ATTEMPTS} attempts")

    def search(self, query: str) -> list[Thing]:
        query = query.strip()
        if not query or time.monotonic() < self.paused_until:
            return []
        # Wikipedia titles are sentence case ("Machine learning"); people and places are title case.
        variants = dict.fromkeys([query, query[:1].upper() + query[1:], query.title()])
        sparql = DBPEDIA_QUERY % " ".join(_sparql_literal(v) for v in variants)
        try:
            rows = self._fetch(sparql)
        except LookupFailed as e:
            self.paused_until = time.monotonic() + self.COOLDOWN
            log.warning("DBpedia lookup for %r failed (%s); pausing DBpedia lookups for %ds", query, e, self.COOLDOWN)
            return []

        things = []
        for row in rows:
            uri, label = row["s"]["value"], row["label"]["value"]
            # Schema.org and DBpedia ontology class names largely coincide; keep the ones Schema.org knows.
            types = [t.rsplit("/", 1)[-1] for t in row.get("types", {}).get("value", "").split()]
            types = [t for t in dict.fromkeys(types) if t in self.schema]
            props = {"description": row.get("description", {}).get("value", ""), "url": uri}
            things.append(Thing(name=label, labels=self.schema.labels_for(types), props=props, id=uri))
        return things


class GoogleKGLookup:
    def __init__(self, schema: SchemaOrg, api_key: str | None, timeout: float):
        self.schema = schema
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, label: str = "Thing") -> list[Thing]:
        if not self.api_key:
            return []
        try:
            resp = requests.get(
                GOOGLE_KG_SEARCH,
                params={"query": query, "types": label, "key": self.api_key, "limit": 5},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            items = resp.json().get("itemListElement", [])
        except (requests.RequestException, ValueError) as e:
            log.warning("Google Knowledge Graph lookup failed: %s", e)
            return []

        things = []
        for item in items:
            result = item.get("result", {})
            if "name" not in result:
                continue
            props = _scalar_props(result)
            if isinstance(result.get("detailedDescription"), dict):
                props.setdefault("articleBody", result["detailedDescription"].get("articleBody", ""))
            types = result.get("@type", [])
            types = types if isinstance(types, list) else [types]
            things.append(Thing(name=result["name"], labels=self.schema.labels_for(types), props=props, id=result["@id"]))
        return things
