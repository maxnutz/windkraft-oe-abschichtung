#!/usr/bin/env python3
"""Create a filled DKM Benützungsarten overview from NÖ DXF linework + NS symbols.

Verschoben aus ``scripts/preprocessing/create_noe_dkm_polygon_fill_map.py``
(Paket W1.P2, docs/rewrite/PLAN.md §7 Spalte "Besitzt": "scripts/preprocessing/*"
- Umzug). Reiner Diagnose-/Visualisierungspfad, kein Teil der eigentlichen
GeoParquet-Erzeugung: ``pipeline.prep.kataster.a_noe_polygonize`` importiert
von hier nur die reinen DXF-/Geometrie-Hilfsfunktionen (unverändert). Dieses
Skript selbst erzeugt PNG-Übersichten unter ``OUT_DIR`` - kein Prep-,
Layer- oder Produktpfad im Sinne von ``pipeline.contract`` (deshalb bleibt
``OUT_DIR`` unterhalb von ``output/``, nicht ``derived/prep/``). Inputs kommen
seit dem Umzug aus ``pipeline.contract.RAW`` statt aus lokal
zusammengesetzten ``data/``-Pfaden (PLAN.md §8 Regel 2).

Inputs used:
- contract.RAW["kataster"]["noe_dxf_zip"]
- contract.RAW["kataster"]["symbol_csv"]

This renders a flexible-resolution overview raster. It is a visualization derived
from DKM polygons, not a legally exact cadastral vector export.
"""
from __future__ import annotations

import argparse
import csv
import math
import multiprocessing as mp
import os
import re
import sys
import time
import zipfile
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Iterable

try:
    import numpy as np
except Exception:  # pragma: no cover - optional speed-up
    np = None

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from PIL import Image, ImageDraw
from pyproj import Transformer
from shapely import set_precision
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

try:
    from rasterio.features import rasterize as rasterio_rasterize
    from rasterio.transform import from_origin as rasterio_from_origin
except Exception:  # pragma: no cover - optional fast render path
    rasterio_rasterize = None
    rasterio_from_origin = None

from pipeline import contract

ROOT = contract.ROOT
ZIP_PATH = contract.RAW["kataster"]["noe_dxf_zip"]
SYMBOL_CSV = contract.RAW["kataster"]["symbol_csv"]
ADMIN_BOUNDARY_PATH = contract.RAW["admin"]["vgd"]
# Bewusst kein contract.PREP/PRODUCTS-Pfad: reine Diagnoseausgabe (PNG/CSV),
# von keinem anderen Skript gelesen (siehe Moduldocstring).
OUT_DIR = ROOT / "output" / "kataster" / "diagnostics"

# NÖ DKM DXFs straddle Austrian GK strips. Raw coordinates with large
# positive X are M31/GK Central (EPSG:31255); the rest of the NÖ ZIP is
# M34/GK East (EPSG:31256). Both are rendered into Austria Lambert.
TRANSFORMERS_TO_LAMBERT = {
    "M31": Transformer.from_crs(31255, 31287, always_xy=True),
    "M34": Transformer.from_crs(31256, 31287, always_xy=True),
}

BACKGROUND_RGBA = (251, 250, 245, 255)
GAP_RGBA = (0, 190, 255, 120)
UNASSIGNED_RGBA = (255, 0, 255, 120)
UNASSIGNED_OUTLINE_RGBA = (180, 0, 180, 220)

CATEGORY_COLORS = {
    "Nicht zugeordnet": "#ff00ff",
    "Baufläche": "#c44e52",
    "Landwirtschaft": "#d7b23f",
    "Garten": "#9ccc65",
    "Weingarten": "#b58900",
    "Alpen": "#8c6bb1",
    "Wald": "#1b7f3a",
    "Gewaesser": "#4aa3df",
    "Verkehrsflaeche": "#6f6f6f",
    "Sonstige Nutzung": "#bdbdbd",
    "Unbekannt": "#000000",
}
DRAW_ORDER = [
    "Sonstige Nutzung", "Gewaesser", "Garten", "Landwirtschaft", "Weingarten",
    "Alpen", "Wald", "Verkehrsflaeche", "Baufläche", "Unbekannt",
]
RASTER_CATEGORIES = DRAW_ORDER + ["Nicht zugeordnet"]
RASTER_CATEGORY_IDS = {cat: idx + 1 for idx, cat in enumerate(RASTER_CATEGORIES)}


def category_base(cat: str) -> str:
    """Strip the ``(historisch)`` suffix from a category.

    Parameters
    ----------
    cat : str
        Category text from the symbol table.

    Returns
    -------
    str
        The trimmed category, or ``"Unbekannt"`` if empty.
    """
    cat = (cat or "").strip()
    return re.sub(r"\s*\(historisch\)\s*", "", cat).strip() or "Unbekannt"


def load_symbol_categories() -> dict[str, str]:
    """Map NS symbols to their base category.

    Reads the ``NS`` rows of ``SYMBOL_CSV``.

    Returns
    -------
    dict of str to str
        Symbol to category from ``category_base()``.
    """
    mapping: dict[str, str] = {}
    with SYMBOL_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if (row.get("layer") or "").strip() == "NS":
                mapping[(row.get("symbol") or "").strip()] = category_base(row.get("kategorie") or "")
    return mapping


def iter_pairs_from_bytes(raw: bytes):
    """Iterate over the group-code/value line pairs of a DXF file.

    Parameters
    ----------
    raw : bytes
        DXF content, decoded as latin-1.

    Yields
    ------
    tuple of str
        ``(code, value)``, both stripped; an unpaired last line is dropped.
    """
    lines = raw.decode("latin1", errors="replace").splitlines()
    it = iter(lines)
    for code in it:
        try:
            value = next(it)
        except StopIteration:
            break
        yield code.strip(), value.strip()


def parse_dxf_bounds_fast(raw: bytes, line_layers: set[str]):
    """Return raw DXF bounds for selected line layers and NS INSERT points.

    This streaming parser is intentionally narrower than ``parse_dxf``: it is
    used for the first pass only, where the script needs extents but not
    Shapely geometries. Avoiding ``pairs = list(...)`` saves memory and time on
    the 3k-file NÖ DKM ZIP.
    """
    pairs = iter_pairs_from_bytes(raw)
    bounds = [math.inf, math.inf, -math.inf, -math.inf]
    pending: tuple[str, str] | None = None

    def next_pair():
        nonlocal pending
        if pending is not None:
            pair = pending
            pending = None
            return pair
        return next(pairs, None)

    def push_pair(pair):
        nonlocal pending
        pending = pair

    def upd(x: float, y: float):
        bounds[0] = min(bounds[0], x); bounds[1] = min(bounds[1], y)
        bounds[2] = max(bounds[2], x); bounds[3] = max(bounds[3], y)

    pair = next_pair()
    while pair is not None:
        code, value = pair
        if code != "0":
            pair = next_pair()
            continue

        if value == "INSERT":
            layer = symbol = None
            x = y = None
            pair = next_pair()
            while pair is not None and pair[0] != "0":
                c, v = pair
                if c == "8": layer = v
                elif c == "2": symbol = v
                elif c == "10":
                    try: x = float(v)
                    except ValueError: pass
                elif c == "20":
                    try: y = float(v)
                    except ValueError: pass
                pair = next_pair()
            if layer == "NS" and symbol and x is not None and y is not None:
                upd(x, y)
            continue

        if value == "LWPOLYLINE":
            layer = None; pts = []; pending_x = None
            pair = next_pair()
            while pair is not None and pair[0] != "0":
                c, v = pair
                if c == "8": layer = v
                elif c == "10":
                    try: pending_x = float(v)
                    except ValueError: pending_x = None
                elif c == "20" and pending_x is not None:
                    try: pts.append((pending_x, float(v)))
                    except ValueError: pass
                    pending_x = None
                pair = next_pair()
            if layer in line_layers:
                for x, y in pts:
                    upd(x, y)
            continue

        if value == "LINE":
            layer = None; x0 = y0 = x1 = y1 = None
            pair = next_pair()
            while pair is not None and pair[0] != "0":
                c, v = pair
                if c == "8": layer = v
                elif c == "10":
                    try: x0 = float(v)
                    except ValueError: pass
                elif c == "20":
                    try: y0 = float(v)
                    except ValueError: pass
                elif c == "11":
                    try: x1 = float(v)
                    except ValueError: pass
                elif c == "21":
                    try: y1 = float(v)
                    except ValueError: pass
                pair = next_pair()
            if layer in line_layers and None not in (x0, y0, x1, y1):
                upd(x0, y0)
                upd(x1, y1)
            continue

        if value == "POLYLINE":
            layer = None; pts = []
            pair = next_pair()
            while pair is not None:
                c, v = pair
                if c == "0":
                    if v == "VERTEX":
                        break
                    if v == "SEQEND":
                        pair = next_pair()
                        break
                    break
                if c == "8": layer = v
                pair = next_pair()
            while pair == ("0", "VERTEX"):
                vx = vy = None
                pair = next_pair()
                while pair is not None and pair[0] != "0":
                    c, v = pair
                    if c == "10":
                        try: vx = float(v)
                        except ValueError: pass
                    elif c == "20":
                        try: vy = float(v)
                        except ValueError: pass
                    pair = next_pair()
                if vx is not None and vy is not None:
                    pts.append((vx, vy))
            if pair == ("0", "SEQEND"):
                pair = next_pair()
            if layer in line_layers:
                for x, y in pts:
                    upd(x, y)
            continue

        pair = next_pair()

    return None if bounds[0] == math.inf else tuple(bounds)


