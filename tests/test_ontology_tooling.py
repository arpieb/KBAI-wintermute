"""Ontology manifests, alignment packs, cache keys, explanations and the `wintermute ontologies` CLI."""

import dataclasses
from pathlib import Path

import pytest

from conftest import DBO_PACK, FIXTURE_ONTOLOGIES, SCHEMA_PACK, make_registry
from wintermute import cli, ontology_cli
from wintermute.config import Settings
from wintermute.kb.ontology import ROOT, OntologyPack, OntologyRegistry
from wintermute.kb.ontology_packs import PACKS, ManifestError, load_manifest, select_packs
from wintermute.models import Thing

ALIGN_PACK = OntologyPack(
    name="align", prefixes={}, source=str(FIXTURE_ONTOLOGIES / "align-mini.ttl"), requires=("schema", "dbo")
)


@pytest.fixture(scope="module")
def aligned():
    return make_registry(DBO_PACK, ALIGN_PACK)


# --- Alignment packs ----------------------------------------------------------------------------


def test_alignment_pack_links_other_packs_classes(aligned):
    assert aligned.classes["dbo:TopicalConcept"].parents == ("schema:Intangible",)
    assert aligned.is_a("dbo:Settlement", "schema:AdministrativeArea")
    assert aligned.is_a("schema:AdministrativeArea", "dbo:PopulatedPlace")
    assert aligned.applied_links["align"] == 2  # the link between unloaded classes is ignored
    assert not any(c.pack == "align" for c in aligned.classes.values())


def test_without_alignment_pack_classes_stay_unlinked(ontology):
    with_dbo = make_registry(DBO_PACK)
    assert with_dbo.ancestors("dbo:TopicalConcept") == [ROOT, "dbo:TopicalConcept"]
    assert not with_dbo.is_a("dbo:Settlement", "schema:AdministrativeArea")


# --- Cache keys ------------------------------------------------------------------------------------


def test_local_packs_are_keyed_by_content(tmp_path):
    ttl = tmp_path / "local.ttl"
    ttl.write_text(
        "@prefix ex: <http://example.org/ns#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "ex:A a rdfs:Class .\n"
    )
    pack = OntologyPack(name="ex", prefixes={"ex": ["http://example.org/ns#"]}, source=str(ttl), requires=("schema",))
    first = make_registry(pack, cache_dir=tmp_path / "cache")
    assert "ex:B" not in first

    ttl.write_text(ttl.read_text() + "ex:B a rdfs:Class ; rdfs:subClassOf ex:A .\n")
    second = make_registry(pack, cache_dir=tmp_path / "cache")
    assert second.ancestors("ex:B") == [ROOT, "ex:A", "ex:B"]
    assert first.fingerprint != second.fingerprint


def test_missing_local_pack_is_reported_unavailable(caplog):
    missing = OntologyPack(name="gone", prefixes={"gone": ["http://example.org/gone#"]}, source="/nonexistent.ttl")
    registry = make_registry(missing)
    assert registry.unavailable == ["gone"]
    assert "gone@" not in registry.fingerprint


# --- why() -------------------------------------------------------------------------------------


def test_why_returns_shortest_chain(aligned):
    assert aligned.why("schema:City", "schema:Place") == [
        ("schema:City", "subClassOf", "schema:AdministrativeArea"),
        ("schema:AdministrativeArea", "subClassOf", "schema:Place"),
    ]
    assert aligned.why("dbo:City", "schema:AdministrativeArea") == [
        ("dbo:City", "equivalentClass", "schema:City"),
        ("schema:City", "subClassOf", "schema:AdministrativeArea"),
    ]
    assert aligned.why("schema:City", "schema:City") == []
    assert aligned.why("schema:City", "schema:Person") is None
    assert aligned.why("schema:Bogus", ROOT) == [("schema:Bogus", "subClassOf", ROOT)]


# --- Manifests ------------------------------------------------------------------------------------


def test_manifest_packs_and_load_list():
    packs, load = load_manifest(FIXTURE_ONTOLOGIES / "manifest.toml")
    assert load == ["schema", "wm", "extra", "extra-align"]
    extra = packs["extra"]
    assert extra.prefixes == {"ex": ["http://example.org/ns#"]}
    assert extra.source == str((FIXTURE_ONTOLOGIES / "extra.ttl").resolve())  # relative to the manifest
    assert extra.version == "1"
    assert extra.requires == ("schema",)
    assert packs["extra-align"].source == "/absolute/align.ttl"
    assert packs["extra-align"].prefixes == {}


