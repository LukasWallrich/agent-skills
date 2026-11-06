"""Find papers in the user's Zotero library and add to it, through Zotero's local API.

The local API needs Zotero 10+, running on this machine, with Settings > Advanced >
"Allow other applications on this computer to communicate with Zotero" switched on.
Reads need no key. Writes need a key the user grants once through a dialog.

`find_available_pdf` asks Zotero's own Find Available PDF to look for a file, which needs
the Find PDF API plugin in `zotero-plugin/`.
"""

import hashlib
import json
import os
import re
import sys
import tempfile
from urllib.parse import unquote, urlparse

import requests

BASE = "http://127.0.0.1:23119/api"
KEY_FILE = os.path.expanduser("~/.claude/zotero_local_key.json")
INBOX = "Agent inbox"
TAG = "agent-added"
NEEDS_PDF_TAG = "needs-pdf"
NON_PAPER_TYPES = {"attachment", "note", "annotation"}
AUTHORIZE_WAIT = 300        # seconds the user has to answer Zotero's dialog
FIND_PDF_PATH = "/find-pdf-api/v1/find"
FIND_PDF_WAIT = 300         # seconds; longer than the plugin's own 240, so its answer arrives
FIND_PDF_STATES = {"found", "none", "not-eligible", "item-not-found", "busy", "timeout",
                   "local-api-off", "forbidden", "bad-request", "unsupported", "error"}
#: CSL type -> Zotero item type. Anything else is treated as a journal article.
ITEM_TYPES = {
    "journal-article": "journalArticle", "article-journal": "journalArticle",
    "article": "preprint", "posted-content": "preprint",
    "chapter": "bookSection", "book-chapter": "bookSection",
    "book": "book", "monograph": "book", "edited-book": "book",
    "paper-conference": "conferencePaper", "proceedings-article": "conferencePaper",
    "report": "report", "dataset": "dataset", "thesis": "thesis", "dissertation": "thesis",
}
CONTAINER_FIELDS = ("publicationTitle", "bookTitle", "proceedingsTitle", "repository")
PUBLISHER_FIELDS = ("publisher", "institution", "university", "repository")


def log(msg):
    print(msg, file=sys.stderr)


class ZoteroError(Exception):
    pass


