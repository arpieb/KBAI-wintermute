# Ontology packs

Wintermute types everything in its knowledge base with classes from **ontology packs**. Schema.org is
the base; other ontologies attach beneath it, so an agent can load extra domain vocabularies without
changing code. This guide covers how packs work, how to choose the packs an agent loads, and how to
add one.

## Concepts

- **Types are CURIEs.** A stored `Thing` has a `labels` list holding its full type ancestry, root
  first and most specific type last:
  `["schema:Thing", "schema:CreativeWork", "dbo:Work", "schema:LearningResource", "schema:Course"]`.
  Bare names (`Place`) mean the `schema` namespace.
- **A pack** is one RDF document (Turtle, JSON-LD, RDF/XML, …) plus the prefixes it defines. From it
  Wintermute reads:
  - classes (`rdfs:Class` / `owl:Class`) with their `rdfs:subClassOf` parents and
    `owl:equivalentClass` links;
  - properties with their domain and range (`rdfs:domain`/`rdfs:range` or Schema.org's
    `schema:domainIncludes`/`schema:rangeIncludes`).
  Only classes and properties inside the pack's own namespaces are defined by it.
- **Alignment links.** `subClassOf` and `equivalentClass` triples *about another pack's classes* are
  applied to whichever loaded pack defines those classes. A pack with no prefixes of its own is a
  pure **alignment pack**.
- **Equivalent classes share ancestry.** If `dbo:City owl:equivalentClass schema:City`, a node typed
  `dbo:City` is also a `schema:City`, a `schema:Place`, and so on, and both names appear in its
  labels.
- **Everything is a `schema:Thing`.** Classes with no loaded parent, and other vocabularies'
  `owl:Thing`, attach to `schema:Thing`, so the "what is X" question can always find a node.

## Built-in packs

| Pack | Prefix | Source |
|---|---|---|
| `schema` | `schema:` → `https://schema.org/` (also `http://`) | Schema.org 30.1 |
| `wm` | `wm:` → `urn:wintermute:` | `wintermute/data/ontologies/wintermute.ttl` (`wm:CourseSurvey`) |
| `dbo` | `dbo:` → `http://dbpedia.org/ontology/` | DBpedia ontology snapshot 2026.07.22 from `dbpedia/ontology-tracker` |

The default is `schema,wm,dbo`. The DBpedia ontology links 40 of its classes to Schema.org itself, so
remote DBpedia results that only have `dbo:` types still answer "who" (`schema:Person`) and
"where" (`schema:Place`) questions.

## Choosing packs for an agent

The packs an agent loads come from the first of these that's set:

1. `WINTERMUTE_ONTOLOGIES`: comma-separated pack names, e.g. `schema,wm`.
2. The `load` list in the manifest named by `WINTERMUTE_ONTOLOGY_MANIFEST`.
3. The defaults: `schema,wm,dbo`.

A **manifest** is a TOML file that declares extra packs, and optionally which packs to load:

```toml
load = ["schema", "wm", "dbo", "bioschemas", "dbpedia-lifesci-align"]

[packs.bioschemas]
prefixes = { bioschemas = "https://bioschemas.org/terms/" }   # a string, or a list: [canonical, *alternates]
source = "https://raw.githubusercontent.com/BioSchemas/bioschemas.github.io/<commit>/pages/_types/bioschemas_types.jsonld"
format = "json-ld"            # an rdflib parser name; guessed from the extension if omitted
version = "v20250219"         # part of a remote pack's cache key
requires = ["schema"]         # loaded first

[packs.dbpedia-lifesci-align]
source = "dbpedia-lifesci-align.ttl"   # relative paths resolve against the manifest's directory
requires = ["schema", "dbo"]
```

Manifest packs can't reuse a built-in pack's name, and no prefix may be bound to two different IRIs.

`examples/ontologies/bioschemas/` is a complete example for a life-sciences agent:

```sh
export WINTERMUTE_ONTOLOGY_MANIFEST=examples/ontologies/bioschemas/manifest.toml
uv run wintermute ontologies
uv run wintermute                      # ask "what is BRCA1"
uv run wintermute ontologies explain BRCA1 --is schema:Gene
```

## Inspecting packs

```sh
uv run wintermute ontologies                                # loaded packs, sizes, versions, fingerprint
uv run wintermute ontologies explain dbo:Mountain           # ancestry, parents, equivalents, property count
uv run wintermute ontologies explain dbo:Mountain --is Place --properties
uv run wintermute ontologies explain birthPlace             # a property: domain, range
uv run wintermute ontologies explain "Mount Everest" --is Place   # a stored thing, and why it's a Place
```

`--is TYPE` prints the shortest chain of `subClassOf`/`equivalentClass` steps that makes the term a
TYPE. That's the quickest way to check what an alignment actually does. Explaining a stored thing
only looks at the local knowledge base, so it never triggers a remote lookup.

## Adding a pack

1. **Pin the source.** Use a versioned URL or a commit-pinned raw GitHub URL, and set `version`.
   Remote packs are cached by URL and version, so an unpinned URL that changes upstream won't be
   picked up, or will change without notice when the cache is cleared. Local files are cached by
   their contents, so edits take effect on the next start.
2. **Declare its prefixes**, plus any alternate spellings of its namespace IRI.
3. **Attach it to Schema.org.** Check whether the ontology already links its classes to Schema.org
   (`owl:equivalentClass` or `rdfs:subClassOf` to `schema:` classes). Bioschemas does; DBpedia does
   for 40 classes. Where it doesn't, write an alignment pack, as in
   `examples/ontologies/bioschemas/dbpedia-lifesci-align.ttl`.
4. **Check the other side's hierarchy before asserting an equivalence.** Equivalence merges the two
   classes' ancestries in both directions. For example, `dbo:Species ≡ schema:Taxon` looks reasonable,
   but in the DBpedia ontology `dbo:Person` is a subclass of `dbo:Animal` under `dbo:Eukaryote` under
   `dbo:Species`, so that alignment would make every person a taxon. Use `rdfs:subClassOf` when one
   class is narrower, and run `ontologies explain … --is …` on a few classes from each side
   afterwards.
5. **Add it.** Put it in a manifest. If every agent should have it, add it to `PACKS` in
   `wintermute/kb/ontology_packs.py` (local files go in `wintermute/data/ontologies/`).

## What happens when packs change

The registry has a **fingerprint** covering the loaded packs and their versions or contents. The
knowledge base records the fingerprint its labels were computed with. When it changes, every stored
`Thing`'s labels are expanded again on the next start (usually well under a second).

- Labels are only ever added. Removing a pack leaves its labels on existing nodes, which is harmless.
- Thing IDs depend only on the name and the most specific type, so they don't change.
- Remote results are typed when they're fetched. Types that no loaded pack knew at that point were
  dropped, so a later relabel can't bring them back. To retype a remote result, delete it (or
  rebuild the KB) and ask about it again.

If a remote pack can't be downloaded and isn't cached, it's skipped with a warning. If that pack is
Schema.org, a small built-in stub (Thing, Person, Place, Organization, Event, CreativeWork) keeps the
question-word filters working. The `ontologies` command shows packs in either state as
UNAVAILABLE.

## Current limits

- Only class hierarchy and equivalence are used for inference. OWL restrictions, transitive or
  inverse properties, and `owl:equivalentProperty` are ignored.
- Properties are recorded (domain, range, sub-property) but not used for answering yet. Stored
  properties are still a JSON blob with bare keys.
