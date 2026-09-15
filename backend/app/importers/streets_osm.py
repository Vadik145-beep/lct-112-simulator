"""Builds data/seed/streets.json (street, okrug, district) from OpenStreetMap.

Source: OpenStreetMap via the Overpass API, data © OpenStreetMap contributors, ODbL 1.0
(docs/LIBRARIES.md). The seed file is committed, so the running system never touches the
network; this script is a one-off preparation step with internet access:

    uv run --group dataset python -m app.importers.streets_osm

Two requests: boundaries of Moscow okrugs (admin_level 5) and districts / settlements
(admin_level 8) with geometry, and every named road inside the Moscow bounding box with its
centre point. Roads are then assigned to a district locally (point in polygon), and each
district to an okrug the same way. A street that crosses districts appears once per district.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO_DIR = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_DIR / "data" / "seed" / "streets.json"
# Public Overpass instances; the next one is tried when an instance is busy or blocks us.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
USER_AGENT = "dds112-trainer-dataset/1.0"
REQUEST_TIMEOUT_SECONDS = 600
# Bounding box of Moscow including the new territories (south, west, north, east).
MOSCOW_BBOX = "55.14,36.80,56.02,37.97"
# Service roads, tracks and paths are not addresses; keep what people call a street.
ADDRESS_HIGHWAYS = (
    "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|"
    "pedestrian|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
)

# Boundaries are taken by bounding box, not by the Moscow area: the Zelenograd exclave is not
# found through the area. Region settlements that fall into the box are dropped later because
# they lie in no okrug.
BOUNDARIES_QUERY = f"""
[out:json][timeout:300];
(
  relation({MOSCOW_BBOX})["boundary"="administrative"]["admin_level"="5"];
  relation({MOSCOW_BBOX})["boundary"="administrative"]["admin_level"="8"];
);
out geom;
"""
ROADS_QUERY = f"""
[out:json][timeout:300];
way({MOSCOW_BBOX})["highway"~"^({ADDRESS_HIGHWAYS})$"]["name"];
out center tags;
"""


def _post(url: str, query: str, timeout: float) -> tuple[int, str]:
    """POSTs a query. ``curl`` is preferred: on Windows the Python TLS stack fails to
    handshake with overpass-api.de, while curl (bundled with Windows and Git) works."""
    curl = shutil.which("curl")
    if curl:
        result = subprocess.run(  # noqa: S603 - fixed executable, arguments are ours
            [
                curl,
                "-sS",
                "--max-time",
                str(int(timeout)),
                "-H",
                f"User-Agent: {USER_AGENT}",
                "--data-binary",
                "@-",
                "-w",
                "\n%{http_code}",
                url,
            ],
            input=query.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ConnectionError(result.stderr.decode("utf-8", "replace").strip())
        body, _, status = result.stdout.decode("utf-8", "replace").rpartition("\n")
        return int(status or 0), body
    response = httpx.post(
        url, content=query.encode("utf-8"), headers={"User-Agent": USER_AGENT}, timeout=timeout
    )
    return response.status_code, response.text


def overpass(query: str, cache: Path | None = None) -> list[dict]:
    """Runs a query; results are cached on disk so an interrupted run resumes."""
    key = hashlib.sha1(query.encode("utf-8")).hexdigest()  # noqa: S324 - cache key only
    cache_file = cache / f"{key}.json" if cache else None
    if cache_file and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    last_error = ""
    for attempt in range(6):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        try:
            status, body = _post(url, query, REQUEST_TIMEOUT_SECONDS)
        except (httpx.HTTPError, ConnectionError, OSError) as exc:
            last_error = f"{url}: {exc}"
        else:
            if status == 200:
                elements = json.loads(body)["elements"]
                if cache_file:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(elements, ensure_ascii=False), "utf-8")
                return elements
            last_error = f"{url}: {status} {body[:200]}"
        # 429 / 504 / broken connection: the instance is busy, wait and try the next one.
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Overpass не ответил: {last_error}")


def short_okrug(name: str) -> str:
    """«Северо-Западный административный округ» → «СЗАО»."""
    words = name.replace("административный округ", "").strip()
    if words == "Зеленоградский":
        return "ЗелАО"
    parts = [p for p in words.replace("-", " ").split() if p]
    return "".join(p[0].upper() for p in parts) + "АО"


def relation_polygon(relation: dict):
    """Assembles the outer rings of an administrative boundary into one (multi)polygon."""
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    lines = [
        LineString([(p["lon"], p["lat"]) for p in member["geometry"]])
        for member in relation.get("members", [])
        if member.get("type") == "way"
        and member.get("role") in ("outer", "")
        and len(member.get("geometry", [])) >= 2
    ]
    if not lines:
        return None
    polygons = list(polygonize(unary_union(lines)))
    return unary_union(polygons) if polygons else None


def build(cache: Path | None) -> list[dict]:
    from shapely import STRtree
    from shapely.geometry import Point

    boundaries = overpass(BOUNDARIES_QUERY, cache)
    roads = overpass(ROADS_QUERY, cache)
    print(f"Границ: {len(boundaries)}, дорог с названием: {len(roads)}", flush=True)

    okrugs, districts = [], []
    for relation in boundaries:
        polygon = relation_polygon(relation)
        name = relation.get("tags", {}).get("name")
        if polygon is None or not name:
            continue
        level = relation["tags"].get("admin_level")
        (okrugs if level == "5" else districts).append((name, polygon))
    okrug_tree = STRtree([p for _, p in okrugs])
    district_tree = STRtree([p for _, p in districts])

    def locate(tree, items, point):
        for index in tree.query(point, predicate="within"):
            return items[index][0]
        return None

    district_okrug = {
        name: locate(okrug_tree, okrugs, polygon.representative_point())
        for name, polygon in districts
    }
    print(f"Округов: {len(okrugs)}, районов и поселений: {len(districts)}", flush=True)

    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for way in roads:
        center = way.get("center")
        name = way.get("tags", {}).get("name", "").strip()
        if not center or not name:
            continue
        district = locate(district_tree, districts, Point(center["lon"], center["lat"]))
        if district is None or district_okrug.get(district) is None:
            continue  # outside Moscow (the bounding box covers parts of the region)
        if (name, district) in seen:
            continue
        seen.add((name, district))
        okrug_full = district_okrug[district]
        rows.append(
            {
                "name": name,
                "okrug": short_okrug(okrug_full),
                "okrug_full": okrug_full,
                "district": district,
            }
        )
    rows.sort(key=lambda r: (r["name"].lower(), r["okrug"], r["district"]))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Улицы Москвы с округом и районом из OSM")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "dds112-overpass-cache",
        help="папка кэша ответов Overpass (для продолжения прерванного запуска)",
    )
    args = parser.parse_args(argv)
    rows = build(args.cache)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(
        f"Записей: {len(rows)}, уникальных названий: {len({r['name'] for r in rows})} → {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
