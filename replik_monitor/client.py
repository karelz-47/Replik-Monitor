"""SOAP 1.1 client for the official public IS REPLIK contract.

Proceeding discovery is deliberately IČO-scoped: getKonaniePodlaICO is paginated and
returns the published last-event date plus a current public proceeding state.  The
older global change feed cannot safely be filtered after its server-side cap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
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
MAX_RESULTS_PER_PAGE = 100
MAX_RECONCILIATION_ATTEMPTS = 3


@dataclass(frozen=True)
class FetchChangesResult:
    """All stable IČO-specific pages. response_count is VysledkovCelkom."""
    changes: list[Change]
    response_count: int


def _local_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    child = next((item for item in node.iter() if _local_name(item) == name), None)
    return child.text.strip() if child is not None and child.text else ""


def _direct_children(node: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in node if _local_name(item) == name]


def _parse_date(value: str) -> datetime:
    # KonanieInfo exposes xsd:date, not a timestamp.  The marker below also contains
    # its complete published state, so same-day amendments are still reconciled.
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def build_ico_page_envelope(ico: str, page: int, results_per_page: int, sort: str = "DatumPoslednejUdalosti") -> bytes:
    if not ico or not ico.isdigit():
        raise ValueError("REPLIK Ico must be a non-empty numeric identifier")
    if page < 0:
        raise ValueError("REPLIK Stranka must be non-negative")
    if not 1 <= results_per_page <= MAX_RESULTS_PER_PAGE:
        raise ValueError("REPLIK VysledkovNaStranku must be 1..100")
    if sort not in {"Relevancia", "DatumZacatiaKonania", "DatumPoslednejUdalosti"}:
        raise ValueError("unsupported REPLIK TypTriedenia")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:rep="{REPLIK_NS}">'
        f'<soap:Body><rep:getKonaniePodlaICORequest>'
        f'<rep:Ico>{escape(ico)}</rep:Ico><rep:Stranka>{page}</rep:Stranka>'
        f'<rep:VysledkovNaStranku>{results_per_page}</rep:VysledkovNaStranku>'
        f'<rep:TypTriedenia>{sort}</rep:TypTriedenia>'
        f'</rep:getKonaniePodlaICORequest></soap:Body></soap:Envelope>'
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


def _state_marker(info: ET.Element) -> str:
    # Canonical public fields make an amendment observable where the contract's
    # DatumPoslednejUdalosti xsd:date alone has insufficient granularity.
    fields = ("Id", "SpisovaZnackaSpravcu", "SpisovaZnackaSudu", "Typ", "DatumZacatiaKonania",
              "DatumZacatiaProcesu", "DatumUkonceniaProcesu", "DovodUkonceniaProcesu",
              "DatumPodania", "Dlznik", "DlznikIco", "DlznikDatumNarodenia", "PoslednaUdalost",
              "DatumPoslednejUdalosti", "Sud", "SudNazov", "Spravca", "TypSpravcu",
              "TypPrideleniaSpravcu", "StavKonania")
    canonical = "\x1f".join(_text(info, field) for field in fields)
    return sha256(canonical.encode("utf-8")).hexdigest()


def parse_ico_page(xml: bytes, expected_ico: str, portal_url: str = DEFAULT_PORTAL_URL) -> tuple[list[Change], int]:
    payload = _body_payload(xml)
    if _local_name(payload) != "getKonaniePodlaICOResponse":
        raise ValueError(f"unexpected REPLIK response: {_local_name(payload)}")
    total_text = _text(payload, "VysledkovCelkom")
    try:
        total = int(total_text)
    except ValueError as exc:
        raise ValueError("REPLIK response lacks valid VysledkovCelkom") from exc
    if total < 0:
        raise ValueError("REPLIK VysledkovCelkom must not be negative")
    lists = _direct_children(payload, "KonanieInfoList")
    infos = [info for listing in lists for info in _direct_children(listing, "KonanieInfo")]
    changes: list[Change] = []
    for info in infos:
        source_id, debtor_ico, changed = _text(info, "Id"), _text(info, "DlznikIco"), _text(info, "DatumPoslednejUdalosti")
        if not (source_id and changed):
            raise ValueError("REPLIK KonanieInfo lacks Id or DatumPoslednejUdalosti")
        # The operation is scoped, but enforce its returned ICO too; a server-side
        # mismatch must never enter this monitor.
        if debtor_ico != expected_ico:
            continue
        case_number = _text(info, "SpisovaZnackaSpravcu") or _text(info, "SpisovaZnackaSudu") or source_id
        title = " — ".join(part for part in (case_number, _text(info, "Dlznik")) if part) or "REPLIK proceeding"
        changes.append(Change(source_id, expected_ico, _parse_date(changed), title,
                              portal_url.rstrip("/") + "/" + DETAIL_PATH + quote(source_id, safe=""), _state_marker(info)))
    return changes, total


class ReplikSoapClient:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, portal_url: str = DEFAULT_PORTAL_URL):
        self.endpoint = endpoint.rstrip("/") + "/"
        self.portal_url = portal_url.rstrip("/") + "/"

    def _post(self, envelope: bytes) -> bytes:
        request = Request(self.endpoint, data=envelope, headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'}, method="POST")
        with urlopen(request, timeout=30) as response:
            return response.read()

    def fetch_changes(self, ico: str, since: datetime, max_results: int = MAX_RESULTS_PER_PAGE) -> FetchChangesResult:
        # since is retained in the interface for checkpoint scheduling. The contract's
        # IČO operation has no time cursor, therefore every poll reconciles its pages.
        del since
        for _attempt in range(MAX_RECONCILIATION_ATTEMPTS):
            first, total = parse_ico_page(self._post(build_ico_page_envelope(ico, 0, max_results)), ico, self.portal_url)
            pages = [first]
            for page in range(1, (total + max_results - 1) // max_results):
                rows, page_total = parse_ico_page(self._post(build_ico_page_envelope(ico, page, max_results)), ico, self.portal_url)
                if page_total != total:
                    break
                pages.append(rows)
            else:
                flattened = [change for page_rows in pages for change in page_rows]
                # Duplicate IDs across a stable snapshot signal a page-boundary race.
                if len({change.source_id for change in flattened}) == len(flattened) and len(flattened) == total:
                    return FetchChangesResult(flattened, total)
        raise RuntimeError("REPLIK IČO pagination did not stabilize after bounded reconciliation")
