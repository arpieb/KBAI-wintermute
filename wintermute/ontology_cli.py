"""`wintermute ontologies`: inspect the loaded ontology packs and explain types, properties and things."""

from __future__ import annotations

from collections.abc import Callable

from wintermute import nlp
from wintermute.kb.ontology import OntClass, OntologyRegistry, OntProperty
from wintermute.models import Thing

INDENT = "  "


def describe_packs(registry: OntologyRegistry) -> str:
    rows = []
    for pack in registry.packs:
        classes = sum(1 for c in registry.classes.values() if c.pack == pack.name)
        props = sum(1 for p in registry.properties.values() if p.pack == pack.name)
        links = registry.applied_links.get(pack.name, 0)
        prefixes = ", ".join(f"{p}: <{iris[0]}>" for p, iris in pack.prefixes.items()) or "(alignment only)"
        if registry.is_stubbed(pack.name):
            origin = "UNAVAILABLE, using a built-in stub"
        else:
            origin = f"{pack.version or 'unversioned'}, {'remote' if pack.is_remote else 'local'}"
        counts = f"{_n(classes, 'class', 'classes')}, {_n(props, 'property', 'properties')}"
        if links:
            counts += f", {_n(links, 'alignment link', 'alignment links')}"
        rows.append((pack.name, prefixes, origin, counts))

    out = ["Loaded ontology packs:"]
    width = max((len(r[0]) for r in rows), default=0)
    for name, prefixes, origin, counts in rows:
        out.append(f"{INDENT}{name:<{width}}  {prefixes}")
        out.append(f"{INDENT}{'':<{width}}  {origin}; {counts}")
    for name in registry.unavailable:
        out.append(f"{INDENT}{name:<{width}}  UNAVAILABLE (couldn't be loaded; see warnings)")
    out.append(f"Fingerprint: {registry.fingerprint}")
    return "\n".join(out)


def explain(
    registry: OntologyRegistry,
    term: str,
    is_type: str | None = None,
    show_properties: bool = False,
    find_things: Callable[[str], list[Thing]] | None = None,
) -> str:
    """Explain a class, property, or (via find_things) a stored thing named `term`."""
    curie = registry.resolve(term)
    if curie in registry.classes:
        return _explain_class(registry, registry.classes[curie], is_type, show_properties)
    if curie in registry.properties:
        return _explain_property(registry, registry.properties[curie])
    things = find_things(term) if find_things else []
    if things:
        return "\n\n".join(_explain_thing(registry, t, is_type, show_properties) for t in things)
    return (
        f"{term!r} isn't a type or property in the loaded packs, or a thing stored in the knowledge base. "
        "(Things found remotely are stored once the bot has been asked about them.)"
    )


def _n(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _summary(text: str, limit: int = 200) -> str:
    text = nlp.first_sentence(text) if text else ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _explain_class(registry: OntologyRegistry, cls: OntClass, is_type: str | None, show_properties: bool) -> str:
    out = [f"{cls.curie}  class from pack {cls.pack!r}" + ("  (datatype)" if cls.datatype else "")]
    if cls.comment:
        out.append(INDENT + _summary(cls.comment))
    out.append(f"{INDENT}Ancestry: {' > '.join(registry.ancestors(cls.curie))}")
    if cls.parents:
        out.append(f"{INDENT}Direct parents: {', '.join(cls.parents)}")
    if cls.equivalents:
        out.append(f"{INDENT}Equivalent to: {', '.join(cls.equivalents)}")
    out += _properties_section(registry, cls.curie, show_properties)
    if is_type:
        out += _why_section(registry, cls.curie, is_type)
    return "\n".join(out)


def _properties_section(registry: OntologyRegistry, curie: str, show: bool) -> list[str]:
    props = registry.properties_of(curie)
    lineage = registry.ancestors(curie)
    own_domains = {curie, *registry.classes[curie].equivalents}
    own = [p for p in props if own_domains.intersection(p.domains)]
    out = [f"{INDENT}Properties: {len(props)} ({len(own)} declared on this class or its equivalents)"]
    if show:
        for p in sorted(props, key=lambda p: (-max(lineage.index(d) for d in p.domains if d in lineage), p.curie)):
            via = ", ".join(d for d in p.domains if d in lineage)
            ranges = " | ".join(p.ranges) or "?"
            out.append(f"{INDENT * 2}{p.curie}: {ranges}  (from {via})")
    return out


def _why_section(registry: OntologyRegistry, curie: str, is_type: str) -> list[str]:
    target = registry.curie(is_type) or is_type
    steps = registry.why(curie, target)
    if steps is None:
        return [f"{INDENT}Not a {target}."]
    if not steps:
        return [f"{INDENT}Is {target}: it's the same class."]
    out = [f"{INDENT}Is a {target}, because:"]
    for a, rel, b in steps:
        out.append(f"{INDENT * 2}{a} {rel} {b}")
    return out


def _explain_property(registry: OntologyRegistry, prop: OntProperty) -> str:
    out = [f"{prop.curie}  property from pack {prop.pack!r}"]
    if prop.comment:
        out.append(INDENT + _summary(prop.comment))
    out.append(f"{INDENT}Domain: {', '.join(prop.domains) or '(any)'}")
    out.append(f"{INDENT}Range: {', '.join(prop.ranges) or '(any)'}")
    if prop.parents:
        out.append(f"{INDENT}Sub-property of: {', '.join(prop.parents)}")
    return "\n".join(out)


def _explain_thing(registry: OntologyRegistry, thing: Thing, is_type: str | None, show_properties: bool) -> str:
    most_specific = thing.labels[-1]
    out = [f"{thing.name!r}  stored thing {thing.id}", f"{INDENT}Labels: {', '.join(thing.labels)}"]
    if most_specific in registry.classes:
        out.append(f"{INDENT}Most specific type: {most_specific}")
        out += _properties_section(registry, most_specific, show_properties)
    if is_type:
        target = registry.curie(is_type) or is_type
        # Stored labels can merge several types; explain via the first one that leads to the target.
        for label in reversed(thing.labels):
            steps = registry.why(label, target)
            if steps is not None:
                out += _why_section(registry, label, target)
                break
        else:
            out.append(f"{INDENT}Not a {target}.")
    return "\n".join(out)
