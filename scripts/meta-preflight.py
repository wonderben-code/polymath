#!/usr/bin/env python3
"""Free, read-only launch preflight for Meta and open-literature access.

This script never requests a model completion, never sends a prompt and never
spends API credits. It reads configuration presence (never values), confirms
that the fixed external services answer, and, only with ``--check-key``, lists
the models visible to the configured Meta key. Listing models consumes no
tokens. Every request is a bounded GET without redirects; credentials travel
only in an Authorization header and are never printed.

Exit status: 0 when every requested check passed, 1 when a check failed and
2 for invalid usage.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

META_MODELS_URL = "https://api.meta.ai/v1/models"
OPENALEX_URL = "https://api.openalex.org/works?search=self-organized%20criticality&per-page=1"
ARXIV_URL = (
    "https://export.arxiv.org/api/query?search_query=all:%22self-organized%20criticality%22"
    "&start=0&max_results=1"
)
ZENODO_SANDBOX_URL = "https://sandbox.zenodo.org/api/records?size=1"
# The only destinations this script may contact. A completion endpoint is
# deliberately absent: preflight can never become a paid request.
ALLOWED_URLS = frozenset({META_MODELS_URL, OPENALEX_URL, ARXIV_URL, ZENODO_SANDBOX_URL})
MAX_BODY_BYTES = 1_048_576
TIMEOUT_SECONDS = 15
PREFERRED_MODEL_PREFIX = "muse-spark"

SPENDING_GATES = (
    "PAID_META_PROBE_ENABLED",
    "PAID_META_RESEARCH_ENABLED",
    "PUBLIC_META_RESEARCH_ENABLED",
    "PUBLICATION_EXECUTION_ENABLED",
)
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


Fetch = Callable[[str, Mapping[str, str]], Response]


@dataclass
class Report:
    passed: int = 0
    failed: int = 0
    warnings: int = 0

    def ok(self, message: str) -> None:
        self.passed += 1
        print(f"  [ok]   {message}")

    def fail(self, message: str) -> None:
        self.failed += 1
        print(f"  [FAIL] {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"  [warn] {message}")

    @staticmethod
    def info(message: str) -> None:
        print(f"  [info] {message}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # Honour an explicitly configured CA bundle (for example a corporate proxy).
    # Verification always stays enabled.
    for variable in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = os.environ.get(variable)
        if path and Path(path).is_file():
            context.load_verify_locations(cafile=path)
    return context


def http_fetch(url: str, headers: Mapping[str, str]) -> Response:
    if url not in ALLOWED_URLS:
        raise ValueError("destination is not on the preflight allowlist")
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=_ssl_context())
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "Polymath-preflight/1", "Accept": "*/*", **headers},
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return Response(response.status, response.read(MAX_BODY_BYTES + 1))
    except urllib.error.HTTPError as error:
        try:
            body = error.read(MAX_BODY_BYTES + 1)
        except OSError:
            body = b""
        return Response(error.code, body)


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE reader; values are kept in memory and never printed."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.split(" #", 1)[0].strip().strip("'\"")
        if key:
            values[key.upper()] = value
    return values


def load_config(environ: Mapping[str, str], dotenv: Mapping[str, str]) -> dict[str, str]:
    # Process settings win over .env, matching the application's precedence.
    config = {key.upper(): value for key, value in dotenv.items()}
    config.update({key.upper(): value for key, value in environ.items()})
    return config


def _present(config: Mapping[str, str], key: str) -> bool:
    return bool(config.get(key, "").strip())


def _describe_failure(status: int) -> str:
    if status == 0:
        return "no response (network, proxy or TLS failure)"
    if status in (401, 403):
        return f"HTTP {status}: access denied"
    if status == 429:
        return "HTTP 429: rate limited"
    return f"HTTP {status}"


def _safe_fetch(fetch: Fetch, url: str, headers: Mapping[str, str]) -> Response:
    try:
        return fetch(url, headers)
    except (OSError, ValueError, ssl.SSLError):
        # Never echo exception text: it can include request details.
        return Response(0, b"")


def check_configuration(config: Mapping[str, str], report: Report) -> None:
    print("Configuration (values are never shown)")
    if _present(config, "MODEL_API_KEY"):
        report.ok("MODEL_API_KEY is set")
    else:
        report.fail("MODEL_API_KEY is not set: add it in the environment settings, never in chat")
    model = config.get("META_MODEL", "").strip()
    if model:
        report.ok(f"META_MODEL is set to {model!r}")
    else:
        report.warn("META_MODEL is not set: choose it from the --check-key model list")
    if _present(config, "OPENALEX_API_KEY"):
        report.ok("OPENALEX_API_KEY is set (sent only as a Bearer header)")
    else:
        report.warn("OPENALEX_API_KEY is not set: the shared free OpenAlex budget can run out")
    enabled = [gate for gate in SPENDING_GATES if config.get(gate, "").lower() in TRUE_VALUES]
    if enabled:
        report.warn("spending gates already on: " + ", ".join(enabled))
    else:
        report.ok("every spending gate is off (nothing can spend credits)")


def check_network(config: Mapping[str, str], fetch: Fetch, report: Report) -> None:
    print("Network (free GET requests only)")
    meta = _safe_fetch(fetch, META_MODELS_URL, {})
    if meta.status == 401:
        report.ok("api.meta.ai answers (401 without a key, as expected)")
    elif meta.status == 200:
        report.warn("api.meta.ai listed models without a key; check the endpoint")
    else:
        report.fail(f"api.meta.ai: {_describe_failure(meta.status)}")

    arxiv = _safe_fetch(fetch, ARXIV_URL, {})
    if arxiv.status == 200 and b"<feed" in arxiv.body[:MAX_BODY_BYTES]:
        report.ok("arXiv search answers with an Atom feed")
    else:
        report.fail(f"arXiv: {_describe_failure(arxiv.status)}")

    headers = {}
    if _present(config, "OPENALEX_API_KEY"):
        headers["Authorization"] = "Bearer " + config["OPENALEX_API_KEY"].strip()
    openalex = _safe_fetch(fetch, OPENALEX_URL, headers)
    if openalex.status == 200:
        report.ok("OpenAlex search answers" + (" with the configured key" if headers else ""))
    elif openalex.status == 429 and not headers:
        report.warn("OpenAlex shared free budget is exhausted: set a free OPENALEX_API_KEY")
    else:
        report.fail(f"OpenAlex: {_describe_failure(openalex.status)}")

    zenodo = _safe_fetch(fetch, ZENODO_SANDBOX_URL, {})
    if zenodo.status == 200:
        report.ok("Zenodo sandbox answers")
    else:
        report.fail(f"Zenodo sandbox: {_describe_failure(zenodo.status)}")


def parse_model_ids(body: bytes) -> list[str] | None:
    if len(body) > MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    identifiers = []
    for entry in entries:
        identifier = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(identifier, str) and 0 < len(identifier) <= 200 and identifier.isprintable():
            identifiers.append(identifier)
    return sorted(set(identifiers))


def check_key(config: Mapping[str, str], fetch: Fetch, report: Report) -> None:
    print("Meta key (lists models; consumes no tokens)")
    key = config.get("MODEL_API_KEY", "").strip()
    if not key:
        report.fail("cannot check the key: MODEL_API_KEY is not set")
        return
    response = _safe_fetch(fetch, META_MODELS_URL, {"Authorization": f"Bearer {key}"})
    if response.status != 200:
        report.fail(f"authenticated model list: {_describe_failure(response.status)}")
        return
    models = parse_model_ids(response.body)
    if models is None:
        report.fail("model list response was not the expected JSON shape")
        return
    report.ok(f"key accepted; {len(models)} model(s) visible")
    for model in models:
        report.info(f"model: {model}")
    preferred = [model for model in models if model.lower().startswith(PREFERRED_MODEL_PREFIX)]
    if preferred:
        report.ok("Muse Spark is available: " + ", ".join(preferred))
    else:
        report.warn("no Muse Spark model listed; choose another capable Meta model")
    configured = config.get("META_MODEL", "").strip()
    if configured and configured in models:
        report.ok(f"META_MODEL {configured!r} is in the list")
    elif configured:
        report.fail(f"META_MODEL {configured!r} is not in the list")


def run(
    arguments: argparse.Namespace,
    environ: Mapping[str, str],
    fetch: Fetch = http_fetch,
) -> int:
    config = load_config(environ, read_dotenv(Path(arguments.env_file)))
    report = Report()
    print("Polymath launch preflight: no completions, no prompts, no credits spent.\n")
    check_configuration(config, report)
    if not arguments.offline:
        print()
        check_network(config, fetch, report)
    if arguments.check_key:
        print()
        check_key(config, fetch, report)
    print(f"\nSummary: {report.passed} passed, {report.warnings} warnings, {report.failed} failed.")
    if not arguments.check_key and not arguments.offline:
        print("Next: rerun with --check-key once MODEL_API_KEY is set to list available models.")
    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Free launch preflight. Never requests a completion or spends credits."
    )
    parser.add_argument(
        "--check-key",
        action="store_true",
        help="list models visible to MODEL_API_KEY (an authenticated GET; no tokens used)",
    )
    parser.add_argument("--offline", action="store_true", help="check configuration only")
    parser.add_argument("--env-file", default=".env", help="optional dotenv file (default .env)")
    arguments = parser.parse_args(argv)
    if arguments.offline and arguments.check_key:
        parser.error("--check-key needs network access")
    return run(arguments, os.environ)


if __name__ == "__main__":
    sys.exit(main())
