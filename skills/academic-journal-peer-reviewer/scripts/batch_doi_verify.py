#!/usr/bin/env python3
"""Look up Crossref registration metadata using the Python standard library.

Run only for DOIs authorized for the current task. Each DOI is sent to the
explicit Crossref endpoint below; no manuscript text is accepted or uploaded.
Metadata retrieval does not compare a bibliography or check citation support.

Usage: python batch_doi_verify.py --doi-list dois.txt --output results.json
       python batch_doi_verify.py --doi "https://doi.org/10.5555/example"

Exit codes: 0 = all lookups completed (including Crossref-not-found),
1 = at least one invalid input or lookup error, 2 = CLI/file operation error.
"""

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


CROSSREF_API = "https://api.crossref.org/works/"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RETRY_DELAY = 5.0


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


# Redirects are refused so a DOI or remote response cannot choose another host.
CROSSREF_HTTP = build_opener(_NoRedirect())


def normalize_doi(value):
    """Accept a bare DOI, a DOI prefix, or a doi.org/dx.doi.org resolver URL."""
    if not isinstance(value, str):
        raise ValueError("DOI must be text")
    doi = value.strip()
    if re.match(r"^https?://", doi, flags=re.IGNORECASE):
        parsed = urlsplit(doi)
        if (parsed.netloc.lower() not in ("doi.org", "dx.doi.org")
                or parsed.query or parsed.fragment):
            raise ValueError("Use a bare DOI or an unambiguous doi.org resolver URL")
        doi = unquote(parsed.path.lstrip("/"), errors="strict")
    else:
        doi = re.sub(r"^doi:\s*", "", doi, count=1, flags=re.IGNORECASE)
    if (len(doi) > 2048 or re.fullmatch(r"10\.[0-9]{4,9}/\S+", doi) is None
            or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in doi)):
        raise ValueError("Expected one DOI in the form 10.<registrant>/<suffix>, without whitespace")
    return doi