def parse_dxf(raw: bytes, line_layers: set[str], bounds_only: bool = False):
    """Return (lines, ns_points, bounds) from a DXF byte string.

    When bounds_only is true, avoid constructing Shapely geometries and NS
    point lists. The first pass only needs extents; skipping geometry creation
    saves substantial CPU on the 3k-file NÖ DKM ZIP.
    """
    if bounds_only:
        return [], [], parse_dxf_bounds_fast(raw, line_layers)

    pairs = list(iter_pairs_from_bytes(raw))
    n = len(pairs)
    lines: list[LineString] = []
    points: list[tuple[str, float, float]] = []
    bounds = [math.inf, math.inf, -math.inf, -math.inf]

    def upd(x: float, y: float):
        bounds[0] = min(bounds[0], x); bounds[1] = min(bounds[1], y)
        bounds[2] = max(bounds[2], x); bounds[3] = max(bounds[3], y)

    def add_line(pts: list[tuple[float, float]], closed: bool):
        if len(pts) < 2:
            return
        if closed and pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        if bounds_only:
            for x, y in pts:
                upd(x, y)
            return
        try:
            ls = LineString(pts)
            if not ls.is_empty and ls.length > 0:
                lines.append(ls)
                for x, y in pts:
                    upd(x, y)
        except Exception:
            return

    i = 0
    while i < n:
        code, value = pairs[i]
        if code != "0":
            i += 1
            continue

        if value == "INSERT":
            layer = symbol = None
            x = y = None
            i += 1
            while i < n and pairs[i][0] != "0":
                c, v = pairs[i]
                if c == "8": layer = v
                elif c == "2": symbol = v
                elif c == "10":
                    try: x = float(v)
                    except ValueError: pass
                elif c == "20":
                    try: y = float(v)
                    except ValueError: pass
                i += 1
            if layer == "NS" and symbol and x is not None and y is not None:
                if not bounds_only:
                    points.append((symbol, x, y))
                upd(x, y)
            continue

        if value == "LWPOLYLINE":
            layer = None; closed = False; pts = []; pending_x = None
            i += 1
            while i < n and pairs[i][0] != "0":
                c, v = pairs[i]
                if c == "8": layer = v
                elif c == "70":
                    try: closed = bool(int(float(v)) & 1)
                    except ValueError: pass
                elif c == "10":
                    try: pending_x = float(v)
                    except ValueError: pending_x = None
                elif c == "20" and pending_x is not None:
                    try: pts.append((pending_x, float(v)))
                    except ValueError: pass
                    pending_x = None
                i += 1
            if layer in line_layers:
                add_line(pts, closed)
            continue

        if value == "LINE":
            layer = None
            x0 = y0 = x1 = y1 = None
            i += 1
            while i < n and pairs[i][0] != "0":
                c, v = pairs[i]
                if c == "8": layer = v
                elif c == "10":
                    try: x0 = float(v)
                    except ValueError: pass
                elif c == "20":
                    try: y0 = float(v)
                    except ValueError: pass
                elif c == "11":
                    try: x1 = float(v)
                    except ValueError: pass
                elif c == "21":
                    try: y1 = float(v)
                    except ValueError: pass
                i += 1
            if layer in line_layers and None not in (x0, y0, x1, y1):
                add_line([(x0, y0), (x1, y1)], False)
            continue

        if value == "POLYLINE":
            layer = None; closed = False; pts = []
            i += 1
            # Header until first VERTEX/SEQEND/entity.
            while i < n:
                c, v = pairs[i]
                if c == "0":
                    if v == "VERTEX":
                        break
                    if v == "SEQEND":
                        i += 1
                        break
                    # Unexpected next entity: no vertices.
                    break
                if c == "8": layer = v
                elif c == "70":
                    try: closed = bool(int(float(v)) & 1)
                    except ValueError: pass
                i += 1
            # Vertices until SEQEND.
            while i < n and pairs[i] == ("0", "VERTEX"):
                i += 1
                vx = vy = None
                while i < n and pairs[i][0] != "0":
                    c, v = pairs[i]
                    if c == "10":
                        try: vx = float(v)
                        except ValueError: pass
                    elif c == "20":
                        try: vy = float(v)
                        except ValueError: pass
                    i += 1
                if vx is not None and vy is not None:
                    pts.append((vx, vy))
            if i < n and pairs[i] == ("0", "SEQEND"):
                i += 1
            if layer in line_layers:
                add_line(pts, closed)
            continue

        i += 1

    return lines, points, None if bounds[0] == math.inf else tuple(bounds)


def rgb(hex_color: str) -> tuple[int, int, int, int]:
    """Convert a hex colour to RGBA with alpha 220.

    Parameters
    ----------
    hex_color : str
        Colour as ``"#rrggbb"`` or ``"rrggbb"``.

    Returns
    -------
    tuple of int
        ``(r, g, b, 220)``.
    """
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 220)


def choose_raw_crs(bounds: tuple[float, float, float, float] | None):
    """Pick the Gauß-Krüger strip of raw NÖ DXF coordinates.

    Parameters
    ----------
    bounds : tuple of float or None
        Raw ``(minx, miny, maxx, maxy)``.

    Returns
    -------
    strip : str
        ``"M31"`` if the x-centre exceeds 75 000, else ``"M34"`` (also without
        bounds).
    transformer : pyproj.Transformer
        The matching entry of ``TRANSFORMERS_TO_LAMBERT``.
    """
    if not bounds:
        return "M34", TRANSFORMERS_TO_LAMBERT["M34"]
    minx, _miny, maxx, _maxy = bounds
    cx = (minx + maxx) / 2
    # In the NÖ ZIP, the apparent eastern side-island has raw M31 coordinates
    # around x=80k..120k. If interpreted as M34 it shifts far east. M31->Lambert
    # places it at Amstetten/Ybbs as expected.
    if cx > 75_000:
        return "M31", TRANSFORMERS_TO_LAMBERT["M31"]
    return "M34", TRANSFORMERS_TO_LAMBERT["M34"]


def transform_bounds(bounds: tuple[float, float, float, float] | None):
    """Transform raw DXF bounds to EPSG:31287.

    Parameters
    ----------
    bounds : tuple of float or None
        Raw ``(minx, miny, maxx, maxy)``.

    Returns
    -------
    tuple of float or None
        Bounds of the four transformed corners, or ``None`` without bounds.
    """
    if not bounds:
        return None
    minx, miny, maxx, maxy = bounds
    _strip, tr = choose_raw_crs(bounds)
    xs, ys = tr.transform([minx, minx, maxx, maxx], [miny, maxy, miny, maxy])
    return (min(xs), min(ys), max(xs), max(ys))


def transform_polygon(poly: Polygon, tr: Transformer):
    """Transform the rings of a polygon.

    Parameters
    ----------
    poly : shapely.geometry.Polygon
        Polygon in source coordinates.
    tr : pyproj.Transformer
        Coordinate transformer.

    Returns
    -------
    exterior : list of tuple
        Transformed exterior coordinates.
    interiors : list of list of tuple
        Transformed coordinates of each interior ring.
    """
    x, y = poly.exterior.xy
    tx, ty = tr.transform(x, y)
    exterior = list(zip(tx, ty))
    interiors = []
    for ring in poly.interiors:
        rx, ry = ring.xy
        rtx, rty = tr.transform(rx, ry)
        interiors.append(list(zip(rtx, rty)))
    return exterior, interiors


def pix_coords(coords, minx: float, maxy: float, px: float):
    """Convert map coordinates to PIL pixel coordinates.

    NumPy is used when available because large polygon rings otherwise spend a
    lot of time in Python arithmetic/list appends. The list fallback keeps the
    script runnable in minimal environments.
    """
    if np is not None:
        arr = np.asarray(coords, dtype="float64")
        if arr.size == 0:
            return []
        xs = np.rint((arr[:, 0] - minx) / px).astype("int32", copy=False)
        ys = np.rint((maxy - arr[:, 1]) / px).astype("int32", copy=False)
        pts = np.column_stack((xs, ys)).tolist()
        if not pts:
            return []
        deduped = [tuple(pts[0])]
        for pt in pts[1:]:
            t = tuple(pt)
            if t != deduped[-1]:
                deduped.append(t)
        return deduped
    pts = [(int(round((x - minx) / px)), int(round((maxy - y) / px))) for x, y in coords]
    if not pts:
        return []
    deduped = [pts[0]]
    for pt in pts[1:]:
        if pt != deduped[-1]:
            deduped.append(pt)
    return deduped


def bounds_intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    """Check whether two bounding boxes intersect (edges included).

    Parameters
    ----------
    a, b : tuple of float
        ``(minx, miny, maxx, maxy)``.

    Returns
    -------
    bool
        ``True`` if the boxes overlap or touch.
    """
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def make_tile_jobs(
    file_infos: list[tuple[str, tuple[float, float, float, float]]],
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    tile_size_m: float,
    halo_m: float,
) -> list[tuple[int, tuple[float, float, float, float], tuple[float, float, float, float], list[str]]]:
    """Split an extent into square tiles and assign DXF files to each.

    Parameters
    ----------
    file_infos : list of tuple
        ``(name, bounds)`` per DXF file.
    minx, miny, maxx, maxy : float
        Extent to tile.
    tile_size_m : float
        Tile edge length in metres.
    halo_m : float
        Overlap added around each tile.

    Returns
    -------
    list of tuple
        ``(tile_id, inner_bounds, halo_bounds, names)`` for every tile whose
        halo intersects at least one file; ``tile_id`` counts all tiles,
        including skipped ones.
    """
    jobs = []
    tile_id = 0
    x = minx
    while x < maxx:
        x1 = min(x + tile_size_m, maxx)
        y = miny
        while y < maxy:
            y1 = min(y + tile_size_m, maxy)
            inner = (x, y, x1, y1)
            halo = (x - halo_m, y - halo_m, x1 + halo_m, y1 + halo_m)
            tile_names = [name for name, tb in file_infos if bounds_intersect(tb, halo)]
            if tile_names:
                jobs.append((tile_id, inner, halo, tile_names))
            tile_id += 1
            y += tile_size_m
        x += tile_size_m
    return jobs


def default_bounds_cache_path(line_layers: set[str], raw_coordinates: bool) -> Path:
    """Default path of the DXF bounds cache.

    Parameters
    ----------
    line_layers : set of str
        DXF line layers; part of the file name.
    raw_coordinates : bool
        Whether bounds are raw or EPSG:31287; part of the file name.

    Returns
    -------
    Path
        ``OUT_DIR / "noe_dkm_bounds_<layers>_<raw|epsg31287>.csv"``.
    """
    layers_key = "_".join(sorted(re.sub(r"[^A-Za-z0-9]+", "_", layer) for layer in line_layers))
    coord_key = "raw" if raw_coordinates else "epsg31287"
    return OUT_DIR / f"noe_dkm_bounds_{layers_key}_{coord_key}.csv"


def read_bounds_cache(path: Path, names: list[str]):
    """Read DXF bounds from the cache.

    Parameters
    ----------
    path : Path
        Cache CSV.
    names : list of str
        DXF file names that must all be present.

    Returns
    -------
    tuple or None
        ``(overall_bounds, coverage_bounds, file_infos, source_file_stats)``;
        ``None`` if the file is missing, unreadable (a warning is printed) or
        lacks any of ``names``.
    """
    if not path.exists():
        return None
    wanted = set(names)
    rows = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = row.get("name") or ""
                if name not in wanted:
                    continue
                rows[name] = (
                    (
                        float(row["minx"]),
                        float(row["miny"]),
                        float(row["maxx"]),
                        float(row["maxy"]),
                    ),
                    row.get("strip") or None,
                )
    except Exception as e:
        print(f"WARN ignoring bounds cache {path}: {e}", flush=True)
        return None
    if any(name not in rows for name in names):
        return None

    overall = [math.inf, math.inf, -math.inf, -math.inf]
    coverage_bounds = []
    file_infos = []
    source_file_stats = Counter()
    for name in names:
        tb, strip = rows[name]
        coverage_bounds.append(tb)
        file_infos.append((name, tb))
        source_file_stats["files"] += 1
        if strip:
            source_file_stats[f"files_{strip}"] += 1
        else:
            source_file_stats["files_no_bounds"] += 1
        overall[0] = min(overall[0], tb[0]); overall[1] = min(overall[1], tb[1])
        overall[2] = max(overall[2], tb[2]); overall[3] = max(overall[3], tb[3])
    return overall, coverage_bounds, file_infos, source_file_stats


