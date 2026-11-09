import dataclasses

import pytest

from conftest import DBO_PACK, SCHEMA_PACK, make_registry
from wintermute import models
from wintermute.config import Settings
from wintermute.kb import ontology as ontology_mod
from wintermute.kb import ontology_packs
from wintermute.kb.graph import Graph
from wintermute.kb.knowledgebase import KBFormatError, Knowledgebase
from wintermute.kb.ontology import ROOT, OntologyPack, OntologyRegistry
from wintermute.kb.ontology_packs import PACKS, packs_for
from wintermute.models import Course, CourseSurvey, Specialty, Thing


@pytest.fixture(scope="module")
def with_dbo():
    return make_registry(DBO_PACK)


def test_ancestry_is_root_first_and_ends_with_the_class(ontology):
    assert ontology.ancestors("schema:City") == ["schema:Thing", "schema:Place", "schema:AdministrativeArea", "schema:City"]
    assert ontology.ancestors("schema:Thing") == ["schema:Thing"]


def test_multiple_inheritance(ontology):
    assert ontology.ancestors("schema:LocalBusiness") == [
        "schema:Thing",
        "schema:Organization",
        "schema:Place",
        "schema:LocalBusiness",
    ]
    assert ontology.ancestors("Course")[-1] == "schema:Course"
    assert "schema:LearningResource" in ontology.ancestors("Course")


def test_name_forms_resolve_to_curies(ontology):
    assert ontology.resolve("City") == "schema:City"
    assert ontology.resolve("schema:City") == "schema:City"
    assert ontology.resolve("https://schema.org/City") == "schema:City"
    assert ontology.resolve("http://schema.org/City") == "schema:City"  # DBpedia's spelling
    assert ontology.resolve("urn:wintermute:CourseSurvey") == "wm:CourseSurvey"
    assert ontology.resolve("schema:birthPlace") == "schema:birthPlace"
    assert ontology.resolve("Bogus") is None
    assert ontology.resolve("nope:City") is None
    assert ontology.resolve("http://example.org/City") is None
    assert ontology.expand("wm:CourseSurvey") == "urn:wintermute:CourseSurvey"


def test_unknown_types_keep_shallow_labels(ontology):
    assert ontology.ancestors("schema:Bogus") == [ROOT, "schema:Bogus"]
    assert ontology.labels_for([]) == [ROOT]


def test_wm_pack_extends_schema(ontology):
    assert ontology.ancestors("wm:CourseSurvey") == ["schema:Thing", "schema:CreativeWork", "wm:CourseSurvey"]
    assert {p.curie for p in ontology.properties_of("wm:CourseSurvey")} >= {"wm:rating", "wm:workload", "schema:name"}
    assert ontology.properties["wm:rating"].ranges == ("schema:Number",)


def test_external_superclasses_and_individuals_are_ignored(ontology):
    assert ontology.classes["schema:BankAccount"].parents == ("schema:Thing",)
    assert "schema:Cardiovascular" not in ontology


def test_datatypes_are_known_but_not_labels(ontology):
    assert ontology.is_datatype("schema:Text")
    assert ontology.is_datatype("schema:URL")  # via subclass
    assert not ontology.is_datatype("schema:City")
    assert ontology.labels_for(["schema:City", "schema:URL"]) == ontology.ancestors("schema:City")


def test_properties(ontology):
    birth = ontology.properties["schema:birthPlace"]
    assert birth.domains == ("schema:Person",)
    assert birth.ranges == ("schema:Place",)
    assert birth.comment == "The place where the person was born."
    assert ontology.properties["schema:alternateName"].parents == ("schema:name",)
    city_props = {p.curie for p in ontology.properties_of("schema:City")}
    assert {"schema:containedInPlace", "schema:name"} <= city_props
    assert "schema:birthPlace" not in city_props


def test_equivalent_classes_share_ancestry(with_dbo):
    assert with_dbo.is_a("dbo:City", "schema:Place")
    assert with_dbo.is_a("dbo:City", "schema:AdministrativeArea")
    assert with_dbo.is_a("schema:City", "dbo:Settlement")
    assert with_dbo.ancestors("dbo:City")[-1] == "dbo:City"
    assert with_dbo.classes["schema:City"].equivalents == ("dbo:City",)
    # Asserted from the schema: side in the dbo pack.
    assert with_dbo.is_a("schema:Person", "dbo:Agent")
    assert with_dbo.properties["dbo:birthPlace"].domains == ("dbo:Person",)


