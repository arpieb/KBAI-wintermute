import json

import pytest

from wintermute.agent import Agent
from wintermute.config import Settings
from wintermute.kb import Knowledgebase
from wintermute.kb.loaders import COURSE_ALIASES
from wintermute.models import Course

COURSES = {
    "CS 7637": ("Knowledge-Based AI", "Structured knowledge representation. More text."),
    "CS 6601": ("Artificial Intelligence", "Basic concepts and methods of artificial intelligence."),
    "CS 7641": ("Machine Learning", "Machine learning techniques and applications."),
}


@pytest.fixture(scope="session")
def kb(tmp_path_factory):
    """A KB with the real FrameNet data and a few fixture courses. No network access."""
    tmp = tmp_path_factory.mktemp("kb")
    cache = tmp / "cache"
    cache.mkdir()
    # Minimal Schema.org hierarchy so nothing is downloaded.
    (cache / "schemaorg-parents.json").write_text(
        json.dumps({"Thing": [], "Place": ["Thing"], "City": ["Place"], "Person": ["Thing"], "CreativeWork": ["Thing"]})
    )
    settings = Settings(db_path=tmp / "test.lbdb", cache_dir=cache, remote_lookups=False, google_api_key=None)
    kb = Knowledgebase(settings)
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