def normal_doi(doi):
    doi = re.sub(r"^\s*(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi or "", flags=re.I)
    return doi.strip().lower()


def normal_title(title):
    return "".join(c for c in re.sub(r"<[^>]+>", "", title or "").casefold() if c.isalnum())


def item_has_doi(data, doi):
    if normal_doi(data.get("DOI")) == doi:
        return True
    return any(normal_doi(found) == doi
               for found in re.findall(r"^\s*DOI:\s*(\S+)", data.get("extra") or "", re.I | re.M))


def csl_year(csl):
    for field in ("issued", "published", "published-online", "published-print", "created"):
        parts = (csl.get(field) or {}).get("date-parts") or [[]]
        if parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def first_text(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def csl_title(csl):
    title = first_text(csl.get("title"))
    subtitle = first_text(csl.get("subtitle"))
    return f"{title}: {subtitle}" if subtitle and subtitle.lower() not in title.lower() else title


def csl_to_item(csl, doi, allowed_fields, allowed_creators):
    """A Zotero item from doi.org's CSL-JSON, holding only fields its type allows."""
    item_type = ITEM_TYPES.get(csl.get("type"), "journalArticle")
    parts = next(((csl.get(f) or {}).get("date-parts") or [[]] for f in
                  ("issued", "published", "published-online", "published-print", "created")
                  if (csl.get(f) or {}).get("date-parts")), [[]])
    date = "-".join(f"{int(p):02d}" if i else str(int(p)) for i, p in enumerate(parts[0]) if p)
    fields = {
        "title": csl_title(csl), "abstractNote": first_text(csl.get("abstract")), "date": date,
        "volume": csl.get("volume"), "issue": csl.get("issue"), "pages": csl.get("page"),
        "ISSN": first_text(csl.get("ISSN")), "ISBN": first_text(csl.get("ISBN")),
        "url": csl.get("URL") or f"https://doi.org/{doi}",
        "language": csl.get("language"), "libraryCatalog": "DOI.org",
    }
    item = {"itemType": item_type}
    for name, value in fields.items():
        if value and name in allowed_fields:
            item[name] = str(value)
    container = first_text(csl.get("container-title"))
    target = next((f for f in CONTAINER_FIELDS if f in allowed_fields), None)
    if container and target:
        item[target] = container
    publisher = first_text(csl.get("publisher"))
    target = next((f for f in PUBLISHER_FIELDS if f in allowed_fields and f not in item), None)
    if publisher and target:
        item[target] = publisher
    if "DOI" in allowed_fields:
        item["DOI"] = doi
    else:
        item["extra"] = f"DOI: {doi}"
    creators = []
    for role in ("author", "editor"):
        if role not in allowed_creators:
            continue
        for person in csl.get(role) or []:
            if person.get("family"):
                creators.append({"creatorType": role, "firstName": person.get("given", ""),
                                 "lastName": person["family"]})
            elif person.get("name") or person.get("literal"):
                creators.append({"creatorType": role, "name": person.get("name") or person["literal"]})
    item["creators"] = creators
    return item


def pdf_filename(item):
    creators = item.get("creators") or []
    who = (creators[0].get("lastName") or creators[0].get("name") or "") if creators else ""
    if len(creators) > 1:
        who += " et al."
    parts = [who, (item.get("date") or "")[:4], (item.get("title") or "paper")[:80]]
    name = " - ".join(part for part in parts if part)
    return re.sub(r'[/\\:*?"<>|\s]+', " ", name).strip() + ".pdf"


class ZoteroLocal:
    def __init__(self, base=BASE, key_file=KEY_FILE):
        self.base = base
        self.key_file = key_file
        self.server_id = ""
        self.session = requests.Session()
        self.session.trust_env = False  # never route loopback through a proxy

    @classmethod
    def connect(cls, **kwargs):
        """A client, or None with a reason logged when the API cannot be reached."""
        client = cls(**kwargs)
        try:
            response = client.session.get(f"{client.base}/users/0/items",
                                          params={"limit": 1, "format": "json"}, timeout=2)
        except requests.RequestException:
            log("Zotero is not reachable on this machine, skipping the library check")
            return None
        if response.status_code == 403:
            log("Zotero's local API is switched off (Settings > Advanced), skipping the library check")
            return None
        if not response.ok:
            log(f"Zotero's local API answered {response.status_code}, skipping the library check")
            return None
        client.server_id = response.headers.get("Zotero-Server-ID", "")
        return client

    # ---------------- reads ----------------
    def _get(self, path, **params):
        response = self.session.get(f"{self.base}/{path}", params=params, timeout=30)
        response.raise_for_status()
        return response

    def _all(self, path, **params):
        """Walk API pages so matching items or collections are not silently missed."""
        start = 0
        while True:
            page = self._get(path, **{**params, "limit": 100, "start": start}).json()
            yield from page
            if len(page) < 100:
                return
            start += len(page)

    def libraries(self):
        """Personal library first, then each group, as API path prefixes."""
        yield "users/0"
        for group in self._all("users/0/groups", format="json"):
            yield f"groups/{group['id']}"

    def find(self, doi, library="users/0"):
        """The items in one library carrying this DOI. Full-text matches are citing papers."""
        doi = normal_doi(doi)
        found = self._all(f"{library}/items", q=doi, qmode="everything", format="json")
        return [item for item in found
                if item["data"]["itemType"] not in NON_PAPER_TYPES and item_has_doi(item["data"], doi)]

    def find_by_title(self, title, year, library="users/0"):
        """An item with this exact title and a year within two, so a preprint matches its article."""
        wanted = normal_title(title)
        if not wanted:
            return None
        for item in self._all(f"{library}/items", q=title[:200], format="json"):
            data = item["data"]
            if data["itemType"] in NON_PAPER_TYPES or normal_title(data.get("title")) != wanted:
                continue
            match = re.search(r"\d{4}", data.get("date") or "")
            if not year or not match or abs(int(match.group()) - year) <= 2:
                return item
        return None

    def pdfs(self, item_key, library="users/0"):
        """Paths on disk of an item's PDF attachments, skipping files not on this machine."""
        paths = []
        for child in self._all(f"{library}/items/{item_key}/children", format="json"):
            data = child["data"]
            if data.get("contentType") != "application/pdf" or data.get("linkMode") == "linked_url":
                continue
            path = self.file_path(child["key"], library)
            if path:
                paths.append(path)
        return paths

    def file_path(self, attachment_key, library="users/0"):
        """Where an attachment's file is on this machine, or None."""
        response = self.session.get(f"{self.base}/{library}/items/{attachment_key}/file/view/url", timeout=30)
        if response.ok and response.text.startswith("file://"):
            path = unquote(urlparse(response.text.strip()).path)
            if os.path.exists(path):
                return path
        return None

    def library_pdfs(self, doi):
        """Every PDF of this DOI in any library, personal first, as (library, path)."""
        for library in self.libraries():
            for item in self.find(doi, library):
                for path in self.pdfs(item["key"], library):
                    yield library, path

    def status(self, doi):
        """(item key, has a PDF) in the personal library, or (None, False)."""
        items = self.find(doi)
        for item in items:
            if self.pdfs(item["key"]):
                return item["key"], True
        return (items[0]["key"], False) if items else (None, False)

    # ---------------- writes ----------------
    def _saved_key(self):
        try:
            with open(self.key_file) as f:
                keys = json.load(f)
                key = keys.get(self.server_id, "") if isinstance(keys, dict) else ""
                return key if isinstance(key, str) else ""
        except (OSError, ValueError):
            return ""

    def _authorize(self):
        log('Zotero is asking permission for "download-paper": choose "Always Allow" in its dialog')
        response = self.session.post(f"{self.base}/local/authorize", json={"appName": "download-paper"},
                                     headers={"Zotero-Server-ID": self.server_id}, timeout=AUTHORIZE_WAIT)
        if not response.ok:
            raise ZoteroError(f"write permission refused ({response.status_code})")
        grant = response.json()
        if not isinstance(grant, dict) or not isinstance(grant.get("key"), str) or not grant["key"]:
            raise ZoteroError("Zotero returned no local API key")
        if not grant.get("remember"):
            raise ZoteroError('Adding a paper takes several writes: choose "Always Allow" in the dialog')
        try:
            with open(self.key_file) as f:
                keys = json.load(f)
        except (OSError, ValueError):
            keys = {}
        keys = {**(keys if isinstance(keys, dict) else {}), self.server_id: grant["key"]}
        folder = os.path.dirname(os.path.abspath(self.key_file))
        os.makedirs(folder, mode=0o700, exist_ok=True)
        handle, part = tempfile.mkstemp(dir=folder, prefix=".zotero-key-")
        try:
            with os.fdopen(handle, "w") as f:
                json.dump(keys, f)
            os.replace(part, self.key_file)
        finally:
            if os.path.exists(part):
                os.unlink(part)
        return grant["key"]

    def _write(self, method, path, retry=True, **kwargs):
        key = self._saved_key() or self._authorize()
        headers = {"Zotero-Server-ID": self.server_id, "Zotero-API-Key": key, **kwargs.get("headers", {})}
        response = self.session.request(method, f"{self.base}/{path}", timeout=120,
                                        **{**kwargs, "headers": headers})
        if response.status_code == 401 and retry:
            self._authorize()
            return self._write(method, path, retry=False, **kwargs)
        if not response.ok:
            raise ZoteroError(f"{method} {path} answered {response.status_code}: {response.text[:200]}")
        return response

    def _create(self, path, obj):
        result = self._write("POST", path, json=[obj]).json()
        if result.get("failed"):
            raise ZoteroError(f"Zotero refused the object: {result['failed']}")
        return result["successful"]["0"]["key"]

    def inbox_key(self):
        for collection in self._all("users/0/collections", format="json"):
            if collection["data"]["name"] == INBOX and not collection["data"].get("parentCollection"):
                return collection["key"]
        return self._create("users/0/collections", {"name": INBOX})

    def create_item(self, csl, doi, has_pdf):
        item_type = ITEM_TYPES.get(csl.get("type"), "journalArticle")
        allowed = {f["field"] for f in self._get("itemTypeFields", itemType=item_type).json()}
        creators = {c["creatorType"] for c in self._get("itemTypeCreatorTypes", itemType=item_type).json()}
        item = csl_to_item(csl, doi, allowed, creators)
        item["collections"] = [self.inbox_key()]
        item["tags"] = [{"tag": TAG}] + ([] if has_pdf else [{"tag": NEEDS_PDF_TAG}])
        return self._create("users/0/items", item), item

    def attach(self, parent_key, pdf_path, filename):
        """Store a copy of the PDF on the item, using the local three-step upload."""
        with open(pdf_path, "rb") as f:
            data = f.read()
        mtime = int(os.path.getmtime(pdf_path) * 1000)
        attachment = self._create("users/0/items", {
            "itemType": "attachment", "parentItem": parent_key, "linkMode": "imported_file",
            "title": "PDF", "contentType": "application/pdf", "filename": filename,
            "tags": [{"tag": TAG}],
        })
        condition = {"If-None-Match": "*"}
        path = f"users/0/items/{attachment}/file"
        try:
            grant = self._write("POST", path, headers=condition, data={
                "md5": hashlib.md5(data).hexdigest(), "filename": filename, "filesize": len(data),
                "mtime": mtime,
            }).json()
            if not grant.get("exists"):
                response = self.session.post(grant["url"], data=data, timeout=300,
                                             headers={"Content-Type": grant.get("contentType", "application/pdf")})
                if response.status_code != 201:
                    raise ZoteroError(f"file upload answered {response.status_code}")
                self._write("POST", path, headers=condition, data={"upload": grant["uploadKey"]})
        except requests.RequestException as error:
            self.delete_item(attachment)
            # An upload grant URL may contain a temporary credential.
            raise ZoteroError(f"file upload failed ({type(error).__name__})") from None
        except Exception:
            self.delete_item(attachment)
            raise
        return attachment

    def delete_item(self, key):
        """Returns whether the item was removed."""
        try:
            version = self._get(f"users/0/items/{key}", format="json").json()["version"]
            self._write("DELETE", f"users/0/items/{key}", headers={"If-Unmodified-Since-Version": str(version)})
            return True
        except Exception as error:
            log(f"Could not remove Zotero item {key}: {error}")
            return False

    def remove_tag(self, key, tag):
        """Take one tag off an item. A failure is only logged."""
        try:
            item = self._get(f"users/0/items/{key}", format="json").json()
            tags = item["data"].get("tags", [])
            kept = [t for t in tags if t["tag"] != tag]
            if len(kept) != len(tags):
                self._write("PATCH", f"users/0/items/{key}", json={"tags": kept},
                            headers={"If-Unmodified-Since-Version": str(item["version"])})
        except Exception as error:
            log(f"Could not remove the {tag} tag from Zotero item {key}: {error}")

    def find_available_pdf(self, item_key):
        """Run Zotero's Find Available PDF on one personal-library item, through the plugin.

        Returns a dict whose "state" is one of FIND_PDF_STATES, or not-installed,
        zotero-closed or not-authorised. It never raises: the download's result stands
        whatever happens here. A file it reports as found is unverified until the caller
        has run the identity check.
        """
        url = self.base.removesuffix("/api") + FIND_PDF_PATH
        try:
            # A key is asked for only on a 401, which shows the plugin is there: without
            # it, Zotero answers 404 and the user sees no dialog for nothing.
            key = self._saved_key()
            for may_retry in (True, False):
                headers = {"Zotero-Server-ID": self.server_id, **({"Zotero-API-Key": key} if key else {})}
                response = self.session.post(url, json={"itemKey": item_key}, headers=headers,
                                             timeout=FIND_PDF_WAIT)
                if response.status_code != 401 or not may_retry:
                    break
                key = self._authorize()
        except ZoteroError as error:
            return {"state": "not-authorised", "message": str(error)}
        except requests.Timeout:
            return {"state": "timeout"}
        except requests.RequestException:
            return {"state": "zotero-closed"}
        except (OSError, ValueError) as error:
            return {"state": "not-authorised", "message": f"Could not save local authorization: {error}"}
        if response.status_code == 401:
            return {"state": "not-authorised", "message": "Zotero refused the saved key"}
        # Only Zotero's own 404 is plain text: the plugin always answers JSON.
        if response.status_code == 404 and "json" not in response.headers.get("Content-Type", ""):
            return {"state": "not-installed"}
        try:
            result = response.json()
        except ValueError:
            result = {}
        if (not isinstance(result, dict) or not isinstance(result.get("state"), str)
                or result["state"] not in FIND_PDF_STATES):
            return {"state": "error", "message": f"Zotero answered {response.status_code}: {response.text[:200]}"}
        return result

    def add_paper(self, doi, pdf_path, csl_lookup):
        """Put this paper in the personal library, without duplicating it.

        `pdf_path` may be None, which adds the record alone. `csl_lookup(doi)` returns
        CSL-JSON or None. Returns (action, item key), action being one of: already,
        attached, added, in-library-no-pdf.
        """
        doi = normal_doi(doi)
        items = self.find(doi)
        for item in items:
            if self.pdfs(item["key"]):
                return "already", item["key"]
        existing = items[0] if items else None
        csl = None
        if not existing:
            csl = csl_lookup(doi)
            if not csl:
                raise ZoteroError("no metadata from doi.org, so the item was not created")
            existing = self.find_by_title(csl_title(csl), csl_year(csl))
            if existing and self.pdfs(existing["key"]):
                return "already", existing["key"]
        if existing:
            if not pdf_path:
                return "in-library-no-pdf", existing["key"]
            self.attach(existing["key"], pdf_path, pdf_filename(existing["data"]))
            self.remove_tag(existing["key"], NEEDS_PDF_TAG)
            return "attached", existing["key"]
        key, item = self.create_item(csl, doi, bool(pdf_path))
        if pdf_path:
            try:
                self.attach(key, pdf_path, pdf_filename(item))
            except Exception:
                self.delete_item(key)
                raise
        return "added", key