def test_rootless_classes_hang_off_schema_thing(with_dbo):
    assert with_dbo.ancestors("dbo:TopicalConcept") == [ROOT, "dbo:TopicalConcept"]
    assert with_dbo.classes["dbo:Agent"].parents == (ROOT,)  # owl:Thing folds into the root


def test_conflicting_prefix_is_rejected():
    clash = OntologyPack(name="evil", prefixes={"schema": ["http://example.org/"]}, source="x.ttl")
    with pytest.raises(ValueError, match="Prefix 'schema'"):
        make_registry(clash)


def test_pack_dependencies():
    assert [p.name for p in OntologyRegistry([PACKS["wm"], SCHEMA_PACK]).packs] == ["schema", "wm"]
    with pytest.raises(ValueError, match="requires 'schema'"):
        OntologyRegistry([PACKS["wm"]])
    with pytest.raises(ValueError, match="Unknown ontology pack"):
        packs_for(["schema", "nope"])


def test_parsed_packs_are_cached(tmp_path, monkeypatch):
    first = make_registry(cache_dir=tmp_path)
    assert len(list((tmp_path / "ontologies").glob("*.json"))) == 2

    def no_parse(*args):
        raise AssertionError("cache miss")

    monkeypatch.setattr(ontology_mod, "_extract", no_parse)
    second = make_registry(cache_dir=tmp_path)
    assert second.classes == first.classes
    assert second.properties == first.properties


def test_unavailable_schema_pack_falls_back_to_stub(caplog):
    missing = dataclasses.replace(SCHEMA_PACK, source="/nonexistent/schema.ttl")
    registry = OntologyRegistry([missing, PACKS["wm"]])
    assert "Could not load ontology pack 'schema'" in caplog.text
    assert registry.resolve("Person") == "schema:Person"
    assert registry.ancestors("schema:Course") == [ROOT, "schema:Course"]
    # CreativeWork is in the stub, so this keeps its real parent.
    assert registry.ancestors("wm:CourseSurvey") == [ROOT, "schema:CreativeWork", "wm:CourseSurvey"]


# --- Models and the knowledge base ------------------------------------------------------------


def test_models_root_matches_registry():
    assert models.ROOT == ROOT


@pytest.mark.parametrize("cls", [Course, CourseSurvey, Specialty])
def test_model_types_are_defined_in_the_registry(ontology, cls):
    assert ontology.resolve(cls.type_name) == cls.type_name


def test_thing_ids_depend_only_on_most_specific_type():
    short = Course.create("CS 7637")
    full = Course(name="CS 7637", labels=["schema:Thing", "schema:CreativeWork", "schema:LearningResource", "schema:Course"])
    assert short.id == full.id
    assert short.id != Specialty.create("CS 7637").id


def test_save_expands_labels_and_from_record_dispatches(kb):
    survey = CourseSurvey.create("CS 9999", rating=4)
    kb.save_thing(survey)
    [found] = kb.find_topics("cs 9999", "schema:CreativeWork")
    assert isinstance(found, CourseSurvey)
    assert found.labels == ["schema:Thing", "schema:CreativeWork", "wm:CourseSurvey"]
    assert found.id == survey.id


def test_question_words_filter_by_type(agent, kb):
    kb.save_thing(Thing(name="Ada Lovelace", labels=[ROOT, "schema:Person"], props={"description": "A mathematician."}))
    ctx = "type-filter"
    assert "A mathematician." in agent.respond("who is ada lovelace?", ctx)
    assert "A mathematician." not in agent.respond("where is ada lovelace?", ctx)
    assert kb.find_topics("ada lovelace", "Person")  # bare names still work