def test_pack_selection_precedence(monkeypatch):
    manifest = FIXTURE_ONTOLOGIES / "manifest.toml"
    monkeypatch.delenv("WINTERMUTE_ONTOLOGIES", raising=False)
    names = lambda **kw: [p.name for p in select_packs(Settings(**kw))]  # noqa: E731
    assert names(ontology_manifest=None) == ["schema", "wm", "dbo"]
    assert names(ontology_manifest=manifest) == ["schema", "wm", "extra", "extra-align"]
    assert names(ontology_manifest=manifest, ontologies=["schema", "extra"]) == ["schema", "extra"]
    monkeypatch.setenv("WINTERMUTE_ONTOLOGY_MANIFEST", str(manifest))
    assert Settings().ontology_manifest == manifest


@pytest.mark.parametrize(
    "text, error",
    [
        ('[packs.schema]\nsource = "x.ttl"\n', "redefines the built-in pack 'schema'"),
        ('[packs.x]\nprefixes = {}\n', "needs a `source`"),
        ('[packs.x]\nsource = "x.ttl"\nurl = "y"\n', "unknown key(s) url"),
        ('loads = ["schema"]\n', "unknown top-level key(s) loads"),
        ('load = "schema"\n', "`load` must be a list"),
        ("not toml [", "Can't read ontology manifest"),
    ],
)
def test_manifest_errors(tmp_path, text, error):
    path = tmp_path / "manifest.toml"
    path.write_text(text)
    with pytest.raises(ManifestError, match=error.replace("(", r"\(").replace(")", r"\)")):
        load_manifest(path)


def test_unknown_pack_in_selection():
    with pytest.raises(ValueError, match="Unknown ontology pack"):
        select_packs(Settings(ontologies=["schema", "nope"], ontology_manifest=None))


# --- CLI ---------------------------------------------------------------------------------------


def test_describe_packs(aligned):
    text = ontology_cli.describe_packs(aligned)
    assert "schema  schema: <https://schema.org/>" in text
    assert "align   (alignment only)" in text
    assert "2 alignment links" in text
    assert "1 class, 4 properties" in text  # wm
    assert f"Fingerprint: {aligned.fingerprint}" in text


def test_describe_stubbed_pack():
    stubbed = OntologyRegistry([dataclasses.replace(SCHEMA_PACK, source="/nonexistent.ttl"), PACKS["wm"]])
    assert "UNAVAILABLE, using a built-in stub" in ontology_cli.describe_packs(stubbed)


def test_explain_class_with_why_and_properties(aligned):
    text = ontology_cli.explain(aligned, "dbo:City", is_type="Place", show_properties=True)
    assert text.startswith("dbo:City  class from pack 'dbo'")
    assert "Equivalent to: schema:City" in text
    assert "Is a schema:Place, because:\n    dbo:City equivalentClass schema:City" in text
    assert "schema:containedInPlace: schema:Place  (from schema:Place)" in text
    assert "Not a schema:Person." in ontology_cli.explain(aligned, "City", is_type="Person")


def test_explain_property(aligned):
    text = ontology_cli.explain(aligned, "birthPlace")
    assert "schema:birthPlace  property from pack 'schema'" in text
    assert "Domain: schema:Person" in text and "Range: schema:Place" in text


def test_explain_thing_and_unknown(aligned):
    thing = Thing(name="Atlanta", labels=aligned.labels_for(["dbo:City"]), id="http://dbpedia.org/resource/Atlanta")
    text = ontology_cli.explain(aligned, "Atlanta", is_type="schema:Place", find_things=lambda name: [thing])
    assert "'Atlanta'  stored thing http://dbpedia.org/resource/Atlanta" in text
    assert "Is a schema:Place, because:" in text
    assert "isn't a type or property" in ontology_cli.explain(aligned, "Nowhere", find_things=lambda name: [])


def test_cli_ontologies_command_does_not_build_the_kb(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("WINTERMUTE_DB", str(tmp_path / "absent.lbdb"))
    monkeypatch.setenv("WINTERMUTE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "select_packs", lambda settings: [SCHEMA_PACK, PACKS["wm"]])
    cli.main(["ontologies"])
    assert "Loaded ontology packs:" in capsys.readouterr().out
    cli.main(["ontologies", "explain", "City", "--is", "Place"])
    assert "Is a schema:Place, because:" in capsys.readouterr().out
    cli.main(["ontologies", "explain", "Somewhere Unknown"])
    assert "isn't a type or property" in capsys.readouterr().out
    assert not Path(tmp_path / "absent.lbdb").exists()
