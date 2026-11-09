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
    resp = _resp(200, payload)
    with mock.patch("wintermute.kb.lookup.requests.get", return_value=resp) as get:
        [thing] = DBpediaLookup(schema(tmp_path), timeout=1).search("atlanta")
    assert '"Atlanta"@en' in get.call_args.kwargs["params"]["query"]
    assert thing.id == "http://dbpedia.org/resource/Atlanta"
    assert thing.labels == ["Thing", "Place", "City"]
    assert thing.get_response() == "capital city of Georgia, United States"


def _resp(status, payload=None):
    return mock.Mock(status_code=status, ok=status < 400, json=lambda: payload)


EMPTY = {"results": {"bindings": []}}


@mock.patch("wintermute.kb.lookup.time.sleep")
def test_dbpedia_retries_transient_errors(sleep, tmp_path):
    lookup = DBpediaLookup(schema(tmp_path), timeout=1)
    responses = [_resp(500), requests.Timeout("slow"), _resp(200, EMPTY)]
    with mock.patch("wintermute.kb.lookup.requests.get", side_effect=responses) as get:
        assert lookup.search("alan turing") == []
    assert get.call_count == 3
    assert lookup.paused_until == 0.0


@mock.patch("wintermute.kb.lookup.time.sleep")
def test_dbpedia_pauses_after_repeated_failures_then_recovers(sleep, tmp_path, caplog):
    lookup = DBpediaLookup(schema(tmp_path), timeout=1)
    with mock.patch("wintermute.kb.lookup.requests.get", return_value=_resp(500)) as get:
        assert lookup.search("x") == []
        assert lookup.search("y") == []  # paused: no request made
    assert get.call_count == DBpediaLookup.ATTEMPTS
    assert "HTTP 500 after 3 attempts" in caplog.text
    assert "sparql?" not in caplog.text  # no query-string dump in the log

    lookup.paused_until = 0.0  # cooldown elapsed
    with mock.patch("wintermute.kb.lookup.requests.get", return_value=_resp(200, EMPTY)) as get:
        lookup.search("z")
    assert get.call_count == 1


@mock.patch("wintermute.kb.lookup.time.sleep")
def test_dbpedia_does_not_retry_client_errors(sleep, tmp_path):
    lookup = DBpediaLookup(schema(tmp_path), timeout=1)
    with mock.patch("wintermute.kb.lookup.requests.get", return_value=_resp(400)) as get:
        assert lookup.search("x") == []
    assert get.call_count == 1
