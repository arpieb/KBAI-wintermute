from unittest import mock

import requests

from wintermute.kb.lookup import DBpediaLookup, _sparql_literal
from wintermute.kb.schema_org import SchemaOrg


def schema(tmp_path):
    s = SchemaOrg.__new__(SchemaOrg)
    s.parents = {"Thing": [], "Place": ["Thing"], "City": ["Place"], "Person": ["Thing"]}
    return s


def test_sparql_literal_escapes_quotes_and_backslashes():
    assert _sparql_literal('a "b" \\ c\nd') == '"a \\"b\\" \\\\ c d"@en'


def test_dbpedia_results_become_labeled_things(tmp_path):
    payload = {
        "results": {
            "bindings": [
                {
                    "s": {"value": "http://dbpedia.org/resource/Atlanta"},
                    "label": {"value": "Atlanta"},
                    "description": {"value": "capital city of Georgia, United States"},
                    "types": {"value": "http://schema.org/City http://dbpedia.org/ontology/Settlement"},
                }
            ]
        }
    }
    resp = mock.Mock(json=lambda: payload, raise_for_status=lambda: None)
    with mock.patch("wintermute.kb.lookup.requests.get", return_value=resp) as get:
        [thing] = DBpediaLookup(schema(tmp_path), timeout=1).search("atlanta")
    assert '"Atlanta"@en' in get.call_args.kwargs["params"]["query"]
    assert thing.id == "http://dbpedia.org/resource/Atlanta"
    assert thing.labels == ["Thing", "Place", "City"]
    assert thing.get_response() == "capital city of Georgia, United States"


def test_dbpedia_disables_itself_after_failure(tmp_path):
    lookup = DBpediaLookup(schema(tmp_path), timeout=1)
    with mock.patch("wintermute.kb.lookup.requests.get", side_effect=requests.ConnectionError("down")) as get:
        assert lookup.search("x") == []
        assert lookup.search("y") == []
    assert get.call_count == 1