def test_old_kb_format_is_rejected(tmp_path, ontology):
    graph = Graph(tmp_path / "old.lbdb")
    graph.execute("CREATE (:Thing {ID: 'x', name: 'CS 7637', labels: ['Thing', 'Course'], props: '{}'})")
    settings = Settings(db_path=tmp_path / "old.lbdb", cache_dir=tmp_path, remote_lookups=False)
    with pytest.raises(KBFormatError, match="wintermute init"):
        Knowledgebase(settings, graph=graph, ontology=ontology)
    graph.close()


def test_new_kb_is_stamped_with_format(tmp_path, ontology):
    graph = Graph(tmp_path / "new.lbdb")
    settings = Settings(db_path=tmp_path / "new.lbdb", cache_dir=tmp_path, remote_lookups=False)
    kb = Knowledgebase(settings, graph=graph, ontology=ontology)
    kb.save_thing(Course.create("CS 1000"))
    Knowledgebase(settings, graph=graph, ontology=ontology)  # reopening is fine
    kb.close()


# --- DBpedia ontology pack and label maintenance ----------------------------------------------


def test_most_specific_type_ends_up_last(with_dbo):
    # Remote sources list types in arbitrary order.
    labels = with_dbo.labels_for(["dbo:Settlement", "schema:Place", "dbo:PopulatedPlace"])
    assert labels[-1] == "dbo:Settlement"
    assert with_dbo.labels_for(["dbo:Person", "dbo:Agent"])[-1] == "dbo:Person"
    assert with_dbo.labels_for(["schema:City", "schema:Thing"])[-1] == "schema:City"


def test_dbo_types_satisfy_schema_question_filters(with_dbo):
    assert with_dbo.is_a("dbo:Settlement", "schema:Place")
    assert with_dbo.is_a("dbo:Person", "schema:Person")


def test_default_packs_include_dbpedia_ontology(monkeypatch):
    monkeypatch.delenv("WINTERMUTE_ONTOLOGIES", raising=False)
    monkeypatch.delenv("WINTERMUTE_ONTOLOGY_MANIFEST", raising=False)
    assert [p.name for p in ontology_packs.select_packs(Settings())] == ["schema", "wm", "dbo"]
    monkeypatch.setenv("WINTERMUTE_ONTOLOGIES", "schema, wm")
    assert Settings().ontologies == ["schema", "wm"]
    dbo = PACKS["dbo"]
    assert dbo.requires == ("schema",)
    assert dbo.prefixes == {"dbo": ["http://dbpedia.org/ontology/"]}
    assert ontology_packs.DBPEDIA_ONTOLOGY_COMMIT in dbo.source  # pinned, not a branch


def test_fingerprint_tracks_packs_and_fallbacks(ontology, with_dbo):
    assert ontology.fingerprint.startswith("schema@")
    assert "dbo@" in with_dbo.fingerprint and "dbo@" not in ontology.fingerprint
    stubbed = OntologyRegistry([dataclasses.replace(SCHEMA_PACK, source="/nonexistent.ttl"), PACKS["wm"]])
    assert stubbed.fingerprint.startswith("schema@stub,")


def test_stored_labels_are_refreshed_when_packs_change(tmp_path, ontology, with_dbo):
    settings = Settings(db_path=tmp_path / "kb.lbdb", cache_dir=tmp_path, remote_lookups=False)
    graph = Graph(settings.db_path)
    kb = Knowledgebase(settings, graph=graph, ontology=ontology)
    city = Thing(name="Atlanta", labels=["schema:City"], id="http://dbpedia.org/resource/Atlanta")
    kb.save_thing(city)
    course = Course.create("CS 1000")
    kb.save_thing(course)
    assert "dbo:City" not in kb.find_topics("atlanta")[0].labels

    kb = Knowledgebase(settings, graph=graph, ontology=with_dbo)
    [found] = kb.find_topics("atlanta", "dbo:Settlement")
    assert found.labels[-1] in ("schema:City", "dbo:City")
    assert {"dbo:City", "dbo:PopulatedPlace"} <= set(found.labels)
    assert kb.find_topics("cs 1000")[0].id == course.id
    status = [r["k"] for r in graph.rows("MATCH (s:KBStatus) RETURN s.key AS k") if r["k"].startswith("ontology=")]
    assert status == [f"ontology={with_dbo.fingerprint}"]
    graph.close()
