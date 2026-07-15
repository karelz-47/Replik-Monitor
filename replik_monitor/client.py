"""Small SOAP client. Endpoint/action/schema details stay configuration-bound."""
from datetime import UTC, datetime
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from .domain import Change


SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"


def _text(node: ET.Element, name: str) -> str:
    child = next((c for c in node.iter() if c.tag.rsplit("}", 1)[-1] == name), None)
    return child.text.strip() if child is not None and child.text else ""


def parse_changes(xml: bytes, company_ico: str) -> list[Change]:
    root = ET.fromstring(xml)
    records = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] in {"Record", "Zaznam", "Change"}]
    output = []
    for record in records:
        source_id = _text(record, "Id") or _text(record, "ID")
        changed = _text(record, "ChangedAt") or _text(record, "DatumZmeny")
        link = _text(record, "Url") or _text(record, "URL")
        if not (source_id and changed and link):
            continue
        parsed = datetime.fromisoformat(changed.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Replik change timestamp must include a timezone offset")
        output.append(Change(source_id, company_ico, parsed.astimezone(UTC), _text(record, "Title") or source_id, link))
    return output


class ReplikSoapClient:
    def __init__(self, endpoint: str, soap_action: str = ""):
        self.endpoint, self.soap_action = endpoint, soap_action

    def fetch_changes(self, ico: str, since: datetime) -> list[Change]:
        envelope = f'''<soap:Envelope xmlns:soap="{SOAP_NS}"><soap:Body><GetChanges><Ico>{ico}</Ico><Since>{since.isoformat()}</Since></GetChanges></soap:Body></soap:Envelope>'''.encode()
        headers = {"Content-Type": "text/xml; charset=utf-8"}
        if self.soap_action:
            headers["SOAPAction"] = self.soap_action
        request = Request(self.endpoint, data=envelope, headers=headers, method="POST")
        with urlopen(request, timeout=30) as response:
            return parse_changes(response.read(), ico)
