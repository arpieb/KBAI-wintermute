import dataclasses
from pathlib import Path

import pytest

from wintermute.agent import Agent
from wintermute.config import Settings
from wintermute.kb import Knowledgebase
from wintermute.kb.loaders import COURSE_ALIASES
from wintermute.kb.ontology import OntologyPack, OntologyRegistry
from wintermute.kb.ontology_packs import PACKS
from wintermute.models import Course

FIXTURE_ONTOLOGIES = Path(__file__).parent / "fixtures" / "ontologies"
# The real Schema.org pack, pointed at a small local slice of it.
SCHEMA_PACK = dataclasses.replace(PACKS["schema"], source=str(FIXTURE_ONTOLOGIES / "schema-mini.ttl"), format="turtle")
DBO_PACK = OntologyPack(
    name="dbo",
    prefixes={"dbo": ["http://dbpedia.org/ontology/"]},
    source=str(FIXTURE_ONTOLOGIES / "dbo-mini.ttl"),
    requires=("schema",),
)


def make_registry(*extra: OntologyPack, cache_dir: Path | None = None) -> OntologyRegistry:
    """Registry over the fixture Schema.org slice, the real wm pack, and any extra packs."""
    return OntologyRegistry([SCHEMA_PACK, PACKS["wm"], *extra], cache_dir=cache_dir)


@pytest.fixture(scope="session")
def ontology():
    return make_registry()

COURSES = {
    "CS 7637": ("Knowledge-Based AI", "Structured knowledge representation. More text."),
    "CS 6601": ("Artificial Intelligence", "Basic concepts and methods of artificial intelligence."),
    "CS 7641": ("Machine Learning", "Machine learning techniques and applications."),
}


@pytest.fixture(scope="session")
def kb(tmp_path_factory, ontology):
    """A KB with the real FrameNet data and a few fixture courses. No network access."""
    tmp = tmp_path_factory.mktemp("kb")
    cache = tmp / "cache"
    cache.mkdir()
    settings = Settings(db_path=tmp / "test.lbdb", cache_dir=cache, remote_lookups=False, google_api_key=None)
    kb = Knowledgebase(settings, ontology=ontology)
    for key in ("courses", "course_surveys"):
        kb.graph.mark_loaded(key)
    for name, (alt, descr) in COURSES.items():
        course = Course.create(name, alternateName=alt, description=descr, department="CS", courseCode=name.split()[1])
        kb.save_thing(course)
        kb.add_alias(course.get("courseCode"), course)
        for alias in COURSE_ALIASES.get(name, []):
            kb.add_alias(alias, course)
    kb.initialize()
    yield kb
    kb.close()


@pytest.fixture
def agent(kb):
    return Agent(kb, debug=True)
