#!/usr/bin/env python3
"""Fetch Govee's public product catalogue and refresh spec/govee_catalog.json (dev only).

Uses the app's catalogue endpoint, public like fetch_scenes.py's scene library (no account, only an
AppVersion header):
    GET https://app2.govee.com/bi/rest/devices/v3/skus
(ICategoriesApi; the app feeds every product's goodsType to Pact.j, CategoriesDataManager.n). The
snapshot keeps each product's sku, goodsType and ic only, with no names or URLs, and the date it was
fetched. The file's doc and doc-ref are spec prose: the tool keeps them and rewrites only `fetched` and
`skus`. tools/gen_devices.py reads the committed snapshot; no build and no runtime code runs this tool.

    python3 tools/fetch_catalog.py                                  # fetch, then refresh the snapshot
    python3 tools/fetch_catalog.py --from saved.json --fetched 2026-10-18   # a saved response instead
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import urllib.request
from typing import Any

_TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS))  # sibling fetch_scenes: the same public endpoint handling
from fetch_scenes import (  # noqa: E402 - needs the sys.path entry above
    DEFAULT_APP_VERSION,
    MAX_RESPONSE_BYTES,
    validate_sku,
    write_catalog,
)

CATALOGUE_URL = "https://app2.govee.com/bi/rest/devices/v3/skus"
OUT = _TOOLS.parent / "spec" / "govee_catalog.json"


def fetch(app_version: str = DEFAULT_APP_VERSION) -> Any:
    """The endpoint's JSON response."""
    req = urllib.request.Request(CATALOGUE_URL, headers={"AppVersion": app_version})
    # Fixed https CATALOGUE_URL, no variable part.
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"catalogue response exceeds {MAX_RESPONSE_BYTES} bytes; refusing")
    return json.loads(raw)


def products(payload: Any) -> dict[str, dict[str, int]]:
    """``{SKU: {"goodsType": g, "ic": i}}`` from a response (categories > supportGroups > supportSkuList >
    products), sorted by SKU. ValueError on a non-200 status, a bad value, or a SKU listed twice with
    different values."""
    if not isinstance(payload, dict) or payload.get("status") != 200:
        raise ValueError(f"catalogue status {payload.get('status') if isinstance(payload, dict) else None!r}")
    out: dict[str, dict[str, int]] = {}
    for category in payload.get("categories") or ():
        for group in category.get("supportGroups") or ():
            for item in group.get("supportSkuList") or ():
                for product in item.get("products") or ():
                    sku = validate_sku(str(product.get("sku")))
                    entry = {key: product.get(key) for key in ("goodsType", "ic")}
                    for key, value in entry.items():
                        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                            raise ValueError(f"{sku}: {key} {value!r} is not a non-negative int")
                    if out.setdefault(sku, entry) != entry:
                        raise ValueError(f"{sku} is listed twice: {out[sku]} and {entry}")
    if not out:
        raise ValueError("the catalogue lists no products")
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", type=pathlib.Path, help="a saved endpoint response")
    ap.add_argument("--fetched", default=datetime.date.today().isoformat(),
                    help="the fetch date written to the snapshot (default today)")
    ap.add_argument("--app-version", default=os.environ.get("GOVEE_APP_VERSION", DEFAULT_APP_VERSION),
                    help=f"AppVersion header (env GOVEE_APP_VERSION; default {DEFAULT_APP_VERSION})")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args(argv)
    datetime.date.fromisoformat(args.fetched)
    payload = json.loads(args.source.read_text(encoding="utf-8")) if args.source else fetch(args.app_version)
    skus = products(payload)
    snapshot = json.loads(args.out.read_text(encoding="utf-8")) if args.out.is_file() else {}
    write_catalog(args.out, {**snapshot, "fetched": args.fetched, "skus": skus})
    print(f">> {args.out}: {len(skus)} SKUs, fetched {args.fetched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