def write_bounds_cache(path: Path, rows: list[tuple[str, tuple[float, float, float, float], str | None]]) -> None:
    """Write the DXF bounds cache.

    Writes to a temporary file first and then replaces ``path``.

    Parameters
    ----------
    path : Path
        Cache CSV; the parent directory is created.
    rows : list of tuple
        ``(name, bounds, strip)`` per DXF file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "minx", "miny", "maxx", "maxy", "strip"])
        for name, bounds, strip in rows:
            writer.writerow([name, *bounds, strip or ""])
    tmp_path.replace(path)


def transform_line(line: LineString, tr: Transformer) -> LineString | None:
    """Transform a line string.

    Parameters
    ----------
    line : shapely.geometry.LineString
        Line in source coordinates.
    tr : pyproj.Transformer
        Coordinate transformer.

    Returns
    -------
    shapely.geometry.LineString or None
        Transformed line; ``None`` for fewer than two points, an invalid
        result, or zero length.
    """
    coords = list(line.coords)
    if len(coords) < 2:
        return None
    xs, ys = zip(*coords)
    tx, ty = tr.transform(xs, ys)
    try:
        out = LineString(zip(tx, ty))
    except Exception:
        return None
    if out.is_empty or out.length <= 0:
        return None
    return out


def polygons_from_geometry(geom) -> list[Polygon]:
    """Split a geometry into its polygons.

    Parameters
    ----------
    geom : shapely.geometry.base.BaseGeometry
        Polygon, MultiPolygon or GeometryCollection (recursive).

    Returns
    -------
    list of shapely.geometry.Polygon
        Non-empty polygons; empty for other geometry types.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty]
    if isinstance(geom, GeometryCollection):
        return [p for g in geom.geoms for p in polygons_from_geometry(g)]
    return []


def precision_union(lines: list[LineString], precision_m: float):
    """Snap linework and union it with the fastest available Shapely path."""
    if not lines:
        return GeometryCollection()
    if np is not None:
        arr = np.asarray(lines, dtype=object)
        snapped = set_precision(arr, precision_m) if precision_m > 0 else arr
        return unary_union(snapped)
    if precision_m > 0:
        return unary_union([set_precision(ls, precision_m) for ls in lines])
    return unary_union(lines)


def assign_points_to_polygons(polys: list[Polygon], points: list[Point]) -> dict[int, int]:
    """Return point-index -> smallest covering polygon-index.

    STRtree can query all points in one call on Shapely 2.x. That is much
    faster than querying every NS symbol individually. Multiple matches happen
    for boundary points or nested rings; matching the old behavior, choose the
    smallest covering polygon.
    """
    if not polys or not points:
        return {}
    tree = STRtree(polys)
    if np is not None:
        try:
            pairs = tree.query(points, predicate="covered_by")
            if getattr(pairs, "size", 0) == 0:
                return {}
            point_idx = pairs[0].astype("int64", copy=False)
            poly_idx = pairs[1].astype("int64", copy=False)
            poly_areas = np.fromiter((p.area for p in polys), dtype="float64", count=len(polys))
            order = np.lexsort((poly_areas[poly_idx], point_idx))
            point_sorted = point_idx[order]
            poly_sorted = poly_idx[order]
            first = np.empty(len(point_sorted), dtype=bool)
            first[0] = True
            first[1:] = point_sorted[1:] != point_sorted[:-1]
            return {int(pt): int(poly) for pt, poly in zip(point_sorted[first], poly_sorted[first])}
        except Exception:
            pass

    assigned: dict[int, int] = {}
    for point_i, pt in enumerate(points):
        best_i = None
        best_area = math.inf
        for candidate in tree.query(pt):
            poly_i = int(candidate)
            poly = polys[poly_i]
            if poly.covers(pt) and poly.area < best_area:
                best_i = poly_i
                best_area = poly.area
        if best_i is not None:
            assigned[point_i] = best_i
    return assigned


def rgba_lookup(diag: bool = False) -> np.ndarray:
    """Lookup table from raster category code to RGBA.

    Parameters
    ----------
    diag : bool, optional
        If ``True``, code 0 is transparent instead of ``BACKGROUND_RGBA``.

    Returns
    -------
    numpy.ndarray
        ``uint8`` array of shape ``(len(RASTER_CATEGORIES) + 1, 4)``.
    """
    lookup = np.zeros((len(RASTER_CATEGORIES) + 1, 4), dtype=np.uint8)
    lookup[0] = BACKGROUND_RGBA
    for cat, idx in RASTER_CATEGORY_IDS.items():
        if cat == "Nicht zugeordnet":
            lookup[idx] = UNASSIGNED_RGBA
        else:
            lookup[idx] = rgb(CATEGORY_COLORS.get(cat, CATEGORY_COLORS["Unbekannt"]))
    if diag:
        lookup[0] = (0, 0, 0, 0)
    return lookup


def rasterize_codes(shapes, out_shape: tuple[int, int], transform) -> np.ndarray:
    """Rasterise ``(geometry, code)`` pairs with ``all_touched=True``.

    Parameters
    ----------
    shapes : sequence
        ``(geometry, code)`` pairs.
    out_shape : tuple of int
        ``(height, width)``.
    transform : affine.Affine
        Raster transform.

    Returns
    -------
    numpy.ndarray
        ``uint8`` codes, 0 where nothing is drawn.
    """
    if not shapes:
        return np.zeros(out_shape, dtype=np.uint8)
    return rasterio_rasterize(
        shapes,
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    )


_BOUNDS_WORKER_ZIP = None
_BOUNDS_WORKER_CFG = None
_WORKER_ZIP = None
_WORKER_CFG = None
_WORKER_FILE_CACHE = None


