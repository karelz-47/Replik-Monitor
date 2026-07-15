import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from replik_monitor.client import (
    DEFAULT_ENDPOINT,
    FetchChangesResult,
    REPLIK_NS,
    ReplikSoapClient,
    build_changes_envelope,
    parse_detail,
    parse_last_changes,
)

FIXTURES = Path("fixtures")


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False


class OfficialSoapContractTests(unittest.TestCase):
    def test_sync_envelope_has_official_document_literal_request_and_bounded_limit(self):
        envelope = build_changes_envelope(datetime(2026, 6, 1, 10, tzinfo=UTC), 25)
        root = ET.fromstring(envelope)
        request = next(item for item in root.iter() if item.tag.endswith("vyhladajPoslednuZmenuOdRequest"))
        self.assertEqual(f"{{{REPLIK_NS}}}vyhladajPoslednuZmenuOdRequest", request.tag)
        self.assertEqual("2026-06-01T10:00:00Z", next(item.text for item in request if item.tag.endswith("ZmenyOd")))
        self.assertEqual("25", next(item.text for item in request if item.tag.endswith("MaximalnyPocetVysledkov")))

    def test_live_contract_shaped_fixtures_parse_timestamp_and_detail_metadata(self):
        changes = parse_last_changes((FIXTURES / "replik_last_changes.xml").read_bytes())
        self.assertEqual(["31415", "27182"], [item[0] for item in changes])
        self.assertEqual(datetime(2026, 6, 1, 10, tzinfo=UTC), changes[1][1])
        self.assertEqual(
            ("47251301", "1K/42/2026 — Example, s. r. o."),
            parse_detail((FIXTURES / "replik_detail_match.xml").read_bytes()),
        )

    def test_sync_fetches_detail_and_filters_by_debtor_ico(self):
        client = ReplikSoapClient()
        summaries = (FIXTURES / "replik_last_changes.xml").read_bytes()
        matching = (FIXTURES / "replik_detail_match.xml").read_bytes()
        other = (FIXTURES / "replik_detail_other.xml").read_bytes()

        def fake_post(envelope):
            if b"vyhladajPoslednuZmenuOdRequest" in envelope:
                return summaries
            return matching if b">31415<" in envelope else other

        with patch.object(client, "_post", side_effect=fake_post):
            result = client.fetch_changes("47251301", datetime(2026, 6, 1, tzinfo=UTC), 2)
        self.assertIsInstance(result, FetchChangesResult)
        self.assertEqual(2, result.response_count)
        self.assertEqual(["31415"], [item.source_id for item in result.changes])
        self.assertEqual("1K/42/2026 — Example, s. r. o.", result.changes[0].title)
        self.assertEqual("https://replik.justice.sk/ru-verejnost-web/pages/konanieDetail.xhtml?konanieId=31415", result.changes[0].url)

    def test_post_uses_verified_endpoint_and_wsdl_empty_soap_action(self):
        client = ReplikSoapClient()
        with patch("replik_monitor.client.urlopen", return_value=_Response(b"ok")) as opener:
            self.assertEqual(b"ok", client._post(b"<test/>"))
        request = opener.call_args.args[0]
        self.assertEqual(DEFAULT_ENDPOINT, request.full_url)
        self.assertEqual('""', request.get_header("Soapaction"))
        self.assertEqual("text/xml; charset=utf-8", request.get_header("Content-type"))


if __name__ == "__main__":
    unittest.main()
