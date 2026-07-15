import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from xml.etree import ElementTree as ET

from replik_monitor.client import (DEFAULT_ENDPOINT, REPLIK_NS, ReplikSoapClient,
                                   build_ico_page_envelope, parse_ico_page)


def page_xml(rows, total):
    entries = "".join(f"<KonanieInfo><Id>{ident}</Id><SpisovaZnackaSpravcu>K/{ident}</SpisovaZnackaSpravcu><SpisovaZnackaSudu></SpisovaZnackaSudu><Dlznik>Company {ident}</Dlznik><DlznikIco>{ico}</DlznikIco><PoslednaUdalost>{event}</PoslednaUdalost><DatumPoslednejUdalosti>{date}</DatumPoslednejUdalosti><StavKonania>open</StavKonania></KonanieInfo>" for ident, ico, date, event in rows)
    return ("<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'><soap:Body>"
            f"<getKonaniePodlaICOResponse><KonanieInfoList>{entries}</KonanieInfoList><VysledkovCelkom>{total}</VysledkovCelkom>"
            "</getKonaniePodlaICOResponse></soap:Body></soap:Envelope>").encode()


class OfficialSoapContractTests(unittest.TestCase):
    def test_ico_envelope_has_official_paged_document_literal_fields(self):
        root = ET.fromstring(build_ico_page_envelope("47251301", 3, 100))
        request = next(item for item in root.iter() if item.tag.endswith("getKonaniePodlaICORequest"))
        self.assertEqual(f"{{{REPLIK_NS}}}getKonaniePodlaICORequest", request.tag)
        self.assertEqual(["47251301", "3", "100", "DatumPoslednejUdalosti"], [child.text for child in request])

    def test_client_reconciles_multiple_ico_pages_over_500_without_global_noise(self):
        client = ReplikSoapClient()
        rows = [(str(index), "47251301", "2026-06-01", f"event {index}") for index in range(501)]
        requests = []
        def fake_post(envelope):
            page = int(next(item.text for item in ET.fromstring(envelope).iter() if item.tag.endswith("Stranka")))
            requests.append(page)
            return page_xml(rows[page * 100:(page + 1) * 100], 501)
        with patch.object(client, "_post", side_effect=fake_post):
            result = client.fetch_changes("47251301", datetime(2026, 1, 1, tzinfo=UTC), 100)
        self.assertEqual(501, result.response_count)
        self.assertEqual(501, len(result.changes))
        self.assertEqual([0, 1, 2, 3, 4, 5] * 2, requests)
        self.assertEqual("https://replik.justice.sk/ru-verejnost-web/pages/konanieDetail.xhtml?konanieId=0", result.changes[0].url)

    def test_exact_ico_and_same_proceeding_newer_public_state_marker(self):
        first, _ = parse_ico_page(page_xml([("42", "47251301", "2026-06-01", "old"), ("noise", "999", "2026-06-01", "ignore")], 2), "47251301")
        second, _ = parse_ico_page(page_xml([("42", "47251301", "2026-06-02", "new")], 1), "47251301")
        self.assertEqual(["42"], [item.source_id for item in first])
        self.assertNotEqual(first[0].change_marker, second[0].change_marker)
        self.assertGreater(second[0].changed_at, first[0].changed_at)

    def test_client_rejects_same_total_snapshot_with_shifted_page_boundaries(self):
        client = ReplikSoapClient()
        collections = [(('a',), ('c',)), (('b',), ('d',))] * 3
        requests = []

        def fake_post(envelope):
            page = int(next(item.text for item in ET.fromstring(envelope).iter() if item.tag.endswith("Stranka")))
            collection = len(requests) // 2
            requests.append(page)
            ident = collections[collection][page][0]
            return page_xml([(ident, "47251301", "2026-06-01", f"event {ident}")], 2)

        with patch.object(client, "_post", side_effect=fake_post):
            with self.assertRaisesRegex(RuntimeError, "did not stabilize"):
                client.fetch_changes("47251301", datetime(2026, 1, 1, tzinfo=UTC), 1)
        self.assertEqual([0, 1] * 6, requests)

    def test_post_uses_verified_endpoint_and_wsdl_empty_soap_action(self):
        class Response:
            def read(self): return b"ok"
            def __enter__(self): return self
            def __exit__(self, *unused): return False
        client = ReplikSoapClient()
        with patch("replik_monitor.client.urlopen", return_value=Response()) as opener:
            self.assertEqual(b"ok", client._post(b"<test/>"))
        request = opener.call_args.args[0]
        self.assertEqual(DEFAULT_ENDPOINT, request.full_url)
        self.assertEqual('\"\"', request.get_header("Soapaction"))


if __name__ == "__main__":
    unittest.main()
