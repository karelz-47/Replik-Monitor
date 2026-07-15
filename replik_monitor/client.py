"""SOAP 1.1 client for the official public IS REPLIK contract.

The public synchronisation operation returns only a proceeding ID and its last-change
instant.  A detail call is consequently required both for debtor ICO filtering and for
human-readable alert metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from .domain import Change

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
REPLIK_NS = "datatypes.konanie.verejnost.ru.sk.hp.com"
DEFAULT_ENDPOINT = "https://replik-ws.justice.sk/ru-verejnost-ws/"
DEFAULT_PORTAL_URL = "https://replik.justice.sk/ru-verejnost-web/"
DETAIL_PATH = "pages/konanieDetail.xhtml?konanieId="


@dataclass(frozen=True)
class FetchChangesResult:
    """Filtered changes plus the unfiltered authoritative sync response count."""
    changes: list[Change]
    response_count: int


def _local_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _text(node: ET.Element, name: str) -> str:
    child = next((item for item in node.iter() if _local_name(item) == name), None)
    return child.text.strip() if child is not None and child.text else ""


def _direct_child(node: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in node if _local_name(item) == name), None)


def _format_datetime(value: datetime) -> str:
    """Render an xsd:dateTime with an explicit UTC designator."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("REPLIK PoslednaZmena must include a timezone offset")
    return parsed.astimezone(UTC)


def build_changes_envelope(since: datetime, max_results: int) -> bytes:
    if not 1 <= max_results <= 500:
        raise ValueError("REPLIK MaximalnyPocetVysledkov must be 1..500")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:rep="{REPLIK_NS}">'
        f'<soap:Body><rep:vyhladajPoslednuZmenuOdRequest>'
        f'<rep:ZmenyOd>{_format_datetime(since)}</rep:ZmenyOd>'
        f'<rep:MaximalnyPocetVysledkov>{max_results}</rep:MaximalnyPocetVysledkov>'
        f'</rep:vyhladajPoslednuZmenuOdRequest></soap:Body></soap:Envelope>'
    ).encode("utf-8")


def build_detail_envelope(konanie_id: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:rep="{REPLIK_NS}">'
        f'<soap:Body><rep:getKonanieDetailRequest><rep:KonanieId>{escape(konanie_id)}</rep:KonanieId>'
        f'</rep:getKonanieDetailRequest></soap:Body></soap:Envelope>'
    ).encode("utf-8")


def _body_payload(xml: bytes) -> ET.Element:
    root = ET.fromstring(xml)
    fault = next((item for item in root.iter() if _local_name(item) == "Fault"), None)
    if fault is not None:
        raise ValueError(f"REPLIK SOAP fault: {_text(fault, 'faultstring') or 'unknown fault'}")
    body = next((item for item in root.iter() if _local_name(item) == "Body"), None)
    if body is None or not list(body):
        raise ValueError("REPLIK response has no SOAP body payload")
    return list(body)[0]


def parse_last_changes(xml: bytes) -> list[tuple[str, datetime]]:
    payload = _body_payload(xml)
    if _local_name(payload) != "vyhladajPoslednuZmenuOdResponse":
        raise ValueError(f"unexpected REPLIK response: {_local_name(payload)}")
    changes: list[tuple[str, datetime]] = []
    for item in payload.iter():
        if _local_name(item) != "PoslednaZmenaNaKonani":
            continue
        konanie_id = item.attrib.get("KonanieId", "").strip()
        changed_at = item.attrib.get("PoslednaZmena", "").strip()
        if not (konanie_id and changed_at):
            raise ValueError("REPLIK change item lacks KonanieId or PoslednaZmena")
        changes.append((konanie_id, _parse_datetime(changed_at)))
    return changes


def parse_detail(xml: bytes) -> tuple[str, str]:
    """Return (debtor_ico, stable human title) from getKonanieDetail."""
    payload = _body_payload(xml)
    if _local_name(payload) != "getKonanieDetailResponse":
        raise ValueError(f"unexpected REPLIK response: {_local_name(payload)}")
    konanie = next((item for item in payload.iter() if _local_name(item) == "Konanie"), None)
    if konanie is None:
        raise ValueError("REPLIK detail response lacks Konanie")
    debtor = _direct_child(konanie, "Dlznik")
    debtor_ico = _text(debtor, "Ico") if debtor is not None else ""
    case_number = _text(konanie, "SpisovaZnackaSpravcu") or _text(konanie, "SpisovaZnackaSudu") or _text(konanie, "Id")
    debtor_name = _text(debtor, "ObchodneMeno") if debtor is not None else ""
    if not debtor_name and debtor is not None:
        debtor_name = " ".join(part for part in (_text(debtor, "Meno"), _text(debtor, "Priezvisko")) if part)
    title = " — ".join(part for part in (case_number, debtor_name) if part) or "REPLIK proceeding"
    return debtor_ico, title


class ReplikSoapClient:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, portal_url: str = DEFAULT_PORTAL_URL):
        self.endpoint = endpoint.rstrip("/") + "/"
        self.portal_url = portal_url.rstrip("/") + "/"

    def _post(self, envelope: bytes) -> bytes:
        # WSDL binding declares a document/literal SOAP 1.1 operation with soapAction="".
        request = Request(
            self.endpoint,
            data=envelope,
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            return response.read()

    def fetch_changes(self, ico: str, since: datetime, max_results: int = 100) -> FetchChangesResult:
        summaries = parse_last_changes(self._post(build_changes_envelope(since, max_results)))
        # Preserve the unfiltered count. Filtering by debtor ICO cannot be used to
        # determine whether the upstream capped response may have been truncated.
        response_count = len(summaries)
        output: list[Change] = []
        for konanie_id, changed_at in summaries:
            debtor_ico, title = parse_detail(self._post(build_detail_envelope(konanie_id)))
            if debtor_ico != ico:
                continue
            # Verified against the official public portal's search-result links on
            # 2026-07-15: /pages/konanieDetail.xhtml?konanieId=<KonanieId>.
            output.append(Change(konanie_id, ico, changed_at, title, self.portal_url + DETAIL_PATH + quote(konanie_id, safe="")))
        return FetchChangesResult(output, response_count)