def flatten_linework(geom) -> list[LineString]:
    """Flatten a geometry into its line strings.

    Parameters
    ----------
    geom : shapely.geometry.base.BaseGeometry
        LineString or a multi-part geometry (recursive).

    Returns
    -------
    list of shapely.geometry.LineString
        The line strings; empty for other geometry types.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if hasattr(geom, "geoms"):
        out = []
        for part in geom.geoms:
            out.extend(flatten_linework(part))
        return out
    return []


def load_noe_boundary_lines(boundary_path: Path) -> list[LineString]:
    """Load the outline of Niederösterreich as line strings.

    Parameters
    ----------
    boundary_path : Path
        Administrative boundary file; reprojected to EPSG:31287 (assumed if
        unset) and filtered to ``BL`` containing "Nieder" if that column
        exists.

    Returns
    -------
    list of shapely.geometry.LineString
        Boundary of the union of the selected geometries.

    Raises
    ------
    SystemExit
        If geopandas cannot be imported or no geometries remain.
    """
    try:
        import geopandas as gpd
    except Exception as e:
        raise SystemExit(f"--include-noe-boundary-line requires geopandas: {e}") from e

    gdf = gpd.read_file(boundary_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(31287)
    else:
        gdf = gdf.to_crs(31287)
    if "BL" in gdf.columns:
        mask = gdf["BL"].astype(str).str.contains("Nieder", case=False, regex=False)
        gdf = gdf[mask]
    if gdf.empty:
        raise SystemExit(f"No Niederösterreich geometries found in {boundary_path}")
    return flatten_linework(gdf.geometry.union_all().boundary)


def _init_bounds_worker(zip_path, line_layers, raw_coordinates):
    """Initialise a worker process for the bounds pass.

    Parameters
    ----------
    zip_path : Path
        NÖ DXF ZIP, opened once per worker.
    line_layers : set of str
        DXF line layers.
    raw_coordinates : bool
        Keep raw coordinates instead of transforming to EPSG:31287.
    """
    global _BOUNDS_WORKER_ZIP, _BOUNDS_WORKER_CFG
    _BOUNDS_WORKER_ZIP = zipfile.ZipFile(zip_path)
    _BOUNDS_WORKER_CFG = {
        "line_layers": line_layers,
        "raw_coordinates": raw_coordinates,
    }


def _process_file_for_bounds(name: str):
    """Compute the bounds of one DXF file in a worker.

    Parameters
    ----------
    name : str
        Member name in the ZIP.

    Returns
    -------
    tuple
        ``(name, raw_bounds, target_bounds, strip)``; ``strip`` is ``None``
        without bounds.
    """
    raw = _BOUNDS_WORKER_ZIP.read(name)
    b = parse_dxf_bounds_fast(raw, _BOUNDS_WORKER_CFG["line_layers"])
    tb = b if _BOUNDS_WORKER_CFG["raw_coordinates"] else transform_bounds(b)
    strip = choose_raw_crs(b)[0] if b else None
    return name, b, tb, strip


def _init_worker(
    zip_path,
    line_layers,
    sym_cat,
    precision_m,
    min_area_m2,
    raw_coordinates,
    minx,
    maxy,
    px,
    highlight_unassigned,
    diagnostics,
    no_clear_holes,
    render_final,
    worker_cache_files,
    boundary_lines,
):
    """Initialise a worker process for polygonisation.

    Opens the ZIP once per worker, stores all parameters in ``_WORKER_CFG``
    and resets the per-worker file cache.

    Parameters
    ----------
    zip_path : Path
        NÖ DXF ZIP.
    line_layers : set of str
        DXF line layers for polygonisation.
    sym_cat : dict of str to str
        NS symbol to category.
    precision_m : float
        Snapping precision.
    min_area_m2 : float
        Minimum polygon area.
    raw_coordinates : bool
        Keep raw coordinates.
    minx, maxy : float
        Upper-left corner of the canvas.
    px : float
        Pixel size in metres.
    highlight_unassigned : bool
        Highlight polygons without NS symbol.
    diagnostics : bool
        Produce the diagnostics map.
    no_clear_holes : bool
        Do not clear interior rings.
    render_final : bool
        Produce the final map.
    worker_cache_files : int
        Size of the per-worker DXF cache.
    boundary_lines : list of shapely.geometry.LineString
        Extra linework for closing border polygons.
    """
    global _WORKER_ZIP, _WORKER_CFG, _WORKER_FILE_CACHE
    _WORKER_ZIP = zipfile.ZipFile(zip_path)
    _WORKER_CFG = {
        "line_layers": line_layers,
        "sym_cat": sym_cat,
        "precision_m": precision_m,
        "min_area_m2": min_area_m2,
        "raw_coordinates": raw_coordinates,
        "minx": minx,
        "maxy": maxy,
        "px": px,
        "highlight_unassigned": highlight_unassigned,
        "diagnostics": diagnostics,
        "no_clear_holes": no_clear_holes,
        "render_final": render_final,
        "worker_cache_files": worker_cache_files,
        "boundary_lines": boundary_lines,
    }
    _WORKER_FILE_CACHE = OrderedDict()


def _get_projected_file_geometry(name: str):
    """Read/parse one DXF once per worker and cache projected linework/points."""
    cfg = _WORKER_CFG
    cache_size = int(cfg.get("worker_cache_files") or 0)
    if cache_size > 0 and _WORKER_FILE_CACHE is not None and name in _WORKER_FILE_CACHE:
        value = _WORKER_FILE_CACHE.pop(name)
        _WORKER_FILE_CACHE[name] = value
        return value

    raw = _WORKER_ZIP.read(name)
    raw_lines, raw_points, raw_bounds = parse_dxf(raw, cfg["line_layers"])
    if not raw_bounds:
        value = ([], [], raw_bounds)
    elif cfg["raw_coordinates"]:
        value = (raw_lines, [(symbol, x, y) for symbol, x, y in raw_points], raw_bounds)
    else:
        _strip, transformer = choose_raw_crs(raw_bounds)
        lines = []
        for raw_line in raw_lines:
            line = transform_line(raw_line, transformer)
            if line is not None:
                lines.append(line)
        if raw_points:
            symbols = [p[0] for p in raw_points]
            xs = [p[1] for p in raw_points]
            ys = [p[2] for p in raw_points]
            tx, ty = transformer.transform(xs, ys)
            points = list(zip(symbols, tx, ty))
        else:
            points = []
        value = (lines, points, raw_bounds)

    if cache_size > 0 and _WORKER_FILE_CACHE is not None:
        _WORKER_FILE_CACHE[name] = value
        while len(_WORKER_FILE_CACHE) > cache_size:
            _WORKER_FILE_CACHE.popitem(last=False)
    return value


def _process_file_for_render(name: str):
    """Polygonize/classify one DXF and return pixel rings for the parent to draw."""
    cfg = _WORKER_CFG
    raw = _WORKER_ZIP.read(name)
    lines, ns_points, raw_bounds = parse_dxf(raw, cfg["line_layers"])
    strip, transformer = choose_raw_crs(raw_bounds)

    stats = Counter({f"files_{strip}": 1, "files": 1, "lines": len(lines), "ns_points": len(ns_points)})
    area_by_cat = Counter()
    unmapped_symbols = Counter()
    ambiguous = 0
    draw_by_cat = defaultdict(list)

    if not lines:
        stats["files_no_lines"] += 1
        return name, stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, None
    if not ns_points:
        stats["files_no_ns"] += 1

    try:
        merged = precision_union(lines, cfg["precision_m"])
        polys = [p for p in polygonize(merged) if p.area >= cfg["min_area_m2"]]
    except Exception as e:
        stats["polygonize_errors"] += 1
        return name, stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, str(e)

    stats["polygons"] += len(polys)
    if not polys:
        return name, stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, None

    poly_votes: dict[int, Counter] = defaultdict(Counter)
    point_geoms = [Point(x, y) for _symbol, x, y in ns_points]
    point_to_poly = assign_points_to_polygons(polys, point_geoms)
    for point_i, (symbol, x, y) in enumerate(ns_points):
        cat = cfg["sym_cat"].get(symbol)
        if not cat:
            cat = "Unbekannt"
            unmapped_symbols[symbol] += 1
        best_i = point_to_poly.get(point_i)
        if best_i is not None:
            poly_votes[best_i][cat] += 1
            stats["assigned_ns_points"] += 1
        else:
            stats["unassigned_ns_points"] += 1

    assigned_poly_ids = set(poly_votes.keys())
    stats["assigned_polygons"] += len(assigned_poly_ids)
    unassigned_polys = [p for pi, p in enumerate(polys) if pi not in assigned_poly_ids]
    stats["unassigned_polygons"] += len(unassigned_polys)
    stats["unassigned_polygon_area_m2"] += sum(p.area for p in unassigned_polys)

    for pi, votes in poly_votes.items():
        if not votes:
            continue
        most = votes.most_common()
        if len(most) > 1:
            ambiguous += 1
        cat = most[0][0]
        poly = polys[pi]
        area_by_cat[cat] += poly.area
        if cfg["raw_coordinates"]:
            exterior = list(poly.exterior.coords)
            interiors = [list(ring.coords) for ring in poly.interiors]
        else:
            exterior, interiors = transform_polygon(poly, transformer)
        exterior_px = pix_coords(exterior, cfg["minx"], cfg["maxy"], cfg["px"])
        if len(exterior_px) >= 3:
            holes_px = [pix_coords(ring, cfg["minx"], cfg["maxy"], cfg["px"]) for ring in interiors]
            draw_by_cat[cat].append((poly.area, exterior_px, [h for h in holes_px if len(h) >= 3]))

    if cfg.get("highlight_unassigned") or cfg.get("diagnostics"):
        for poly in unassigned_polys:
            if cfg["raw_coordinates"]:
                exterior = list(poly.exterior.coords)
            else:
                exterior, _interiors = transform_polygon(poly, transformer)
            exterior_px = pix_coords(exterior, cfg["minx"], cfg["maxy"], cfg["px"])
            if len(exterior_px) >= 3:
                draw_by_cat["Nicht zugeordnet"].append((poly.area, exterior_px, []))

    return name, stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, None


def _append_projected_polygon(draw_by_cat, cat: str, poly: Polygon, cfg) -> bool:
    """Add a polygon, converted to pixel rings, to a category group.

    Parameters
    ----------
    draw_by_cat : dict of str to list
        Draw groups by category; receives ``(area, exterior_px, holes_px)``.
    cat : str
        Category.
    poly : shapely.geometry.Polygon
        Polygon in map coordinates.
    cfg : dict
        Needs ``minx``, ``maxy`` and ``px``.

    Returns
    -------
    bool
        ``False`` if the exterior has fewer than 3 pixel points (nothing
        added), else ``True``. Holes with fewer than 3 points are dropped.
    """
    exterior_px = pix_coords(list(poly.exterior.coords), cfg["minx"], cfg["maxy"], cfg["px"])
    if len(exterior_px) < 3:
        return False
    holes_px = [pix_coords(list(ring.coords), cfg["minx"], cfg["maxy"], cfg["px"]) for ring in poly.interiors]
    draw_by_cat[cat].append((poly.area, exterior_px, [h for h in holes_px if len(h) >= 3]))
    return True


def _process_tile_for_render(job):
    """Polygonize all DXFs touching a tile+halo and return clipped tile-interior rings."""
    tile_id, inner_bounds, halo_bounds, file_names = job
    cfg = _WORKER_CFG
    inner_box = box(*inner_bounds)
    lines: list[LineString] = []
    ns_points: list[tuple[str, float, float]] = []
    stats = Counter({"tiles": 1, "tile_file_refs": len(file_names)})
    area_by_cat = Counter()
    unmapped_symbols = Counter()
    ambiguous = 0
    draw_by_cat = defaultdict(list)

    for name in file_names:
        file_lines, file_points, _raw_bounds = _get_projected_file_geometry(name)
        for line in file_lines:
            if bounds_intersect(line.bounds, halo_bounds):
                lines.append(line)

        for symbol, x, y in file_points:
            if halo_bounds[0] <= x <= halo_bounds[2] and halo_bounds[1] <= y <= halo_bounds[3]:
                ns_points.append((symbol, x, y))
    for line in cfg.get("boundary_lines") or []:
        if bounds_intersect(line.bounds, halo_bounds):
            lines.append(line)

    stats["tile_input_lines"] += len(lines)
    stats["tile_input_ns_points"] += len(ns_points)
    if not lines:
        stats["tiles_no_lines"] += 1
        return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, None
    if not ns_points:
        stats["tiles_no_ns"] += 1

    try:
        merged = precision_union(lines, cfg["precision_m"])
        polys = [p for p in polygonize(merged) if p.area >= cfg["min_area_m2"] and p.intersects(inner_box)]
    except Exception as e:
        stats["polygonize_errors"] += 1
        return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, str(e)

    if not polys:
        return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, None

    poly_votes: dict[int, Counter] = defaultdict(Counter)
    point_geoms = [Point(x, y) for _symbol, x, y in ns_points]
    point_to_poly = assign_points_to_polygons(polys, point_geoms)
    for point_i, (symbol, x, y) in enumerate(ns_points):
        cat = cfg["sym_cat"].get(symbol)
        if not cat:
            cat = "Unbekannt"
            unmapped_symbols[symbol] += 1
        best_i = point_to_poly.get(point_i)
        in_inner = inner_bounds[0] <= x < inner_bounds[2] and inner_bounds[1] <= y < inner_bounds[3]
        if best_i is not None:
            poly_votes[best_i][cat] += 1
            if in_inner:
                stats["assigned_ns_points"] += 1
        elif in_inner:
            stats["unassigned_ns_points"] += 1

    for pi, poly in enumerate(polys):
        try:
            clipped = poly.intersection(inner_box)
        except Exception:
            stats["clip_errors"] += 1
            continue
        pieces = [p for p in polygons_from_geometry(clipped) if p.area >= cfg["min_area_m2"]]
        if not pieces:
            continue

        votes = poly_votes.get(pi)
        if votes:
            most = votes.most_common()
            if len(most) > 1:
                ambiguous += len(pieces)
            cat = most[0][0]
            for piece in pieces:
                stats["polygons"] += 1
                stats["assigned_polygons"] += 1
                area_by_cat[cat] += piece.area
                _append_projected_polygon(draw_by_cat, cat, piece, cfg)
        else:
            for piece in pieces:
                stats["polygons"] += 1
                stats["unassigned_polygons"] += 1
                stats["unassigned_polygon_area_m2"] += piece.area
                if cfg.get("highlight_unassigned") or cfg.get("diagnostics"):
                    _append_projected_polygon(draw_by_cat, "Nicht zugeordnet", piece, cfg)

    return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, draw_by_cat, None


def _process_tile_for_raster(job):
    """Polygonize/classify one tile and return raster code arrays for pasting."""
    tile_id, inner_bounds, halo_bounds, file_names = job
    cfg = _WORKER_CFG
    inner_box = box(*inner_bounds)
    lines: list[LineString] = []
    ns_points: list[tuple[str, float, float]] = []
    stats = Counter({"tiles": 1, "tile_file_refs": len(file_names)})
    area_by_cat = Counter()
    unmapped_symbols = Counter()
    ambiguous = 0

    tile_width = max(1, int(math.ceil((inner_bounds[2] - inner_bounds[0]) / cfg["px"])))
    tile_height = max(1, int(math.ceil((inner_bounds[3] - inner_bounds[1]) / cfg["px"])))
    out_shape = (tile_height, tile_width)
    transform = rasterio_from_origin(inner_bounds[0], inner_bounds[3], cfg["px"], cfg["px"])

    for name in file_names:
        file_lines, file_points, _raw_bounds = _get_projected_file_geometry(name)
        for line in file_lines:
            if bounds_intersect(line.bounds, halo_bounds):
                lines.append(line)
        for symbol, x, y in file_points:
            if halo_bounds[0] <= x <= halo_bounds[2] and halo_bounds[1] <= y <= halo_bounds[3]:
                ns_points.append((symbol, x, y))
    for line in cfg.get("boundary_lines") or []:
        if bounds_intersect(line.bounds, halo_bounds):
            lines.append(line)

    stats["tile_input_lines"] += len(lines)
    stats["tile_input_ns_points"] += len(ns_points)
    if not lines:
        stats["tiles_no_lines"] += 1
        payload = (inner_bounds, tile_width, tile_height, None, None)
        return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, payload, None
    if not ns_points:
        stats["tiles_no_ns"] += 1

    try:
        merged = precision_union(lines, cfg["precision_m"])
        polys = [p for p in polygonize(merged) if p.area >= cfg["min_area_m2"] and p.intersects(inner_box)]
    except Exception as e:
        stats["polygonize_errors"] += 1
        payload = (inner_bounds, tile_width, tile_height, None, None)
        return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, payload, str(e)

    if not polys:
        payload = (inner_bounds, tile_width, tile_height, None, None)
        return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, payload, None

    poly_votes: dict[int, Counter] = defaultdict(Counter)
    point_geoms = [Point(x, y) for _symbol, x, y in ns_points]
    point_to_poly = assign_points_to_polygons(polys, point_geoms)
    for point_i, (symbol, x, y) in enumerate(ns_points):
        cat = cfg["sym_cat"].get(symbol)
        if not cat:
            cat = "Unbekannt"
            unmapped_symbols[symbol] += 1
        best_i = point_to_poly.get(point_i)
        in_inner = inner_bounds[0] <= x < inner_bounds[2] and inner_bounds[1] <= y < inner_bounds[3]
        if best_i is not None:
            poly_votes[best_i][cat] += 1
            if in_inner:
                stats["assigned_ns_points"] += 1
        elif in_inner:
            stats["unassigned_ns_points"] += 1

    render_final = cfg.get("render_final", True)
    render_diag = cfg.get("diagnostics", False)
    raw_shapes = []
    diag_shapes = []
    for pi, poly in enumerate(polys):
        try:
            clipped = poly.intersection(inner_box)
        except Exception:
            stats["clip_errors"] += 1
            continue
        pieces = [p for p in polygons_from_geometry(clipped) if p.area >= cfg["min_area_m2"]]
        if not pieces:
            continue

        votes = poly_votes.get(pi)
        if votes:
            most = votes.most_common()
            if len(most) > 1:
                ambiguous += len(pieces)
            cat = most[0][0]
            cat_id = RASTER_CATEGORY_IDS.get(cat, RASTER_CATEGORY_IDS["Unbekannt"])
            for piece in pieces:
                stats["polygons"] += 1
                stats["assigned_polygons"] += 1
                area_by_cat[cat] += piece.area
                if render_final:
                    raw_shapes.append((piece, cat_id))
                if render_diag:
                    diag_shapes.append((piece, cat_id))
        else:
            unassigned_id = RASTER_CATEGORY_IDS["Nicht zugeordnet"]
            for piece in pieces:
                stats["polygons"] += 1
                stats["unassigned_polygons"] += 1
                stats["unassigned_polygon_area_m2"] += piece.area
                if render_final and cfg.get("highlight_unassigned"):
                    raw_shapes.append((piece, unassigned_id))
                if render_diag:
                    diag_shapes.append((piece, unassigned_id))

    raw_bytes = None
    diag_bytes = None
    if render_final:
        raw_bytes = rasterize_codes(raw_shapes, out_shape, transform).tobytes()
    if render_diag:
        diag_bytes = rasterize_codes(diag_shapes, out_shape, transform).tobytes()
    payload = (inner_bounds, tile_width, tile_height, raw_bytes, diag_bytes)
    return f"tile_{tile_id}", stats, area_by_cat, unmapped_symbols, ambiguous, payload, None


def paint_draw_groups(draw_by_cat, draw, diag_draw, highlight_unassigned: bool, no_clear_holes: bool):
    """Paint grouped polygons onto the final and diagnostics images.

    Polygons are drawn largest first, and each polygon's holes are cleared
    to the background before smaller ones are drawn, so nested polygons are
    not erased.

    Parameters
    ----------
    draw_by_cat : dict of str to list
        Draw groups from ``_append_projected_polygon()``.
    draw : PIL.ImageDraw.ImageDraw or None
        Final map.
    diag_draw : PIL.ImageDraw.ImageDraw or None
        Diagnostics map.
    highlight_unassigned : bool
        Also paint unassigned polygons on the final map.
    no_clear_holes : bool
        Do not clear interior rings.
    """
    draw_order = DRAW_ORDER + (["Nicht zugeordnet"] if (highlight_unassigned or diag_draw is not None) else [])
    entries = []
    for cat in draw_order:
        color = CATEGORY_COLORS.get(cat)
        if not color:
            continue
        final_fill = UNASSIGNED_RGBA if cat == "Nicht zugeordnet" else rgb(color)
        diag_fill = UNASSIGNED_RGBA if cat == "Nicht zugeordnet" else rgb(color)
        for area, exterior_px, holes_px in draw_by_cat.get(cat, []):
            entries.append((area, cat, exterior_px, holes_px, final_fill, diag_fill))

    # Draw larger polygons first. Their holes are cleared before smaller island
    # polygons are drawn, so nested classified polygons are not erased to white.
    entries.sort(key=lambda item: item[0], reverse=True)
    for _area, cat, exterior_px, holes_px, final_fill, diag_fill in entries:
        if draw is not None and (cat != "Nicht zugeordnet" or highlight_unassigned):
            draw.polygon(exterior_px, fill=final_fill)
            if cat == "Nicht zugeordnet":
                draw.line(exterior_px + [exterior_px[0]], fill=UNASSIGNED_OUTLINE_RGBA, width=1)
        if diag_draw is not None:
            diag_draw.polygon(exterior_px, fill=diag_fill)
        if not no_clear_holes:
            for pts in holes_px:
                if draw is not None and (cat != "Nicht zugeordnet" or highlight_unassigned):
                    draw.polygon(pts, fill=BACKGROUND_RGBA)
                if diag_draw is not None:
                    diag_draw.polygon(pts, fill=BACKGROUND_RGBA)


def paste_tile_raster(payload, img: Image.Image | None, diag_img: Image.Image | None, minx: float, maxy: float, px: float):
    """Paste a rendered tile into the final and diagnostics images.

    Parameters
    ----------
    payload : tuple
        ``(inner_bounds, tile_width, tile_height, raw_bytes, diag_bytes)``;
        the byte strings are ``uint8`` category codes.
    img : PIL.Image.Image or None
        Final map.
    diag_img : PIL.Image.Image or None
        Diagnostics map; only non-zero codes are pasted.
    minx, maxy : float
        Upper-left corner of the canvas.
    px : float
        Pixel size in metres.
    """
    inner_bounds, tile_width, tile_height, raw_bytes, diag_bytes = payload
    col0 = int(round((inner_bounds[0] - minx) / px))
    row0 = int(round((maxy - inner_bounds[3]) / px))
    if raw_bytes is not None and img is not None:
        raw_codes = np.frombuffer(raw_bytes, dtype=np.uint8).reshape((tile_height, tile_width))
        tile_rgba = rgba_lookup(diag=False)[raw_codes]
        img.paste(Image.fromarray(tile_rgba), (col0, row0))
    if diag_bytes is not None and diag_img is not None:
        diag_codes = np.frombuffer(diag_bytes, dtype=np.uint8).reshape((tile_height, tile_width))
        mask = (diag_codes != 0).astype(np.uint8) * 255
        if mask.any():
            tile_rgba = rgba_lookup(diag=True)[diag_codes]
            diag_img.paste(Image.fromarray(tile_rgba), (col0, row0), Image.fromarray(mask))


def reason_for_component(fractions: dict[str, float]) -> str:
    """Classify a white residual component by its dominant cause.

    Parameters
    ----------
    fractions : dict of str to float
        Share of the component per cause.

    Returns
    -------
    str
        The first cause with a share of at least 0.5, in the order
        unassigned, no polygon, outside coverage, classified in diagnostics;
        then unassigned plus no polygon together; otherwise
        ``"mixed_or_boundary_raster_effect"``.
    """
    if fractions.get("unassigned", 0) >= 0.5:
        return "polygon_exists_without_ns_symbol"
    if fractions.get("no_polygon_gap", 0) >= 0.5:
        return "inside_dxf_bounds_but_no_polygon"
    if fractions.get("outside_diagnostic_coverage", 0) >= 0.5:
        return "inside_noe_but_outside_current_dkm_coverage"
    if fractions.get("diagnostic_classified", 0) >= 0.5:
        return "classified_in_diagnostics_but_white_in_final"
    if fractions.get("unassigned", 0) + fractions.get("no_polygon_gap", 0) >= 0.5:
        return "mixed_unassigned_and_no_polygon"
    return "mixed_or_boundary_raster_effect"


def write_boundary_residual_diagnostics(
    raw_img: Image.Image,
    diag_img: Image.Image | None,
    boundary_path: Path,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    px: float,
    output_prefix: Path,
    min_component_area_km2: float,
    component_limit: int,
) -> dict[str, float | int]:
    """Compare the rendered map with the NÖ administrative outline.

    The existing diagnostics map distinguishes unassigned polygons (magenta)
    from approximate DKM coverage with no rendered polygon left (cyan). This
    function uses the real NÖ outline as the mask and classifies the white
    residual components against those diagnostic pixels.
    """
    if np is None:
        raise SystemExit("--boundary-diagnostics requires numpy")
    try:
        import geopandas as gpd
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        from scipy import ndimage
    except Exception as e:
        raise SystemExit(f"--boundary-diagnostics requires geopandas, rasterio and scipy: {e}") from e

    boundary_path = Path(boundary_path)
    if not boundary_path.exists():
        raise SystemExit(f"Boundary shapefile not found: {boundary_path}")

    gdf = gpd.read_file(boundary_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(31287)
    else:
        gdf = gdf.to_crs(31287)
    if "BL" in gdf.columns:
        gdf = gdf[gdf["BL"] == "Niederösterreich"]
    if gdf.empty:
        raise SystemExit(f"No Niederösterreich geometries found in {boundary_path}")

    land_geom = gdf.dissolve().geometry.iloc[0]
    width, height = raw_img.size
    transform = from_origin(minx, maxy, px, px)
    noe_mask = rasterize(
        [(land_geom, 1)],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    ).astype(bool)

    raw_arr = np.asarray(raw_img.convert("RGBA"))
    background = np.all(raw_arr == np.asarray(BACKGROUND_RGBA, dtype=raw_arr.dtype), axis=2)
    residual = noe_mask & background
    painted = noe_mask & ~background

    zero = np.zeros((height, width), dtype=bool)
    if diag_img is not None:
        diag_arr = np.asarray(diag_img.convert("RGBA"))
        diag_background = np.all(diag_arr == np.asarray(BACKGROUND_RGBA, dtype=diag_arr.dtype), axis=2)
        diag_cyan = np.all(diag_arr == np.asarray(GAP_RGBA, dtype=diag_arr.dtype), axis=2)
        diag_magenta = np.all(diag_arr == np.asarray(UNASSIGNED_RGBA, dtype=diag_arr.dtype), axis=2)
        diag_classified = ~diag_background & ~diag_cyan & ~diag_magenta
    else:
        diag_background = zero
        diag_cyan = zero
        diag_magenta = zero
        diag_classified = zero

    residual_unassigned = residual & diag_magenta
    residual_gap = residual & diag_cyan
    residual_diag_classified = residual & diag_classified
    residual_outside_diag = residual & (diag_background if diag_img is not None else residual)

    vis = np.full_like(raw_arr, (244, 244, 240, 255))
    vis[noe_mask] = (255, 255, 255, 255)
    vis[painted] = raw_arr[painted]
    vis[residual_outside_diag] = (255, 150, 0, 235)
    vis[residual_diag_classified] = (220, 0, 0, 235)
    vis[residual_gap] = (0, 160, 255, 235)
    vis[residual_unassigned] = (255, 0, 255, 235)

    out_residual = output_prefix.with_name(output_prefix.name + "_noe_boundary_residuals.png")
    fig, ax = plt.subplots(figsize=(12, 9), dpi=250)
    ax.imshow(Image.fromarray(vis, "RGBA"), extent=[minx, maxx, miny, maxy], origin="upper")
    gpd.GeoSeries([land_geom], crs=31287).boundary.plot(ax=ax, color="#111111", linewidth=0.6)
    ax.set_aspect("equal")
    ax.set_title("NÖ-DKM Boundary Residual Diagnostics", fontsize=13)
    ax.set_xlabel("EPSG:31287 X")
    ax.set_ylabel("EPSG:31287 Y")
    handles = [
        Line2D([0], [0], marker="s", linestyle="", color=(1.0, 0.0, 1.0), label="Polygon vorhanden, kein NS-Symbol", markersize=8),
        Line2D([0], [0], marker="s", linestyle="", color=(0.0, 0.63, 1.0), label="In DKM-Bounds, aber kein Polygon", markersize=8),
        Line2D([0], [0], marker="s", linestyle="", color=(1.0, 0.59, 0.0), label="In NÖ, außerhalb aktueller DKM-Coverage", markersize=8),
        Line2D([0], [0], marker="s", linestyle="", color=(0.86, 0.0, 0.0), label="In Diagnose klassifiziert, final weiß", markersize=8),
    ]
    ax.legend(handles=handles, loc="upper right", frameon=True, framealpha=0.92, fontsize=8)
    fig.savefig(out_residual, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)

    structure = np.ones((3, 3), dtype=np.uint8)
    labels, component_count = ndimage.label(residual, structure=structure)
    slices = ndimage.find_objects(labels)
    noe_edge = noe_mask & ~ndimage.binary_erosion(noe_mask, structure=structure, border_value=0)
    min_area_m2 = min_component_area_km2 * 1_000_000.0
    to_wgs84 = Transformer.from_crs(31287, 4326, always_xy=True)

    components = []
    for label_id, slc in enumerate(slices, 1):
        if slc is None:
            continue
        component = labels[slc] == label_id
        pixels = int(component.sum())
        area_m2 = pixels * px * px
        if area_m2 < min_area_m2:
            continue

        rows, cols = np.nonzero(component)
        row0 = slc[0].start
        col0 = slc[1].start
        abs_rows = rows + row0
        abs_cols = cols + col0
        centroid_x = minx + (float(abs_cols.mean()) + 0.5) * px
        centroid_y = maxy - (float(abs_rows.mean()) + 0.5) * px
        lon, lat = to_wgs84.transform(centroid_x, centroid_y)

        total = float(pixels)
        unassigned_count = int((diag_magenta[slc] & component).sum())
        gap_count = int((diag_cyan[slc] & component).sum())
        classified_count = int((diag_classified[slc] & component).sum())
        outside_diag_count = int(((diag_background[slc] if diag_img is not None else component) & component).sum())
        fractions = {
            "unassigned": unassigned_count / total,
            "no_polygon_gap": gap_count / total,
            "diagnostic_classified": classified_count / total,
            "outside_diagnostic_coverage": outside_diag_count / total,
        }

        touches_canvas_edge = (
            slc[0].start == 0 or slc[1].start == 0 or slc[0].stop == height or slc[1].stop == width
        )
        touches_noe_boundary = bool((noe_edge[slc] & component).any())
        components.append({
            "component_id": label_id,
            "reason": reason_for_component(fractions),
            "pixels": pixels,
            "area_m2": area_m2,
            "area_km2": area_m2 / 1_000_000.0,
            "centroid_x_31287": centroid_x,
            "centroid_y_31287": centroid_y,
            "centroid_lon": lon,
            "centroid_lat": lat,
            "bbox_minx_31287": minx + slc[1].start * px,
            "bbox_miny_31287": maxy - slc[0].stop * px,
            "bbox_maxx_31287": minx + slc[1].stop * px,
            "bbox_maxy_31287": maxy - slc[0].start * px,
            "unassigned_ratio": fractions["unassigned"],
            "no_polygon_gap_ratio": fractions["no_polygon_gap"],
            "outside_diagnostic_coverage_ratio": fractions["outside_diagnostic_coverage"],
            "diagnostic_classified_ratio": fractions["diagnostic_classified"],
            "touches_canvas_edge": int(touches_canvas_edge),
            "touches_noe_boundary": int(touches_noe_boundary),
        })

    components.sort(key=lambda row: row["area_m2"], reverse=True)
    if component_limit > 0:
        written_components = components[:component_limit]
    else:
        written_components = components

    out_components = output_prefix.with_name(output_prefix.name + "_noe_boundary_white_components.csv")
    with out_components.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "rank", "component_id", "reason", "pixels", "area_m2", "area_km2",
            "centroid_x_31287", "centroid_y_31287", "centroid_lon", "centroid_lat",
            "bbox_minx_31287", "bbox_miny_31287", "bbox_maxx_31287", "bbox_maxy_31287",
            "unassigned_ratio", "no_polygon_gap_ratio", "outside_diagnostic_coverage_ratio",
            "diagnostic_classified_ratio", "touches_canvas_edge", "touches_noe_boundary",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rank, row in enumerate(written_components, 1):
            out_row = {"rank": rank, **row}
            for key, value in list(out_row.items()):
                if isinstance(value, float):
                    out_row[key] = round(value, 6)
            writer.writerow(out_row)

    stats = {
        "boundary_noe_mask_area_m2_raster": float(noe_mask.sum()) * px * px,
        "boundary_noe_mask_area_km2_raster": float(noe_mask.sum()) * px * px / 1_000_000.0,
        "boundary_painted_area_m2_raster": float(painted.sum()) * px * px,
        "boundary_painted_area_km2_raster": float(painted.sum()) * px * px / 1_000_000.0,
        "boundary_white_residual_area_m2_raster": float(residual.sum()) * px * px,
        "boundary_white_residual_area_km2_raster": float(residual.sum()) * px * px / 1_000_000.0,
        "boundary_white_unassigned_area_m2_raster": float(residual_unassigned.sum()) * px * px,
        "boundary_white_no_polygon_gap_area_m2_raster": float(residual_gap.sum()) * px * px,
        "boundary_white_outside_diagnostic_coverage_area_m2_raster": float(residual_outside_diag.sum()) * px * px,
        "boundary_white_diagnostic_classified_area_m2_raster": float(residual_diag_classified.sum()) * px * px,
        "boundary_white_components_total": int(component_count),
        "boundary_white_components_written": int(len(written_components)),
    }

    out_boundary_stats = output_prefix.with_name(output_prefix.name + "_noe_boundary_stats.csv")
    with out_boundary_stats.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        for key, value in sorted(stats.items()):
            w.writerow([key, round(value, 4) if isinstance(value, float) else value])
    print(f"wrote {out_residual}")
    print(f"wrote {out_components}")
    print(f"wrote {out_boundary_stats}")
    return stats


def main() -> None:
    """Render the NÖ DKM land-use overview from DXF linework and NS symbols.

    Writes into ``OUT_DIR`` a raw PNG, the decorated map (unless
    ``--diagnostics-only``), a diagnostics PNG (with ``--diagnostics``),
    boundary residual diagnostics (with ``--boundary-diagnostics``) and a
    statistics CSV.

    Raises
    ------
    SystemExit
        On incompatible option combinations, if no usable bounds or tile jobs
        are found, or if the canvas would be too large.
    """
    global _WORKER_ZIP, _WORKER_CFG, _WORKER_FILE_CACHE

    ap = argparse.ArgumentParser()
    ap.add_argument("--pixel-size-m", type=float, default=50.0)
    ap.add_argument("--line-layers", "--layers", dest="line_layers", default="GG,NG,KG", help="Comma-separated DXF layers for polygonization")
    ap.add_argument("--limit-files", type=int, default=0)
    ap.add_argument("--precision-m", type=float, default=0.01)
    ap.add_argument("--min-area-m2", type=float, default=4.0)
    ap.add_argument("--raw-coordinates", action="store_true", help="Render raw DXF coordinates without GK->Lambert normalization")
    ap.add_argument("--highlight-unassigned", action="store_true", help="Highlight polygonized areas with no assigned NS symbol on the final map")
    ap.add_argument("--diagnostics", action="store_true", help="Write a diagnostics map: cyan approximate DKM coverage without polygons, magenta unassigned polygons")
    ap.add_argument("--diagnostics-only", action="store_true", help="Only write diagnostics/statistics outputs; skip the final decorated map")
    ap.add_argument("--polygonize-mode", choices=("tile", "file"), default="tile", help="tile joins neighboring DXFs with a halo; file keeps the old per-DXF behavior")
    ap.add_argument("--tile-size-m", type=float, default=20_000.0, help="Tile interior size for --polygonize-mode tile")
    ap.add_argument("--tile-halo-m", type=float, default=2_000.0, help="Overlap around each tile used to close polygons across DXF/KG boundaries")
    ap.add_argument("--render-engine", choices=("auto", "rasterio", "pil"), default="auto", help="Tile render backend; rasterio avoids returning millions of pixel rings")
    ap.add_argument("--worker-cache-files", type=int, default=128, help="Per-worker LRU cache size for parsed/projected DXFs in tile mode; use 0 to disable")
    ap.add_argument("--tile-chunksize", type=int, default=4, help="Multiprocessing chunksize for tile jobs; larger values improve per-worker cache reuse")
    ap.add_argument("--bounds-cache", default="", help="Bounds cache CSV path; default is output/kataster/diagnostics/noe_dkm_bounds_<layers>_<crs>.csv")
    ap.add_argument("--no-bounds-cache", action="store_true", help="Disable reading/writing the DXF bounds cache")
    ap.add_argument("--refresh-bounds-cache", action="store_true", help="Rebuild the DXF bounds cache even if it already exists")
    ap.add_argument("--keep-intermediate", action="store_true", help="Reserved for future tile workflows; no intermediate files are kept by default")
    ap.add_argument("--output-prefix", default="noe_dkm_benutzungsarten_flaechen", help="Output filename prefix in output/kataster/diagnostics/")
    ap.add_argument("--no-clear-holes", action="store_true", help="Do not paint polygon interior rings back to background; diagnostic for white holes")
    ap.add_argument("--include-noe-boundary-line", action="store_true", help="Add the NÖ administrative boundary as linework so border polygons can close")
    ap.add_argument("--boundary-diagnostics", action="store_true", help="Compare the rendered map with the NÖ administrative outline and write white-residual component diagnostics")
    ap.add_argument("--boundary-shapefile", default=str(ADMIN_BOUNDARY_PATH), help="Administrative boundary shapefile for --boundary-diagnostics")
    ap.add_argument("--boundary-min-component-area-km2", type=float, default=0.0, help="Minimum white residual component area written to the boundary CSV")
    ap.add_argument("--boundary-components-limit", type=int, default=500, help="Maximum white residual components written; use 0 for all")
    ap.add_argument("--workers", type=int, default=1, help="Parallel DXF polygonization workers (try 4-8; 1 keeps old serial behavior)")
    args = ap.parse_args()
    if args.diagnostics_only:
        args.diagnostics = True
    if args.boundary_diagnostics and args.diagnostics_only:
        raise SystemExit("--boundary-diagnostics needs the rendered final/raw map; do not combine it with --diagnostics-only")
    if args.raw_coordinates and args.polygonize_mode == "tile":
        raise SystemExit("--polygonize-mode tile requires normalized EPSG:31287 coordinates; use --polygonize-mode file with --raw-coordinates")
    if args.include_noe_boundary_line and args.polygonize_mode != "tile":
        raise SystemExit("--include-noe-boundary-line is implemented for --polygonize-mode tile")
    render_engine = args.render_engine
    if render_engine == "auto":
        render_engine = "rasterio" if (
            args.polygonize_mode == "tile"
            and not args.no_clear_holes
            and np is not None
            and rasterio_rasterize is not None
            and rasterio_from_origin is not None
        ) else "pil"
    if render_engine == "rasterio":
        if args.polygonize_mode != "tile":
            raise SystemExit("--render-engine rasterio is currently only implemented for --polygonize-mode tile")
        if args.no_clear_holes:
            raise SystemExit("--render-engine rasterio does not support --no-clear-holes; use --render-engine pil")
        if np is None or rasterio_rasterize is None or rasterio_from_origin is None:
            raise SystemExit("--render-engine rasterio requires numpy and rasterio")
    if args.keep_intermediate:
        print("NOTE: current workflow creates no intermediate files; --keep-intermediate is reserved for future tile workflows.", flush=True)

    t0 = time.time()
    OUT_DIR.mkdir(exist_ok=True)
    px = args.pixel_size_m
    line_layers = {s.strip() for s in args.line_layers.split(",") if s.strip()}
    suffix = f"{int(px) if px.is_integer() else str(px).replace('.', 'p')}m"
    if args.highlight_unassigned:
        suffix += "_unassigned_highlight"
    if args.no_clear_holes:
        suffix += "_no_clear_holes"
    if args.include_noe_boundary_line:
        suffix += "_noe_boundary_closed"
    out_raw = OUT_DIR / f"{args.output_prefix}_{suffix}_raw.png"
    out_png = OUT_DIR / f"{args.output_prefix}_{suffix}.png"
    out_diag = OUT_DIR / f"{args.output_prefix}_{suffix}_diagnostics.png"
    out_stats = OUT_DIR / f"{args.output_prefix}_{suffix}_stats.csv"

    sym_cat = load_symbol_categories()
    workers = max(1, min(args.workers, os.cpu_count() or 1))
    boundary_lines = load_noe_boundary_lines(Path(args.boundary_shapefile)) if args.include_noe_boundary_line else []
    if boundary_lines:
        print(f"loaded {len(boundary_lines)} NÖ boundary line(s) for polygon closure", flush=True)

    with zipfile.ZipFile(ZIP_PATH) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".dxf")]
        if args.limit_files:
            names = names[:args.limit_files]

        # First pass: bounds from selected linework and NS points.
        bounds_t0 = time.time()
        overall = [math.inf, math.inf, -math.inf, -math.inf]
        coverage_bounds: list[tuple[float, float, float, float]] = []
        file_infos: list[tuple[str, tuple[float, float, float, float]]] = []
        source_file_stats = Counter()
        bounds_cache_path = Path(args.bounds_cache) if args.bounds_cache else default_bounds_cache_path(line_layers, args.raw_coordinates)
        bounds_cache_rows: list[tuple[str, tuple[float, float, float, float], str | None]] = []
        cached_bounds = None
        if not args.no_bounds_cache and not args.refresh_bounds_cache:
            cached_bounds = read_bounds_cache(bounds_cache_path, names)
        if cached_bounds is not None:
            overall, coverage_bounds, file_infos, source_file_stats = cached_bounds
            source_file_stats["bounds_cache_hit"] = 1
            bounds_seconds = time.time() - bounds_t0
            print(f"bounds cache hit {bounds_cache_path}", flush=True)
            print(f"bounds done in {bounds_seconds:.1f}s", flush=True)
        else:
            source_file_stats["bounds_cache_hit"] = 0

            def consume_bounds_result(idx: int, result):
                name, b, tb, strip = result
                source_file_stats["files"] += 1
                if strip:
                    source_file_stats[f"files_{strip}"] += 1
                else:
                    source_file_stats["files_no_bounds"] += 1
                if tb:
                    coverage_bounds.append(tb)
                    file_infos.append((name, tb))
                    bounds_cache_rows.append((name, tb, strip))
                    overall[0] = min(overall[0], tb[0]); overall[1] = min(overall[1], tb[1])
                    overall[2] = max(overall[2], tb[2]); overall[3] = max(overall[3], tb[3])
                if idx % 250 == 0 or idx == len(names):
                    print(f"bounds {idx}/{len(names)}", flush=True)

            if workers > 1:
                print(f"parallel bounds workers={workers}", flush=True)
                with mp.Pool(
                    processes=workers,
                    initializer=_init_bounds_worker,
                    initargs=(str(ZIP_PATH), line_layers, args.raw_coordinates),
                ) as pool:
                    for idx, result in enumerate(pool.imap_unordered(_process_file_for_bounds, names, chunksize=16), 1):
                        consume_bounds_result(idx, result)
            else:
                for idx, name in enumerate(names, 1):
                    b = parse_dxf_bounds_fast(zf.read(name), line_layers)
                    tb = b if args.raw_coordinates else transform_bounds(b)
                    strip = choose_raw_crs(b)[0] if b else None
                    consume_bounds_result(idx, (name, b, tb, strip))
            bounds_seconds = time.time() - bounds_t0
            if not args.no_bounds_cache and bounds_cache_rows:
                write_bounds_cache(bounds_cache_path, bounds_cache_rows)
                print(f"bounds cache wrote {bounds_cache_path}", flush=True)
            print(f"bounds done in {bounds_seconds:.1f}s", flush=True)

        if overall[0] == math.inf:
            raise SystemExit("No usable bounds found")
        minx, miny, maxx, maxy = overall
        pad = px * 4
        minx -= pad; miny -= pad; maxx += pad; maxy += pad
        width = int(math.ceil((maxx - minx) / px))
        height = int(math.ceil((maxy - miny) / px))
        if width * height > 180_000_000:
            raise SystemExit(f"Canvas too large: {width}x{height}; increase --pixel-size-m")
        print(f"canvas {width}x{height} px={px} bounds={(minx,miny,maxx,maxy)}", flush=True)

        img = Image.new("RGBA", (width, height), BACKGROUND_RGBA)
        draw = ImageDraw.Draw(img, "RGBA")
        final_draw = None if args.diagnostics_only else draw
        diag_img = Image.new("RGBA", (width, height), BACKGROUND_RGBA) if args.diagnostics else None
        diag_draw = ImageDraw.Draw(diag_img, "RGBA") if diag_img is not None else None
        if diag_draw is not None:
            for bx0, by0, bx1, by1 in coverage_bounds:
                rect = [
                    int(round((bx0 - minx) / px)),
                    int(round((maxy - by1) / px)),
                    int(round((bx1 - minx) / px)),
                    int(round((maxy - by0) / px)),
                ]
                diag_draw.rectangle(rect, fill=GAP_RGBA)

        stats = Counter()
        area_by_cat = Counter()
        symbol_counts = Counter()
        unmapped_symbols = Counter()
        ambiguous = 0

        render_t0 = time.time()
        if args.polygonize_mode == "tile":
            stats.update(source_file_stats)
            stats["tile_size_m"] = args.tile_size_m
            stats["tile_halo_m"] = args.tile_halo_m
            stats["render_engine"] = render_engine
            stats["worker_cache_files"] = args.worker_cache_files
            stats["include_noe_boundary_line"] = int(bool(boundary_lines))
            stats["noe_boundary_line_count"] = len(boundary_lines)
            tile_jobs = make_tile_jobs(file_infos, minx, miny, maxx, maxy, args.tile_size_m, args.tile_halo_m)
            stats["tiles_total"] = len(tile_jobs)
            print(
                f"tile polygonization tiles={len(tile_jobs)} tile_size={args.tile_size_m:g}m "
                f"halo={args.tile_halo_m:g}m workers={workers} render={render_engine}",
                flush=True,
            )
            if not tile_jobs:
                raise SystemExit("No tile jobs generated")

            if render_engine == "rasterio":
                chunksize = max(1, args.tile_chunksize)
                if workers > 1:
                    with mp.Pool(
                        processes=workers,
                        initializer=_init_worker,
                        initargs=(
                            str(ZIP_PATH),
                            line_layers,
                            sym_cat,
                            args.precision_m,
                            args.min_area_m2,
                            args.raw_coordinates,
                            minx,
                            maxy,
                            px,
                            args.highlight_unassigned,
                            args.diagnostics,
                            args.no_clear_holes,
                            not args.diagnostics_only,
                            args.worker_cache_files,
                            boundary_lines,
                        ),
                    ) as pool:
                        iterator = pool.imap(_process_tile_for_raster, tile_jobs, chunksize=chunksize)
                        for idx, (name, tile_stats, tile_area, tile_unmapped, tile_ambiguous, payload, error) in enumerate(iterator, 1):
                            stats.update(tile_stats)
                            area_by_cat.update(tile_area)
                            unmapped_symbols.update(tile_unmapped)
                            ambiguous += tile_ambiguous
                            if error:
                                print(f"WARN polygonize failed {name}: {error}", flush=True)
                            paste_tile_raster(payload, None if args.diagnostics_only else img, diag_img, minx, maxy, px)
                            if idx % 10 == 0 or idx == len(tile_jobs):
                                print(f"processed tiles {idx}/{len(tile_jobs)} polys={stats['polygons']} assigned={stats['assigned_ns_points']}", flush=True)
                else:
                    _WORKER_ZIP = zf
                    _WORKER_CFG = {
                        "line_layers": line_layers,
                        "sym_cat": sym_cat,
                        "precision_m": args.precision_m,
                        "min_area_m2": args.min_area_m2,
                        "raw_coordinates": args.raw_coordinates,
                        "minx": minx,
                        "maxy": maxy,
                        "px": px,
                        "highlight_unassigned": args.highlight_unassigned,
                        "diagnostics": args.diagnostics,
                        "no_clear_holes": args.no_clear_holes,
                        "render_final": not args.diagnostics_only,
                        "worker_cache_files": args.worker_cache_files,
                        "boundary_lines": boundary_lines,
                    }
                    _WORKER_FILE_CACHE = OrderedDict()
                    for idx, job in enumerate(tile_jobs, 1):
                        name, tile_stats, tile_area, tile_unmapped, tile_ambiguous, payload, error = _process_tile_for_raster(job)
                        stats.update(tile_stats)
                        area_by_cat.update(tile_area)
                        unmapped_symbols.update(tile_unmapped)
                        ambiguous += tile_ambiguous
                        if error:
                            print(f"WARN polygonize failed {name}: {error}", flush=True)
                        paste_tile_raster(payload, None if args.diagnostics_only else img, diag_img, minx, maxy, px)
                        if idx % 10 == 0 or idx == len(tile_jobs):
                            print(f"processed tiles {idx}/{len(tile_jobs)} polys={stats['polygons']} assigned={stats['assigned_ns_points']}", flush=True)
            elif workers > 1:
                with mp.Pool(
                    processes=workers,
                    initializer=_init_worker,
                    initargs=(
                        str(ZIP_PATH),
                        line_layers,
                        sym_cat,
                        args.precision_m,
                        args.min_area_m2,
                        args.raw_coordinates,
                        minx,
                        maxy,
                        px,
                        args.highlight_unassigned,
                        args.diagnostics,
                        args.no_clear_holes,
                        not args.diagnostics_only,
                        args.worker_cache_files,
                        boundary_lines,
                    ),
                ) as pool:
                    iterator = pool.imap(_process_tile_for_render, tile_jobs, chunksize=max(1, args.tile_chunksize))
                    for idx, (name, tile_stats, tile_area, tile_unmapped, tile_ambiguous, draw_by_cat, error) in enumerate(iterator, 1):
                        stats.update(tile_stats)
                        area_by_cat.update(tile_area)
                        unmapped_symbols.update(tile_unmapped)
                        ambiguous += tile_ambiguous
                        if error:
                            print(f"WARN polygonize failed {name}: {error}", flush=True)
                        paint_draw_groups(draw_by_cat, final_draw, diag_draw, args.highlight_unassigned, args.no_clear_holes)
                        if idx % 10 == 0 or idx == len(tile_jobs):
                            print(f"processed tiles {idx}/{len(tile_jobs)} polys={stats['polygons']} assigned={stats['assigned_ns_points']}", flush=True)
            else:
                _WORKER_ZIP = zf
                _WORKER_CFG = {
                    "line_layers": line_layers,
                    "sym_cat": sym_cat,
                    "precision_m": args.precision_m,
                    "min_area_m2": args.min_area_m2,
                    "raw_coordinates": args.raw_coordinates,
                    "minx": minx,
                    "maxy": maxy,
                    "px": px,
                    "highlight_unassigned": args.highlight_unassigned,
                    "diagnostics": args.diagnostics,
                    "no_clear_holes": args.no_clear_holes,
                    "render_final": not args.diagnostics_only,
                    "worker_cache_files": args.worker_cache_files,
                    "boundary_lines": boundary_lines,
                }
                _WORKER_FILE_CACHE = OrderedDict()
                for idx, job in enumerate(tile_jobs, 1):
                    name, tile_stats, tile_area, tile_unmapped, tile_ambiguous, draw_by_cat, error = _process_tile_for_render(job)
                    stats.update(tile_stats)
                    area_by_cat.update(tile_area)
                    unmapped_symbols.update(tile_unmapped)
                    ambiguous += tile_ambiguous
                    if error:
                        print(f"WARN polygonize failed {name}: {error}", flush=True)
                    paint_draw_groups(draw_by_cat, final_draw, diag_draw, args.highlight_unassigned, args.no_clear_holes)
                    if idx % 10 == 0 or idx == len(tile_jobs):
                        print(f"processed tiles {idx}/{len(tile_jobs)} polys={stats['polygons']} assigned={stats['assigned_ns_points']}", flush=True)
        elif workers > 1:
            print(f"parallel polygonization workers={workers}", flush=True)
            with mp.Pool(
                processes=workers,
                initializer=_init_worker,
                initargs=(
                    str(ZIP_PATH),
                    line_layers,
                    sym_cat,
                    args.precision_m,
                    args.min_area_m2,
                    args.raw_coordinates,
                    minx,
                    maxy,
                    px,
                    args.highlight_unassigned,
                    args.diagnostics,
                    args.no_clear_holes,
                    not args.diagnostics_only,
                    0,
                    boundary_lines,
                ),
            ) as pool:
                for idx, (name, file_stats, file_area, file_unmapped, file_ambiguous, draw_by_cat, error) in enumerate(
                    pool.imap(_process_file_for_render, names, chunksize=4), 1
                ):
                    stats.update(file_stats)
                    area_by_cat.update(file_area)
                    unmapped_symbols.update(file_unmapped)
                    ambiguous += file_ambiguous
                    if error:
                        print(f"WARN polygonize failed {name}: {error}", flush=True)
                    paint_draw_groups(draw_by_cat, final_draw, diag_draw, args.highlight_unassigned, args.no_clear_holes)
                    if idx % 50 == 0 or idx == len(names):
                        print(f"processed {idx}/{len(names)} polys={stats['polygons']} assigned={stats['assigned_ns_points']}", flush=True)
        else:
            for idx, name in enumerate(names, 1):
                lines, ns_points, raw_bounds = parse_dxf(zf.read(name), line_layers)
                strip, transformer = choose_raw_crs(raw_bounds)
                stats[f"files_{strip}"] += 1
                stats["files"] += 1
                stats["lines"] += len(lines)
                stats["ns_points"] += len(ns_points)
                if not lines:
                    stats["files_no_lines"] += 1
                    continue
                if not ns_points:
                    stats["files_no_ns"] += 1
                try:
                    merged = precision_union(lines, args.precision_m)
                    polys = [p for p in polygonize(merged) if p.area >= args.min_area_m2]
                except Exception as e:
                    stats["polygonize_errors"] += 1
                    print(f"WARN polygonize failed {name}: {e}", flush=True)
                    continue
                stats["polygons"] += len(polys)
                if not polys:
                    continue

                poly_votes: dict[int, Counter] = defaultdict(Counter)
                point_geoms = [Point(x, y) for _symbol, x, y in ns_points]
                point_to_poly = assign_points_to_polygons(polys, point_geoms)
                for point_i, (symbol, x, y) in enumerate(ns_points):
                    cat = sym_cat.get(symbol)
                    symbol_counts[symbol] += 1
                    if not cat:
                        cat = "Unbekannt"; unmapped_symbols[symbol] += 1
                    best_i = point_to_poly.get(point_i)
                    if best_i is not None:
                        poly_votes[best_i][cat] += 1
                        stats["assigned_ns_points"] += 1
                    else:
                        stats["unassigned_ns_points"] += 1

                assigned_poly_ids = set(poly_votes.keys())
                stats["assigned_polygons"] += len(assigned_poly_ids)
                unassigned_polys = [p for pi, p in enumerate(polys) if pi not in assigned_poly_ids]
                stats["unassigned_polygons"] += len(unassigned_polys)
                stats["unassigned_polygon_area_m2"] += sum(p.area for p in unassigned_polys)

                by_cat: dict[str, list[Polygon]] = defaultdict(list)
                for pi, votes in poly_votes.items():
                    if not votes:
                        continue
                    most = votes.most_common()
                    if len(most) > 1:
                        ambiguous += 1
                    cat = most[0][0]
                    by_cat[cat].append(polys[pi])
                    area_by_cat[cat] += polys[pi].area

                draw_by_cat = defaultdict(list)
                for cat, cat_polys in by_cat.items():
                    for poly in cat_polys:
                        if args.raw_coordinates:
                            exterior = list(poly.exterior.coords)
                            interiors = [list(ring.coords) for ring in poly.interiors]
                        else:
                            exterior, interiors = transform_polygon(poly, transformer)
                        exterior_px = pix_coords(exterior, minx, maxy, px)
                        if len(exterior_px) >= 3:
                            holes_px = [pix_coords(ring, minx, maxy, px) for ring in interiors]
                            draw_by_cat[cat].append((poly.area, exterior_px, [h for h in holes_px if len(h) >= 3]))

                if args.highlight_unassigned or diag_draw is not None:
                    for poly in unassigned_polys:
                        if args.raw_coordinates:
                            exterior = list(poly.exterior.coords)
                        else:
                            exterior, _interiors = transform_polygon(poly, transformer)
                        exterior_px = pix_coords(exterior, minx, maxy, px)
                        if len(exterior_px) >= 3:
                            draw_by_cat["Nicht zugeordnet"].append((poly.area, exterior_px, []))

                paint_draw_groups(draw_by_cat, final_draw, diag_draw, args.highlight_unassigned, args.no_clear_holes)

                if idx % 50 == 0 or idx == len(names):
                    print(f"processed {idx}/{len(names)} polys={stats['polygons']} assigned={stats['assigned_ns_points']}", flush=True)

    polygon_render_seconds = time.time() - render_t0
    print(f"polygon/render done in {polygon_render_seconds:.1f}s", flush=True)

    if not args.diagnostics_only:
        img.save(out_raw)
    diagnostic_gap_area_m2 = 0.0
    diagnostic_coverage_area_m2 = 0.0
    if diag_img is not None:
        if np is not None:
            arr = np.asarray(diag_img)
            alpha = arr[:, :, 3]
            cyan = (arr[:, :, 0] < 40) & (arr[:, :, 1] > 150) & (arr[:, :, 2] > 180) & (alpha > 0)
            non_background = np.any(arr != np.asarray(BACKGROUND_RGBA, dtype=arr.dtype), axis=2)
            diagnostic_gap_area_m2 = float(cyan.sum()) * px * px
            diagnostic_coverage_area_m2 = float(non_background.sum()) * px * px
        else:
            gap_pixels = coverage_pixels = 0
            for r, g, b, a in diag_img.getdata():
                if (r, g, b, a) != BACKGROUND_RGBA:
                    coverage_pixels += 1
                if r < 40 and g > 150 and b > 180 and a > 0:
                    gap_pixels += 1
            diagnostic_gap_area_m2 = gap_pixels * px * px
            diagnostic_coverage_area_m2 = coverage_pixels * px * px
        diag_img.save(out_diag)

    boundary_stats = {}
    if args.boundary_diagnostics:
        output_stem = OUT_DIR / f"{args.output_prefix}_{suffix}"
        boundary_stats = write_boundary_residual_diagnostics(
            img,
            diag_img,
            Path(args.boundary_shapefile),
            minx,
            miny,
            maxx,
            maxy,
            px,
            output_stem,
            args.boundary_min_component_area_km2,
            args.boundary_components_limit,
        )

    # Add axes/title/legend around the raster.
    if not args.diagnostics_only:
        fig, ax = plt.subplots(figsize=(12, 9), dpi=250)
        ax.imshow(img, extent=[minx, maxx, miny, maxy], origin="upper")
        ax.set_aspect("equal")
        layers_label = ",".join(sorted(line_layers))
        ax.set_title(f"NÖ DKM – flächig aus {layers_label} polygonisiert ({suffix} Raster)", fontsize=14)
        if args.raw_coordinates:
            ax.set_xlabel("DXF X-Koordinate")
            ax.set_ylabel("DXF Y-Koordinate")
        else:
            ax.set_xlabel("EPSG:31287 X")
            ax.set_ylabel("EPSG:31287 Y")
        ax.grid(True, color="#dddddd", linewidth=0.3)
        handles = [Line2D([0], [0], marker='s', linestyle='', color=CATEGORY_COLORS[c], label=c, markersize=8)
                   for c in DRAW_ORDER if area_by_cat.get(c, 0) > 0 and c in CATEGORY_COLORS]
        if args.highlight_unassigned and stats.get("unassigned_polygons", 0):
            handles.append(Line2D([0], [0], marker='s', linestyle='', color=CATEGORY_COLORS["Nicht zugeordnet"], label="Nicht zugeordnet", markersize=8))
        ax.legend(handles=handles, loc="upper right", frameon=True, framealpha=0.92, fontsize=8)
        fig.text(0.01, 0.01, "Quelle nur: NÖ-DKM-DXF ZIP + BEV_DKM_DXF_Symbole_V2.6.csv; Polygone aus DKM-Layern " + layers_label + ".", fontsize=7, color="#444444")
        fig.savefig(out_png, bbox_inches="tight", pad_inches=0.08)
        plt.close(fig)

    with out_stats.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        for k, v in sorted(stats.items()):
            w.writerow([k, v])
        w.writerow(["ambiguous_polygons", ambiguous])
        w.writerow(["bounds_seconds", round(bounds_seconds, 1)])
        w.writerow(["polygon_render_seconds", round(polygon_render_seconds, 1)])
        if diag_img is not None:
            w.writerow(["diagnostic_coverage_area_m2_raster", round(diagnostic_coverage_area_m2, 2)])
            w.writerow(["diagnostic_coverage_area_km2_raster", round(diagnostic_coverage_area_m2 / 1_000_000, 4)])
            w.writerow(["diagnostic_gap_area_m2_raster", round(diagnostic_gap_area_m2, 2)])
            w.writerow(["diagnostic_gap_area_km2_raster", round(diagnostic_gap_area_m2 / 1_000_000, 4)])
        for k, v in sorted(boundary_stats.items()):
            w.writerow([k, round(v, 4) if isinstance(v, float) else v])
        w.writerow(["runtime_seconds", round(time.time() - t0, 1)])
        w.writerow([])
        w.writerow(["category", "area_m2", "area_km2"])
        for cat, area in area_by_cat.most_common():
            w.writerow([cat, round(area, 2), round(area / 1_000_000, 4)])
        w.writerow([])
        w.writerow(["unmapped_symbol", "count"])
        for sym, count in unmapped_symbols.most_common():
            w.writerow([sym, count])

    if not args.diagnostics_only:
        print(f"wrote {out_png}")
    if diag_img is not None:
        print(f"wrote {out_diag}")
    if not args.diagnostics_only:
        print(f"wrote {out_raw}")
    print(f"wrote {out_stats}")
    print("stats", dict(stats), "ambiguous_polygons", ambiguous, "unmapped", dict(unmapped_symbols), flush=True)


if __name__ == "__main__":
    main()