def _retry_delay(headers, attempt):
    fallback = min(0.5 * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
    value = headers.get("Retry-After") if headers else None
    if value is None:
        return fallback
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            return fallback
    except (ValueError, TypeError):
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            seconds = (stamp - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    return min(max(seconds, 0.0), MAX_RETRY_DELAY)


def _metadata(payload, doi):
    if (not isinstance(payload, dict) or payload.get("status") != "ok"
            or payload.get("message-type") != "work"
            or not isinstance(payload.get("message"), dict)):
        raise ValueError("Crossref returned an unexpected work-response structure")
    data = payload["message"]
    returned_doi = data.get("DOI")
    if not isinstance(returned_doi, str) or returned_doi.casefold() != doi.casefold():
        raise ValueError("Crossref response DOI does not match the requested DOI")
    titles = data.get("title")
    if not isinstance(titles, list) or not titles or not all(isinstance(x, str) for x in titles) or not titles[0].strip():
        raise ValueError("Crossref work record has no usable title")
    authors = data.get("author", [])
    if not isinstance(authors, list) or not all(isinstance(author, dict) for author in authors):
        raise ValueError("Crossref work record has malformed author metadata")
    names = []
    for author in authors:
        parts = [author.get("given", ""), author.get("family", "")]
        if not all(isinstance(part, str) for part in parts):
            raise ValueError("Crossref author names must be strings")
        name = " ".join(part for part in parts if part).strip()
        if not name and isinstance(author.get("name"), str):
            name = author["name"]
        if name:
            names.append(name)
    journal = data.get("container-title", [])
    if not isinstance(journal, list) or not all(isinstance(x, str) for x in journal):
        raise ValueError("Crossref work record has malformed container metadata")
    year = None
    for field in ("published-print", "published-online", "issued"):
        date = data.get(field, {})
        if isinstance(date, dict):
            parts = date.get("date-parts", [])
            if (isinstance(parts, list) and parts and isinstance(parts[0], list)
                    and parts[0] and type(parts[0][0]) is int):
                year = str(parts[0][0])
                break
    return {"title": titles[0], "authors": ", ".join(names) or None,
            "journal": journal[0] if journal else None, "year": year,
            "volume": data.get("volume"), "issue": data.get("issue"),
            "page": data.get("page"), "url": "https://doi.org/" + quote(doi, safe="/")}


def verify_doi(doi, *, timeout=10.0, max_attempts=3, opener=None, sleep=None):
    """Retrieve metadata, returning process states without inferring authenticity."""
    result = {"doi": doi, "source": "Crossref", "endpoint": CROSSREF_API,
              "status": "invalid-input", "attempts": 0, "work_exists": None,
              "bibliography_compared": False, "claim_checked": False}
    try:
        doi = normalize_doi(doi)
        result["doi"] = doi
        if (type(max_attempts) is not int or not 1 <= max_attempts <= 5
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 120):
            raise ValueError("timeout must be in (0, 120] seconds and max_attempts in [1, 5]")
    except ValueError as error:
        result["error"] = str(error)
        return result

    open_request = opener if opener is not None else CROSSREF_HTTP.open
    wait = sleep if sleep is not None else time.sleep
    request = Request(CROSSREF_API + quote(doi, safe=""), headers={
        "Accept": "application/json", "User-Agent": "PeerReviewDOILookup/1.1"})
    result["status"] = "lookup-error"
    for attempt in range(1, max_attempts + 1):
        result["attempts"] = attempt
        retry_headers = None
        try:
            with open_request(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Crossref response exceeds the 2 MiB size limit")
            metadata = _metadata(json.loads(raw), doi)
            result.update(metadata)
            result.pop("error", None)
            result["status"] = "metadata-only"
            result["scope"] = "Crossref registration metadata; no field comparison or claim assessment"
            return result
        except HTTPError as error:
            retry_headers = error.headers
            error.close()
            if error.code == 404:
                result["status"] = "crossref-not-found"
                result["error"] = "Crossref returned no record; work existence remains undetermined"
                return result
            result["error"] = f"Crossref HTTP {error.code}"
            if error.code != 429 and not 500 <= error.code <= 599:
                return result
        except (TimeoutError, socket.timeout, URLError, ConnectionError, OSError) as error:
            # Only transient transport failures and 429/5xx are retried.
            result["error"] = f"Crossref transport error: {error}"
        except (ValueError, UnicodeError, TypeError) as error:
            result["error"] = f"Crossref response error: {error}"
            return result
        if attempt < max_attempts:
            wait(_retry_delay(retry_headers, attempt))
    return result


def _output_path(value, input_path):
    destination = Path(value).expanduser().resolve()
    if input_path is not None:
        if destination == input_path or (destination.exists() and os.path.samefile(destination, input_path)):
            raise ValueError("Output must not overwrite the DOI input file or an alias to it")
    if not destination.parent.is_dir():
        raise ValueError("Output directory does not exist")
    if destination.exists() and not destination.is_file():
        raise ValueError("Output path must be a file")
    return destination


def _write_atomic(destination, results):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=destination.parent, prefix="." + destination.name + ".",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(results, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--doi", help="One bare DOI, DOI: prefix, or doi.org URL")
    group.add_argument("--doi-list", help="UTF-8 text file containing one DOI per nonempty line")
    parser.add_argument("--output", help="Write UTF-8 JSON atomically; cannot be the DOI input file")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout, maximum 120 seconds")
    parser.add_argument("--max-attempts", type=int, default=3, help="Attempts per DOI, from 1 to 5")
    args = parser.parse_args(argv)
    try:
        input_path = Path(args.doi_list).expanduser().resolve() if args.doi_list else None
        if input_path is None:
            dois = [args.doi]
        else:
            dois = [line.strip() for line in input_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        if not dois:
            raise ValueError("The DOI input file contains no nonempty lines")
        destination = _output_path(args.output, input_path) if args.output else None
    except (OSError, ValueError, RuntimeError, UnicodeError) as error:
        print(f"Input/output error: {error}", file=sys.stderr)
        return 2

    results = [verify_doi(doi, timeout=args.timeout, max_attempts=args.max_attempts) for doi in dois]
    try:
        if destination is not None:
            _write_atomic(destination, results)
        # ASCII JSON also works in legacy Windows consoles; the file stays UTF-8.
        print(json.dumps(results, ensure_ascii=True, indent=2))
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Output error: {error}", file=sys.stderr)
        return 2
    return 1 if any(row["status"] in ("lookup-error", "invalid-input") for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
