"""Shared primitives for the Widmung-based Abschichtung pipeline (3-script variant:
build_official_zoning_layers.py, build_widmung_osm_layers.py,
create_widmung_wka_distance_zones.py in scripts/main/).

Intentionally duplicated from scripts/main/create_osm_wka_distance_zones.py rather than
imported from it: scripts/main/ is not an importable package, and the original script is
the working production pipeline that this variant must not risk touching. A future cleanup
could point the old script at this module too, but that is out of scope here.
"""
from __future__ import annotations

import gc
import hashlib
import json
import math
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

import geopandas as gpd
from affine import Affine
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import LineString, Polygon, box
from shapely.ops import linemerge, unary_union

from calc.distance_engine import fft_circle_dilation
from calc.wind_zones import load_wind_zones

TARGET_CRS = "EPSG:31287"


@contextmanager
def timed(label: str):
    """Context manager that prints start, end and duration of a step.

    Parameters
    ----------
    label : str
        Name of the step in the ``[start]``/``[done]`` lines.

    Yields
    ------
    None
        Control returns to the ``with`` block; the ``[done]`` line is printed
        even if the block raises.
    """
    t0 = time.perf_counter()
    print(f"[start] {label}", flush=True)
    try:
        yield
    finally:
        print(f"[done]  {label}: {time.perf_counter() - t0:.1f}s", flush=True)


# ---------------------------------------------------------------------------
# Buffer distance constants
# ---------------------------------------------------------------------------

# Einheitlich 1.000 m; nur NÖ 1.200 m (dort schreibt das SekROP den
# 1.200-m-Mindestabstand zu gewidmetem Wohnbauland vor).
SETTLEMENT_BUFFER_BY_BL = {
    "Burgenland": 1000.0,
    "Niederösterreich": 1200.0,
    "Oberösterreich": 1000.0,
    "Steiermark": 1000.0,
    "Kärnten": 1000.0,
    "Salzburg": 1000.0,
    "Tirol": 1000.0,
    "Vorarlberg": 1000.0,
    "Wien": 1000.0,
}

INDIVIDUAL_BUFFER_BY_BL = {
    "Burgenland": 750.0,
    "Niederösterreich": 750.0,
    "Oberösterreich": 750.0,
    "Steiermark": 750.0,
    "Kärnten": 750.0,
    "Salzburg": 750.0,
    "Tirol": 750.0,
    "Vorarlberg": 750.0,
    "Wien": 750.0,
}

# NEW for the Widmung pipeline: flat buffer for OSM buildings that are neither
# official Wohnbau/Mischnutzung, Häuser-im-Grünen-und-Ähnliches, nor one of the
# three special sub-categories below (important objects/cableway/address cluster).
# Effectively just excludes the building footprint (one 25m cell), no real setback.
GENERAL_BUILDING_BUFFER_M = 25.0

# Named settlement-setback variants -> one resulting available-zone band each in
# the final output GeoTIFF (available_cleaned_min_<ha>ha_<variant>). Value semantics:
#   "baseline"       -> the Bundesland-specific SETTLEMENT_BUFFER_BY_BL above
#   a number         -> uniform metres for all Bundesländer
#   {Bundesland: m}  -> per-BL, missing Bundesländer fall back to the baseline
# Lifted from create_osm_wka_distance_zones.py's identically-named constant.
SETTLEMENT_BUFFER_VARIANTS = {
    "default": "baseline",
    "800m": 800.0,
    "1000m": 1000.0,
    "1200m": 1200.0,
    "1500m": 1500.0,
    "2000m": 2000.0,
}

# Wien and Burgenland have no official zoning source in data/widmung/;
# they keep the OSM address-seed settlement/greenland detection as a fallback.
WIEN_BGLD_FALLBACK_BL = {"Wien", "Burgenland"}

# Die amtlichen Windkraft-Zonen je Bundesland sind in calc/wind_zones.py
# registriert (WIND_ZONE_SOURCES) und werden über load_wind_zones() geladen.

GREENLAND_BUILDING_TYPES = {
    "chapel",
    "wayside_chapel",
    "church",
    "castle",
    "monastery",
    "ruins",
    "ruin",
}

# "Wichtige Einzelobjekte" (Kirchen/Kapellen/Burgen/Klöster/Ruinen + Friedhöfe):
# NUR NOCH v1-Kette. Die v2-Kette hat die Kategorie entfernt - Denkmal- und
# Ortsbildschutz ist kein Immissionsabstand, und für 11.065 der Objekte außerhalb
# NÖs gab es ohnehin keine amtliche Quelle, die den Ausschluss stützt. Die
# betroffenen OSM-Gebäude laufen dort jetzt als general_buildings (25 m) mit,
# Friedhofsflächen entfallen ganz.
IMPORTANT_OBJECT_BUILDING_TYPES = GREENLAND_BUILDING_TYPES
CEMETERY_LANDUSE_VALUES = {"cemetery"}
IMPORTANT_OBJECT_BUFFER_M = 250.0

# Cableway/lift station buildings: identified by proximity to an OSM aerialway
# line, not by building type (OSM rarely tags a dedicated building=* for these).
CABLEWAY_BUILDING_MATCH_RADIUS_M = 100.0
CABLEWAY_BUILDING_BUFFER_M = 50.0

# Addressed-building "Ansammlungen" (Streusiedlungen) nationwide: connected
# clusters of addressed buildings with no official Wohnbau/Häuser-im-Grünen
# coverage and no required OSM place-seed touch (unlike address_seed_settlement_
# masks' Wien/Burgenland fallback), folded into haeuser_im_gruenen_source once a
# cluster reaches this many addressed buildings.
ADDRESS_CLUSTER_EPS_M = 150.0
ADDRESS_CLUSTER_MIN_BUILDINGS = 3

# ---------------------------------------------------------------------------
# Häuser-im-Grünen v2 (amtliche Quellen statt OSM-Adress-Cluster)
# See plans/haeuser-im-gruenen-v2.md and calc/hig_detection.py.
# ---------------------------------------------------------------------------

# Kandidaten-Filter: DKM-Bauflächen innerhalb dieses Radius um amtliche Siedlung,
# HiG-Widmung oder Ferienhaus-Widmung gelten als durch die Widmung erfasst und
# werden NICHT zu Hüllen-Kandidaten. KEIN Abschichtungspuffer - der Radius wählt
# nur aus, was noch zu erkennen ist.
HIG_FILTER_BUFFER_M = 500.0

# Verkettungsdistanz des morphologischen Closings (Dilation = Hälfte, Erosion =
# Dilation − 35 m Saum, siehe streusiedlung.chain_hull_params). 200 m ist das
# Knie der DBSCAN-Analyse (scripts/analysis/streusiedlung_knee.py): k-Distanz-
# Knie k=4 bei 184 m, Knie der ε-Sweep-Anteilskurve für T=5 bei 200 m.
HIG_CHAIN_M = 200.0

# Streusiedlungs-Schwelle: bewohnte Hüllen mit mindestens so vielen adressierten
# Objekten sind Streusiedlungs-GEBIETE (750 m); bewohnte Hüllen darunter sind
# Einzellagen und bekommen nur noch den Gebäude-Fußabdruck-Abstand (25 m).
# Politikentscheidung (Juli 2026): Gebiets-Schutz statt Einzelobjekt-Schutz -
# die NÖ-Validierung zeigt, dass damit bewusst auf die Abdeckung amtlich
# geschützter Einzelhöfe verzichtet wird (48 % statt 95 % GWR-Abdeckung).
HIG_MIN_ADRESSEN = 5
BEWOHNT_EINZELLAGE_BUFFER_M = 25.0

# Signalradien je Kandidatengebäude.
HIG_ADDRESS_RADIUS_M = 100.0
HIG_GARDEN_RADIUS_M = 150.0

# DKM-Footprints darüber sind NÖ-DXF-Polygonisierungsartefakte (809 Fälle,
# größtes 735 ha). Bis W5.P2: ausnahmslos durch eine 5-m-Scheibe um ihren
# Zentroid ersetzt. Seit W5.P2 (08.09.2026, Punkt 34, Nutzerentscheidung):
# nur noch, wenn der Footprint mindestens eine BEV-Adresse im EIGENEN Polygon
# trägt. Ohne eine solche Adresse entfällt der Kandidat vollständig - keine
# Scheibe, keine Hüllen-Mitgliedschaft (calc/hig_detection.py:
# scan_dkm_candidates(), address_xy-Parameter). Kein representative_point(),
# kein neuer Schwellwert - siehe dortiger Docstring.
HIG_MAX_FOOTPRINT_M2 = 10_000.0

# Unter diesem Wohnanteil (BEV-Eigenschaft 01/02/03 unter den erreichten
# BEV-Gebäuden) gilt eine adressierte Hülle als industriegebietartig.
HIG_WOHNANTEIL_MIN_SHARE = 0.1

# Ab diesem Anteil Mitgliedsgebäude in Betriebs-/Industriewidmung gilt die
# Widmung als Industriesignal. Gebäudeweise, nicht als Hüllen-Überlappung:
# eine um 75 m dilatierte Hülle berührt Nachbarwidmungen viel zu leicht.
HIG_INDUSTRIE_WIDMUNG_MIN_SHARE = 0.5

# Ferienhaus-/Tourismusgebiete bekommen NICHT den vollen Siedlungsabstand,
# sondern denselben 750-m-Wert wie die übrigen Häuser im Grünen.
FERIENHAUS_BUFFER_M = 750.0

# Industriegebietartige und unbewohnte Hüllen: nur der Fußabdruck, kein Abstand.
NONRESIDENTIAL_HULL_BUFFER_M = 25.0

# Clean-Schema (Aug 2026): EIN gemeinsamer 750-m-Puffer für die ganze
# Häuser-im-Grünen-Familie (Ferienhaus/Tourismus, amtliche HiG-Widmung,
# Streusiedlungs-Hüllen, wichtige Einzelobjekte). Ersetzt die frühere Mischung
# aus FERIENHAUS_BUFFER_M, INDIVIDUAL_BUFFER_BY_BL und IMPORTANT_OBJECT_BUFFER_M
# (250 m) in der v2-Kette; v1 und die OSM-Kette bleiben bei ihren Konstanten.
HIG_FAMILY_BUFFER_M = 750.0

# An-/Abflugkorridore ersetzen in der v2-Kette das Paar airport_area +
# airport_lateral_check_6km: nur die Hauptflughäfen aus
# config buffers.major_airport_osm_ids zählen; je Landebahn-Ende ein
# Kreissektor entlang der verlängerten Bahnachse. Achsen kürzer als
# AIRPORT_RUNWAY_MIN_LENGTH_M (Helipads, Reststummel) bekommen keinen Sektor.
AIRPORT_CORRIDOR_LENGTH_M = 5000.0
AIRPORT_CORRIDOR_HALF_ANGLE_DEG = 15.0
AIRPORT_RUNWAY_MIN_LENGTH_M = 500.0

# Windkraftanlagen sind keine Gebäude im Sinne der Abschichtung. OSM taggt
# einzelne Anlagen zusätzlich mit `building=yes` auf DERSELBEN Way/Relation, die
# auch `power=generator` + `generator:source=wind` trägt (Ö-Stand 2026-03: 12
# Anlagen). Diese landen dadurch im Gebäude-Layer, fallen durch alle vier
# Klassifikationsstufen und schließen als "allgemeines Gebäude" mit 25 m Puffer
# ihren eigenen Standort aus - obwohl bestehende WKA-Standorte gerade die besten
# Flächen sind (Repowering) und nie ein Ausschlussgrund sein dürfen.
#
# Historische `man_made=windmill` (Retzer Windmühle u.a.) bleiben bewusst
# Gebäude: das sind echte Bauwerke, keine Kraftwerke.
WIND_POWER_OSM_FILTERS = ["nwr/generator:source=wind", "nwr/man_made=wind_turbine"]

# Hochzählen, sobald sich das ERGEBNIS der Gebäudeklassifikation ändert, ohne
# dass sich ein Parameter ändert (neuer Eingangsfilter o.ä.). Der Wert landet als
# Tag in den Checkpoint-Layern; ohne ihn würden vorhandene distance_layers/*.tif
# als gültig gelten und die Änderung stillschweigend nicht wirksam werden.
# 6 (09.09.2026, W7.5): PEOPLE_CARRYING_AERIALWAY_TYPES eingeengt auf gondola/
# cable_car/chair_lift/mixed_lift - is_cableway in build_osm_building_sources()
# (pipeline/layers/osm.py) liest denselben Filter, das ERGEBNIS der
# Klassifikation ändert sich also, ohne dass ein Prep-Eingang sich ändert
# (genau der hier dokumentierte Fall). Invalidiert die ganze Gruppe
# "v2 OSM building classification + angehängte Bänder 40/41"
# (cableway_buildings_source, general_buildings_source,
# general_buildings_roh_osm, general_buildings_roh_dkm) automatisch, ohne
# manuelles Löschen der Checkpoint-Dateien.
BUILDING_CLASSIFICATION_REVISION = "6-personenseilbahn-typen-eingeengt"

PLACE_TYPES = {
    "city",
    "town",
    "village",
    "hamlet",
    "suburb",
    "neighbourhood",
    "isolated_dwelling",
}

ADDRESS_REQUIRED_TAGS = ["addr:housenumber", "addr:city", "addr:postcode"]
ADDRESS_TAGS = ADDRESS_REQUIRED_TAGS + ["addr:street", "addr:place", "addr:country", "building", "name"]

# Nutzerentscheidung vom 09.09.2026 (W7.5): Personenseilbahnen sind genau
# diese vier OSM-``aerialway``-Typen - KEINE Schlepplifte (drag_lift, t-bar,
# j-bar, platter, rope_tow), KEIN magic_carpet, und nichts aus goods,
# zip_line, explosive, avalanche, pylon, station, yes, proposed, abandoned,
# deflection_roller. Vorher (bis W7.5) enthielt diese Liste zusätzlich die
# fünf Schlepplift-Varianten und magic_carpet (zehn statt vier Typen).
#
# Einzige Definitionsstelle fuer alle drei Verbraucher (Auftrag W7.5: "eine
# Liste, an einer Stelle definiert, von allen drei Verbrauchern gelesen"):
#   - Band 17 cableway_people_150m: pipeline/layers/osm.py,
#     build_infrastructure_masks()
#   - Band 10 cableway_buildings_source: pipeline/layers/osm.py,
#     build_osm_building_sources()
#   - Band 42 sources_human: pipeline/layers/geo.py
PEOPLE_CARRYING_AERIALWAY_TYPES = {
    "gondola",
    "cable_car",
    "chair_lift",
    "mixed_lift",
}

MILITARY_AREA_TYPES = {
    "barracks",
    "range",
    "training_area",
    "danger_area",
    "airfield",
    "naval_base",
    "base",
}

INFRA_RULES = {
    "power_380_400kv": {"standard": 150.0, "minimum": 150.0, "source": "OSM/GPKG power voltage 380/400 kV"},
    "road_motorway_trunk": {"standard": 150.0, "minimum": 150.0, "source": "OSM roads motorway/trunk, tunnels excluded"},
    "road_federal_state": {"standard": 150.0, "minimum": 150.0, "source": "OSM roads primary/secondary/tertiary, tunnels excluded"},
    "rail_main": {"standard": 150.0, "minimum": 150.0, "source": "OSM railway=rail/narrow_gauge, tunnels excluded"},
    "cableway_people_150m": {"standard": 150.0, "minimum": 150.0, "source": "OSM PBF people-carrying aerialways/lifts, material cableways excluded"},
    "airport_lateral_check_6km": {"standard": 6000.0, "minimum": 6000.0, "source": "OSM major airports only; lateral/check area, not 15-km radial taboo"},
}

NATURE_BANDS = ["nature_protection_areas", "osm_nature_protection_areas"]
GEOGRAPHY_BANDS = ["geography_slope_too_steep", "geography_elevation_too_high", "geography_wind_too_low"]
OFFICIAL_ZONING_BANDS = ["official_wind_zoning"]

# Größere Wasserkörper (Seen, Stauseen, Flussläufe) sind kein WKA-Standort -
# reiner Fußabdruck-Ausschluss ohne Abstand, zählt zum Geography-Aggregat.
# Eigene Band-Gruppe statt Erweiterung von GEOGRAPHY_BANDS, damit v1/OSM-Kette
# (die build_geography_masks unverändert nutzen) nicht brechen. Die Schwelle
# wirkt auf ZUSAMMENHÄNGENDE Rasterflächen, nicht pro OSM-Feature: Flussufer
# sind in OSM in viele kleine Teilpolygone gestückelt, die erst verkettet
# "größer" sind. Kleinteiche/Pools/Bäche darunter bleiben bewusst drin.
WATER_BANDS = ["geography_water_bodies"]
WATER_MIN_AREA_HA = 1.0


# ---------------------------------------------------------------------------
# Grid / bounds
# ---------------------------------------------------------------------------

def expand_bounds(bounds, distance_m: float):
    """Grow a bounding box by a distance on all four sides.

    Parameters
    ----------
    bounds : tuple of float
        ``(minx, miny, maxx, maxy)``.
    distance_m : float
        Distance in metres.

    Returns
    -------
    tuple of float
        The expanded ``(minx, miny, maxx, maxy)``.
    """
    minx, miny, maxx, maxy = bounds
    d = float(distance_m)
    return (minx - d, miny - d, maxx + d, maxy + d)


def parse_bbox(text: str | None):
    """Parse a ``--bbox`` argument.

    Parameters
    ----------
    text : str or None
        ``"minx,miny,maxx,maxy"``.

    Returns
    -------
    tuple of float or None
        The four values, or ``None`` if ``text`` is empty.

    Raises
    ------
    ValueError
        If ``text`` does not contain exactly four values.
    """
    if not text:
        return None
    vals = [float(x.strip()) for x in text.split(",")]
    if len(vals) != 4:
        raise ValueError("--bbox must be minx,miny,maxx,maxy")
    return tuple(vals)


def load_grid(cfg: dict, bbox_text: str | None) -> dict:
    """Derive the target grid from the DGM (fallback: the wind raster).

    Parameters
    ----------
    cfg : dict
        Loaded config; reads ``cfg["paths"]["dgm"]`` or
        ``cfg["paths"]["wind_pd_150"]``.
    bbox_text : str or None
        Optional ``--bbox``; restricts the grid to the matching window of the
        template raster.

    Returns
    -------
    dict
        ``shape``, ``transform``, ``crs`` and ``bounds`` of the grid.
    """
    template = Path(cfg["paths"].get("dgm") or cfg["paths"].get("wind_pd_150"))
    bbox = parse_bbox(bbox_text)
    with rasterio.open(template) as src:
        if bbox:
            win = from_bounds(*bbox, transform=src.transform).round_offsets().round_lengths()
            transform = src.window_transform(win)
            height, width = int(win.height), int(win.width)
            bounds = rasterio.windows.bounds(win, src.transform)
        else:
            transform = src.transform
            height, width = src.height, src.width
            bounds = src.bounds
        crs = src.crs
    return {"shape": (height, width), "transform": transform, "crs": crs, "bounds": bounds}


# ---------------------------------------------------------------------------
# OSM PBF plumbing
# ---------------------------------------------------------------------------

OSM_PBF_FILTERS = {
    "buildings": ["w/building", "r/building"],
    "landuse": ["w/landuse", "r/landuse"],
    "roads": ["w/highway"],
    "railways": ["w/railway"],
    "powerlines": ["w/power=line", "w/power=minor_line"],
    "transport": ["n/aeroway", "w/aeroway", "r/aeroway"],
    "aerialways": ["n/aerialway", "w/aerialway", "r/aerialway"],
    "military": ["w/landuse=military", "r/landuse=military", "w/military", "r/military"],
    "nature": [
        "w/boundary=protected_area", "r/boundary=protected_area",
        "w/boundary=national_park", "r/boundary=national_park",
        "w/leisure=nature_reserve", "r/leisure=nature_reserve",
        "w/protect_class", "r/protect_class",
        "w/protection_title", "r/protection_title",
    ],
    "places": [*(f"n/place={x}" for x in sorted(PLACE_TYPES)), *(f"w/place={x}" for x in sorted(PLACE_TYPES)), *(f"r/place={x}" for x in sorted(PLACE_TYPES))],
    "addresses": ["n/addr:housenumber", "w/addr:housenumber", "r/addr:housenumber"],
    "windpower": WIND_POWER_OSM_FILTERS,
    # natural=water ist das moderne Schema (water=lake/reservoir/river im
    # Subtag); waterway=riverbank und landuse=reservoir sind die noch
    # verbreiteten Altschemata für Flussufer bzw. Stauseen.
    "water": [
        "w/natural=water", "r/natural=water",
        "w/waterway=riverbank", "r/waterway=riverbank",
        "w/landuse=reservoir", "r/landuse=reservoir",
    ],
}

OSM_PBF_INCLUDE_TAGS = {
    "buildings": ["building"],
    "landuse": ["landuse"],
    "roads": ["highway", "tunnel"],
    "railways": ["railway", "tunnel"],
    "powerlines": ["power", "voltage"],
    "transport": ["aeroway"],
    "aerialways": ["aerialway"],
    "military": ["landuse", "military"],
    "nature": ["boundary", "leisure", "protect_class", "protection_title", "name"],
    "places": ["place", "name"],
    "addresses": ADDRESS_TAGS,
    "windpower": ["power", "generator:source", "man_made", "name"],
    "water": ["natural", "water", "waterway", "landuse", "name"],
}

# Kein "windpower"-Eintrag: der Layer ist winzig (Ö-weit ~1.600 Objekte, davon
# nur ~13 Ways/Relations) und wird ohne Spaltenauswahl gelesen. `osmium export` schreibt nur Tags, die
# mindestens ein Objekt trägt - eine feste Spaltenliste würde bei pyogrio
# scheitern, sobald ein Tag (z.B. man_made) im Ausschnitt gar nicht vorkommt.
# "water" fehlt aus demselben Grund: in Bbox-Ausschnitten ohne Stausee kommt
# z.B. landuse=reservoir gar nicht vor; gebraucht wird ohnehin nur die Geometrie.
OSM_PBF_COLUMNS = {
    "buildings": ["building", "geometry"],
    "landuse": ["landuse", "geometry"],
    "roads": ["highway", "tunnel", "geometry"],
    "railways": ["railway", "tunnel", "geometry"],
    "powerlines": ["power", "voltage", "geometry"],
    "transport": ["aeroway", "id", "@id", "geometry"],
    "aerialways": ["aerialway", "geometry"],
    "military": ["landuse", "military", "geometry"],
    "nature": ["boundary", "leisure", "protect_class", "protection_title", "name", "geometry"],
    "places": ["place", "name", "geometry"],
    "addresses": ADDRESS_TAGS + ["geometry"],
}

OSM_PBF_CACHE: dict[str, Path] = {}
OSM_PBF_EXTRACT_CACHE: dict[tuple, Path] = {}

# Grid (m) used to snap an already layer-expanded bounds_31287 outward before it
# enters the PBF cache key. Every call site expands grid["bounds"] by its own
# margin (0-6000 m, see INFRA_RULES/rule_distance) before calling
# pbf_for_bounds(); snapping erases that per-caller noise so all call sites
# land on the identical canonical box regardless of which margin they applied.
# Verified against the real Austria grid bounds: min/max corners sit far enough
# from any 10-km line that every margin 0-6000 m snaps to the same box.
PBF_CLIP_SNAP_M = 10000.0

# Fixed key/clip margin added to the snapped canonical box. One shared cache
# stem for the source PBF, no matter which caller asks first - layer-precise
# filtering happens later in read_layer(bounds=...), so a generous fixed clip
# is safe. Value = the largest total margin any call site needs: 6000 m
# (airport_lateral_check_6km, the largest INFRA_RULES/rule_distance entry)
# plus the historical 7000 m reprojection-safety buffer.
PBF_CLIP_MARGIN_M = 13000.0


def _run_osmium(cmd: list[str]) -> None:
    """Run an osmium command.

    Parameters
    ----------
    cmd : list of str
        Command and arguments.

    Raises
    ------
    subprocess.CalledProcessError
        If the command exits with a non-zero status.
    """
    subprocess.run(cmd, check=True)


def osm_cache_stem(key: tuple) -> str:
    """Derive the on-disk cache stem for a PBF clip key.

    NEVER use the builtin hash() here. PYTHONHASHSEED randomises str hashing per
    process, so an identical bbox mints a fresh stem on every run, the on-disk
    cache never hits, and each run re-extracts the whole PBF under a new name.
    That bug grew output/abschichtung/osm_pbf_layers to 124 GB: 141 distinct
    stems for a handful of real bboxes, ~95 GB of byte-identical duplicates.
    Keep this a content hash so the cache stays bounded.

    Mirrored in scripts/main/create_osm_wka_distance_zones.py — fix both together.
    """
    return "clip_" + hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12]


def pbf_for_bounds(osm_pbf: str, osm_pbf_cache_dir: str, bounds_31287=None) -> Path:
    """Return the full PBF or a spatially clipped PBF for the current grid bounds.

    bounds_31287 arrives already expanded by the caller's own layer-specific
    margin (0-6000 m depending on call site). To guarantee ONE shared cache
    stem for the source PBF regardless of that margin, the incoming bounds are
    first snapped outward to PBF_CLIP_SNAP_M (erasing the per-caller margin
    noise) and then expanded by the fixed PBF_CLIP_MARGIN_M - never by the
    caller-supplied margin directly. See PBF_CLIP_MARGIN_M docstring above.
    """
    pbf = Path(osm_pbf) if osm_pbf else Path("")
    if not pbf.exists() or bounds_31287 is None:
        return pbf
    snap = PBF_CLIP_SNAP_M
    snapped = (
        math.floor(bounds_31287[0] / snap) * snap,
        math.floor(bounds_31287[1] / snap) * snap,
        math.ceil(bounds_31287[2] / snap) * snap,
        math.ceil(bounds_31287[3] / snap) * snap,
    )
    minx, miny, maxx, maxy = expand_bounds(snapped, PBF_CLIP_MARGIN_M)
    west, south, east, north = transform_bounds(TARGET_CRS, "EPSG:4326", minx, miny, maxx, maxy, densify_pts=21)
    key = (str(pbf), (round(west, 7), round(south, 7), round(east, 7), round(north, 7)))
    if key in OSM_PBF_EXTRACT_CACHE:
        return OSM_PBF_EXTRACT_CACHE[key]
    cache = Path(osm_pbf_cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    stem = osm_cache_stem(key)
    clipped = cache / f"{stem}.osm.pbf"
    if not clipped.exists() or clipped.stat().st_mtime < pbf.stat().st_mtime:
        bbox = f"{west},{south},{east},{north}"
        print(f"[start] spatial OSM PBF extract bbox={bbox}", flush=True)
        _run_osmium(["osmium", "extract", "--bbox", bbox, str(pbf), "-O", "-o", str(clipped)])
        print(f"[done]  spatial OSM PBF extract: {clipped}", flush=True)
    OSM_PBF_EXTRACT_CACHE[key] = clipped
    return clipped


def osm_layer_path(cfg: dict, osm_pbf: str, osm_pbf_cache_dir: str, layer: str, bounds_31287=None) -> Path:
    """Return an OSM-derived layer path, extracting it from the full/clipped PBF if available."""
    pbf = pbf_for_bounds(osm_pbf, osm_pbf_cache_dir, bounds_31287)
    if not pbf.exists():
        osm = Path(cfg["paths"]["osm_dir"])
        legacy = {
            "buildings": "gis_osm_buildings_a_free_1.shp",
            "landuse": "gis_osm_landuse_a_free_1.shp",
            "roads": "gis_osm_roads_free_1.shp",
            "railways": "gis_osm_railways_free_1.shp",
            "powerlines": "gis_osm_powerlines_free_1.shp",
            "transport": "gis_osm_transport_a_free_1.shp",
        }
        if layer not in legacy:
            return Path("__missing_osm_layer__")
        return osm / legacy[layer]
    cache_key = f"{pbf.stem}_{layer}"
    if cache_key in OSM_PBF_CACHE:
        return OSM_PBF_CACHE[cache_key]
    cache = Path(osm_pbf_cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    filtered = cache / f"{cache_key}.osm.pbf"
    exported = cache / f"{cache_key}.geojsonseq"
    if not exported.exists() or exported.stat().st_mtime < pbf.stat().st_mtime:
        filters = OSM_PBF_FILTERS[layer]
        export_config = {
            "attributes": {"type": False, "id": True, "version": False, "changeset": False, "timestamp": False, "uid": False, "user": False, "way_nodes": False},
            "format_options": {},
            "linear_tags": True,
            "area_tags": True,
            "exclude_tags": [],
            "include_tags": OSM_PBF_INCLUDE_TAGS[layer],
        }
        print(f"[start] extract OSM PBF layer {layer}: {' '.join(filters)}", flush=True)
        _run_osmium(["osmium", "tags-filter", str(pbf), *filters, "-O", "-o", str(filtered)])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as cfg_file:
            json.dump(export_config, cfg_file)
            cfg_path = cfg_file.name
        try:
            _run_osmium(["osmium", "export", str(filtered), "-c", cfg_path, "-O", "-f", "geojsonseq", "-o", str(exported)])
        finally:
            Path(cfg_path).unlink(missing_ok=True)
        print(f"[done]  extract OSM PBF layer {layer}: {exported}", flush=True)
    OSM_PBF_CACHE[cache_key] = exported
    return exported


def read_layer(path: Path, bounds=None, where: str | None = None, columns: list[str] | None = None) -> gpd.GeoDataFrame:
    """Read a vector layer, normalise a few OSM columns and reproject it.

    Parameters
    ----------
    path : Path
        Vector file.
    bounds : tuple of float, optional
        If given, only features intersecting this box are kept.
    where : str, optional
        Attribute filter passed to ``geopandas.read_file``.
    columns : list of str, optional
        Columns to read; only applied to GeoJSON(Seq), FlatGeobuf and
        GeoPackage files.

    Returns
    -------
    geopandas.GeoDataFrame
        Features in ``TARGET_CRS``. Empty if the file is missing, is a GeoJSON
        file smaller than 10 bytes, or has no features. Without an ``fclass``
        column it is filled from the first of ``landuse``, ``highway``,
        ``railway``, ``aeroway``, ``aerialway``; without ``type`` it is filled
        from ``building``.
    """
    if not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    # Empty osmium GeoJSONSeq exports (no features in the bbox) are 0-1 bytes and
    # make pyogrio raise DataSourceError; treat such tiny files as an empty layer.
    if path.suffix.lower() in {".geojsonseq", ".geojson", ".json"} and path.stat().st_size < 10:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    kwargs = {"where": where}
    if columns and path.suffix.lower() in {".geojsonseq", ".geojson", ".json", ".fgb", ".gpkg"}:
        kwargs["columns"] = columns
    try:
        gdf = gpd.read_file(path, **{k: v for k, v in kwargs.items() if v is not None})
    except TypeError:
        gdf = gpd.read_file(path)
        if where and "fclass" in gdf.columns and "residential" in where:
            gdf = gdf[gdf["fclass"].astype(str).eq("residential")]
    if gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    if "fclass" not in gdf.columns:
        for tag in ("landuse", "highway", "railway", "aeroway", "aerialway"):
            if tag in gdf.columns:
                gdf["fclass"] = gdf[tag]
                break
    if "type" not in gdf.columns and "building" in gdf.columns:
        gdf["type"] = gdf["building"]
    gdf = gdf.to_crs(TARGET_CRS)
    if bounds is not None:
        bbox_geom = box(*bounds)
        gdf = gdf[gdf.geometry.intersects(bbox_geom)].copy()
    return gdf


def drop_wind_power_buildings(buildings: gpd.GeoDataFrame, wind_power: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return `buildings` without the rows that are in fact wind turbines.

    OSM carries `building=yes` on the same way/relation as the wind generator
    tags for a handful of Austrian turbines, so they arrive in the buildings
    layer and would exclude their own site as an "allgemeines Gebäude" (see
    WIND_POWER_OSM_FILTERS for the full rationale).

    The match is geometric, not tag-based, because the buildings layer keeps
    only the `building` tag (OSM_PBF_INCLUDE_TAGS) and therefore cannot tell a
    turbine from a shed. That is safe here: turbine footprints in the Austrian
    extract are <= 50 m², so an `intersects` predicate cannot swallow anything
    larger. `power=plant` (wind-farm outlines, many hectares) is deliberately
    NOT part of WIND_POWER_OSM_FILTERS for exactly that reason.

    Never mutates its inputs - returns the original object when there is
    nothing to drop, otherwise a filtered copy.
    """
    turbines = wind_power[wind_power.geometry.notnull() & ~wind_power.geometry.is_empty] if not wind_power.empty else wind_power
    if buildings.empty or turbines.empty:
        # Loud on purpose: an empty wind layer usually means the OSM extract is
        # missing/stale, not that Austria has no turbines.
        print(f"[info]  wind turbines removed from OSM building layer: 0 ({len(turbines):,} OSM wind-power objects in bounds)", flush=True)
        return buildings

    hits = gpd.sjoin(buildings[["geometry"]], turbines[["geometry"]], how="inner", predicate="intersects")
    drop_index = hits.index.unique()
    if len(drop_index) == 0:
        print(f"[info]  wind turbines removed from OSM building layer: 0 of {len(buildings):,} buildings ({len(turbines):,} OSM wind-power objects in bounds, none tagged as a building)", flush=True)
        return buildings

    kept = buildings.loc[~buildings.index.isin(drop_index)].copy()
    print(
        "[info]  wind turbines removed from OSM building layer: "
        f"{len(drop_index):,} of {len(buildings):,} buildings "
        f"({len(turbines):,} OSM wind-power objects in bounds)",
        flush=True,
    )
    return kept


# ---------------------------------------------------------------------------
# Raster/vector buffering primitives
# ---------------------------------------------------------------------------

def raster_mask(gdf: gpd.GeoDataFrame, buffer_m: float, grid: dict, label: str = "buffer") -> np.ndarray:
    """Rasterise geometries and dilate them by a circular buffer.

    Rasterises with ``all_touched=True`` on a padded grid, dilates with
    ``fft_circle_dilation()`` (tile size 2048) and crops back to the grid.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Geometries to rasterise.
    buffer_m : float
        Buffer radius in metres; ``0`` rasterises without buffer.
    grid : dict
        Target grid.
    label : str, optional
        Label for progress messages, default ``"buffer"``.

    Returns
    -------
    numpy.ndarray
        Boolean mask of the grid's shape; all ``False`` if nothing is
        rasterised.
    """
    shape = grid["shape"]
    if gdf.empty:
        return np.zeros(shape, dtype=bool)

    cell_m = max(abs(float(grid["transform"].a)), abs(float(grid["transform"].e)))
    radius_px = int(np.ceil(float(buffer_m) / cell_m)) if buffer_m > 0 else 0
    pad_px = max(radius_px + 2, 2)
    height, width = shape
    padded_shape = (height + 2 * pad_px, width + 2 * pad_px)
    padded_transform = grid["transform"] * Affine.translation(-pad_px, -pad_px)

    seed = rasterize(
        ((geom, 1) for geom in gdf.geometry if geom is not None and not geom.is_empty),
        out_shape=padded_shape,
        transform=padded_transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    ).astype(bool)
    if not seed.any():
        return np.zeros(shape, dtype=bool)
    if buffer_m > 0:
        dilated = fft_circle_dilation(seed, float(buffer_m), cell_m, 2048, label)
    else:
        dilated = seed
    return dilated[pad_px : pad_px + height, pad_px : pad_px + width].astype(bool)


def admin_boundaries(cfg: dict, bounds) -> gpd.GeoDataFrame:
    """Read the administrative boundaries (VGD) for a bounding box.

    Parameters
    ----------
    cfg : dict
        Loaded config; reads ``cfg["paths"]["vgd"]``.
    bounds : tuple of float or None
        Bounding box passed to ``read_layer()``.

    Returns
    -------
    geopandas.GeoDataFrame
        The boundaries; the first of ``BL_NAME``, ``NAME``, ``NAME_1``,
        ``bundesland`` is renamed to ``BL`` if no ``BL`` column exists.
    """
    vgd = Path(cfg["paths"]["vgd"])
    gdf = read_layer(vgd, bounds=bounds)
    if gdf.empty:
        return gdf
    if "BL" not in gdf.columns:
        for col in ["BL_NAME", "NAME", "NAME_1", "bundesland"]:
            if col in gdf.columns:
                gdf = gdf.rename(columns={col: "BL"})
                break
    return gdf


def add_bl_by_centroid(gdf: gpd.GeoDataFrame, bl: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Assign each feature the Bundesland that contains its centroid.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Features to tag.
    bl : geopandas.GeoDataFrame
        Bundesland polygons with a ``BL`` column.

    Returns
    -------
    geopandas.GeoDataFrame
        Copy of ``gdf`` with a ``BL`` column; ``None`` throughout if ``gdf`` or
        ``bl`` is empty or ``bl`` has no ``BL`` column.
    """
    if gdf.empty or bl.empty or "BL" not in bl.columns:
        return gdf.assign(BL=None)
    pts = gpd.GeoDataFrame(gdf.drop(columns="geometry"), geometry=gdf.geometry.centroid, crs=gdf.crs)
    joined = gpd.sjoin(pts, bl[["BL", "geometry"]], how="left", predicate="within")
    out = gdf.copy()
    out["BL"] = joined["BL"].values
    return out


def province_buffer_mask(gdf: gpd.GeoDataFrame, bl: gpd.GeoDataFrame, distances: dict[str, float], grid: dict) -> np.ndarray:
    """Buffer features with a per-Bundesland distance.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Features to buffer.
    bl : geopandas.GeoDataFrame
        Bundesland polygons, used by ``add_bl_by_centroid()``.
    distances : dict of str to float
        Buffer distance in metres per Bundesland; entries ``<= 0`` are skipped.
    grid : dict
        Target grid.

    Returns
    -------
    numpy.ndarray
        Boolean union of the buffered masks of all Bundesländer.
    """
    out = np.zeros(grid["shape"], dtype=bool)
    tagged = add_bl_by_centroid(gdf, bl)
    if "BL" not in tagged.columns:
        return out
    for bl_name, dist in distances.items():
        if dist <= 0:
            continue
        sub = tagged[tagged["BL"].eq(bl_name)]
        out |= raster_mask(sub, dist, grid, f"{bl_name} {dist:g}m")
    return out


def province_buffer_cell_mask(source_mask: np.ndarray, bl: gpd.GeoDataFrame, distances: dict[str, float], grid: dict) -> np.ndarray:
    """Buffer source raster cells with province-specific distances."""
    out = np.zeros(grid["shape"], dtype=bool)
    if not source_mask.any() or bl.empty or "BL" not in bl.columns:
        return out
    cell_m = max(abs(float(grid["transform"].a)), abs(float(grid["transform"].e)))
    for bl_name, dist in distances.items():
        if dist <= 0:
            continue
        sub = bl[bl["BL"].eq(bl_name)]
        if sub.empty:
            continue
        province_mask = raster_mask(sub[["geometry"]].copy(), 0.0, grid, f"{bl_name} province")
        seed = source_mask & province_mask
        if not seed.any():
            continue
        out |= fft_circle_dilation(seed, float(dist), cell_m, 2048, f"{bl_name} {dist:g}m")
    return out.astype(bool)


def uniform_buffer_cell_mask(source_mask: np.ndarray, distance_m: float, grid: dict, label: str = "uniform buffer") -> np.ndarray:
    """Buffer source raster cells with one distance nationwide (no per-BL lookup)."""
    if not source_mask.any() or distance_m <= 0:
        return np.zeros(grid["shape"], dtype=bool)
    cell_m = max(abs(float(grid["transform"].a)), abs(float(grid["transform"].e)))
    return fft_circle_dilation(source_mask, float(distance_m), cell_m, 2048, label).astype(bool)


def resolve_variant_buffers(spec) -> dict[str, float]:
    """Resolve a settlement-buffer variant spec to a full Bundesland->metres dict.

    ``"baseline"``/``None`` -> SETTLEMENT_BUFFER_BY_BL; a number -> uniform for all
    Bundesländer; a dict -> per-BL with SETTLEMENT_BUFFER_BY_BL as fallback.
    """
    base = {bl: float(v) for bl, v in SETTLEMENT_BUFFER_BY_BL.items()}
    if spec is None or spec == "baseline":
        return base
    if isinstance(spec, (int, float)):
        return {bl: float(spec) for bl in base}
    if isinstance(spec, dict):
        merged = dict(base)
        merged.update({bl: float(v) for bl, v in spec.items()})
        return merged
    raise ValueError(f"invalid settlement buffer variant spec: {spec!r}")


def variant_band_names(source_band: str, vname: str, min_fragment_area_ha: float) -> list[str]:
    """The four per-variant output bands: buffer, human total, raw and cleaned area."""
    return [
        f"{source_band}_{vname}",
        f"exclusion_human_{vname}",
        f"available_after_all_exclusions_raw_{vname}",
        f"available_cleaned_min_{min_fragment_area_ha:g}ha_{vname}",
    ]


# ---------------------------------------------------------------------------
# Point/cell helpers (building classification)
# ---------------------------------------------------------------------------

def _point_rows_cols(points: np.ndarray, transform: Affine) -> tuple[np.ndarray, np.ndarray]:
    """Convert point coordinates to raster rows and columns.

    Parameters
    ----------
    points : numpy.ndarray
        Coordinates of shape ``(N, 2)``.
    transform : affine.Affine
        Raster transform.

    Returns
    -------
    rows : numpy.ndarray
        Row indices (``int64``, floored).
    cols : numpy.ndarray
        Column indices (``int64``, floored).
    """
    inv = ~transform
    cols_f, rows_f = inv * (points[:, 0], points[:, 1])
    return np.floor(rows_f).astype(np.int64), np.floor(cols_f).astype(np.int64)


def building_points(buildings: gpd.GeoDataFrame) -> np.ndarray:
    """Representative point of each building.

    Parameters
    ----------
    buildings : geopandas.GeoDataFrame
        Building geometries.

    Returns
    -------
    numpy.ndarray
        Coordinates of shape ``(N, 2)``; ``(0, 2)`` if ``buildings`` is empty.
    """
    if buildings.empty:
        return np.zeros((0, 2), dtype=np.float64)
    return np.array([(p.x, p.y) for p in buildings.geometry.representative_point()], dtype=np.float64)


def sample_mask_at_points(mask: np.ndarray, points: np.ndarray, grid: dict) -> np.ndarray:
    """For each point, return whether it falls on a True cell of `mask` (False outside grid)."""
    if len(points) == 0:
        return np.zeros(0, dtype=bool)
    rows, cols = _point_rows_cols(points, grid["transform"])
    height, width = grid["shape"]
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    out = np.zeros(len(points), dtype=bool)
    out[inside] = mask[rows[inside], cols[inside]]
    return out


def _building_count_grid(buildings: gpd.GeoDataFrame, grid: dict) -> np.ndarray:
    """Count buildings per grid cell.

    Parameters
    ----------
    buildings : geopandas.GeoDataFrame
        Building geometries; counted at their representative point.
    grid : dict
        Target grid.

    Returns
    -------
    numpy.ndarray
        ``uint16`` count per cell; points outside the grid are ignored.
    """
    if buildings.empty:
        return np.zeros(grid["shape"], dtype=np.uint16)
    pts = building_points(buildings)
    rows, cols = _point_rows_cols(pts, grid["transform"])
    h, w = grid["shape"]
    inside = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
    counts = np.zeros((h, w), dtype=np.uint16)
    np.add.at(counts, (rows[inside], cols[inside]), 1)
    return counts


def _rasterize_points(gdf: gpd.GeoDataFrame, grid: dict) -> np.ndarray:
    """Rasterise the representative point of each feature.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Features.
    grid : dict
        Target grid.

    Returns
    -------
    numpy.ndarray
        Boolean mask, ``True`` in every cell that contains a point.
    """
    if gdf.empty:
        return np.zeros(grid["shape"], dtype=bool)
    pts = gdf.geometry.representative_point()
    return rasterize(
        ((geom, 1) for geom in pts if geom is not None and not geom.is_empty),
        out_shape=grid["shape"],
        transform=grid["transform"],
        fill=0,
        dtype="uint8",
        all_touched=True,
    ).astype(bool)


def greenland_flags_from_settlement_cells(buildings: gpd.GeoDataFrame, settlement_source: np.ndarray, grid: dict, greenland_min_distance_m: float) -> np.ndarray:
    """Flag buildings farther than the threshold from settlement source cells."""
    if buildings.empty:
        return np.zeros(0, dtype=bool)
    pts = building_points(buildings)
    rows, cols = _point_rows_cols(pts, grid["transform"])
    height, width = grid["shape"]
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    out = np.zeros(len(buildings), dtype=bool)
    if not settlement_source.any():
        out[inside] = True
        return out
    cell_x = abs(float(grid["transform"].a))
    cell_y = abs(float(grid["transform"].e))
    dist = ndimage.distance_transform_edt(~settlement_source, sampling=(cell_y, cell_x))
    out[inside] = dist[rows[inside], cols[inside]] > float(greenland_min_distance_m)
    return out


def _filter_postal_addresses(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep OSM address objects with housenumber, city and postcode."""
    if gdf.empty:
        return gdf
    mask = pd.Series(True, index=gdf.index)
    for col in ADDRESS_REQUIRED_TAGS:
        if col not in gdf.columns:
            return gdf.iloc[0:0].copy()
        mask &= gdf[col].fillna("").astype(str).str.strip().ne("")
    return gdf[mask].copy()


def _addressed_buildings_from_osm_addresses(addresses: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Match OSM postal address points/polygons to building polygons."""
    if addresses.empty or buildings.empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    address_points = addresses.copy()
    address_points.geometry = address_points.geometry.representative_point()
    joined = gpd.sjoin(
        address_points[["geometry"]],
        buildings.reset_index()[["index", "geometry"]],
        how="inner",
        predicate="within",
    )
    if joined.empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    return buildings.loc[sorted(joined["index"].dropna().unique())].copy()


def address_seed_settlement_masks(
    buildings: gpd.GeoDataFrame,
    residential_landuse: gpd.GeoDataFrame,
    places: gpd.GeoDataFrame,
    addresses: gpd.GeoDataFrame,
    grid: dict,
    settlement_dbscan_eps_m: float,
) -> tuple[np.ndarray, np.ndarray, gpd.GeoDataFrame]:
    """Cluster addressed buildings, using OSM place objects as settlement seeds.

    Lifted from create_osm_wka_distance_zones.py::_address_seed_settlement_masks
    (args namespace replaced by an explicit settlement_dbscan_eps_m parameter).
    """
    addresses = _filter_postal_addresses(addresses)
    addressed_buildings = _addressed_buildings_from_osm_addresses(addresses, buildings)
    counts = _building_count_grid(addressed_buildings, grid)
    occupied = counts > 0
    landuse_source = raster_mask(residential_landuse[["geometry"]].copy(), 0.0, grid, "landuse_residential_source") if not residential_landuse.empty else np.zeros(grid["shape"], dtype=bool)
    if places.empty:
        seed_cells = np.zeros(grid["shape"], dtype=bool)
    else:
        place_type = places.get("place", pd.Series("", index=places.index)).fillna("").astype(str).str.lower()
        seed_cells = _rasterize_points(places[place_type.isin(PLACE_TYPES)].copy(), grid)

    cell_m = max(abs(float(grid["transform"].a)), abs(float(grid["transform"].e)))
    if occupied.any():
        connected_surface = fft_circle_dilation(occupied, float(settlement_dbscan_eps_m), cell_m, 2048, "address_seed_settlement_connect")
        labels, nlabels = ndimage.label(connected_surface, structure=np.ones((3, 3), dtype=bool))
        seed_touch = fft_circle_dilation(seed_cells, float(settlement_dbscan_eps_m), cell_m, 2048, "address_seed_match") if seed_cells.any() else np.zeros(grid["shape"], dtype=bool)
        seeded_ids = np.unique(labels[seed_touch & (labels > 0)])
        seeded_components = np.isin(labels, seeded_ids)
        seeded_settlement_source = connected_surface & seeded_components
    else:
        nlabels = 0
        seeded_ids = np.array([], dtype=np.int32)
        seeded_components = np.zeros(grid["shape"], dtype=bool)
        seeded_settlement_source = np.zeros(grid["shape"], dtype=bool)

    settlement_source = seeded_settlement_source
    individual_addressed_source = occupied & ~seeded_components
    print(
        "[info]  address-seed settlement: "
        f"postal_addresses={len(addresses):,}, addressed_buildings={len(addressed_buildings):,}, "
        f"occupied_cells={int(occupied.sum()):,}, place_seed_cells={int(seed_cells.sum()):,}, "
        f"components={int(nlabels):,}, seeded_components={len(seeded_ids):,}, "
        f"landuse_cells={int(landuse_source.sum()):,}, settlement_cells={int(settlement_source.sum()):,}, "
        f"individual_address_cells={int(individual_addressed_source.sum()):,}",
        flush=True,
    )
    return settlement_source.astype(bool), individual_addressed_source.astype(bool), addressed_buildings


def address_cluster_mask(
    buildings: gpd.GeoDataFrame,
    addresses: gpd.GeoDataFrame,
    grid: dict,
    eps_m: float,
    min_buildings: int,
) -> np.ndarray:
    """Cell mask of addressed-building clusters ("Ansammlungen") nationwide.

    Unlike address_seed_settlement_masks(), this does NOT require a connected
    component to touch an OSM place seed - it only requires the component to
    contain at least `min_buildings` addressed buildings. This is meant to catch
    small unofficial Streusiedlungen (a handful of addressed farmhouses/hamlets)
    that official Wohnbau/Häuser-im-Grünen zoning and the place-seed-gated
    Wien/Burgenland fallback both miss. A single isolated addressed building does
    NOT qualify - that stays in the general/minor buildings bucket.
    """
    addresses = _filter_postal_addresses(addresses)
    addressed_buildings = _addressed_buildings_from_osm_addresses(addresses, buildings)
    if addressed_buildings.empty:
        return np.zeros(grid["shape"], dtype=bool)
    counts = _building_count_grid(addressed_buildings, grid)
    occupied = counts > 0
    if not occupied.any():
        return np.zeros(grid["shape"], dtype=bool)
    cell_m = max(abs(float(grid["transform"].a)), abs(float(grid["transform"].e)))
    connected_surface = fft_circle_dilation(occupied, float(eps_m), cell_m, 2048, "address_cluster_connect")
    labels, nlabels = ndimage.label(connected_surface, structure=np.ones((3, 3), dtype=bool))
    if nlabels == 0:
        return np.zeros(grid["shape"], dtype=bool)
    comp_counts = ndimage.sum(counts, labels=labels, index=np.arange(1, nlabels + 1))
    qualifying_ids = np.flatnonzero(comp_counts >= int(min_buildings)) + 1
    mask = np.isin(labels, qualifying_ids) & connected_surface
    print(
        "[info]  address cluster ('Ansammlungen'): "
        f"addressed_buildings={len(addressed_buildings):,}, occupied_cells={int(occupied.sum()):,}, "
        f"components={int(nlabels):,}, qualifying_components={len(qualifying_ids):,}, "
        f"eps_m={float(eps_m):g}, min_buildings={int(min_buildings)}, mask_cells={int(mask.sum()):,}",
        flush=True,
    )
    return mask.astype(bool)


# ---------------------------------------------------------------------------
# Infrastructure / airport masks (unchanged rules, lifted verbatim)
# ---------------------------------------------------------------------------

def rule_distance(name: str, mode: str, total_height_m: float) -> float:
    """Buffer distance of an infrastructure rule.

    Parameters
    ----------
    name : str
        Key in ``INFRA_RULES``.
    mode : str
        ``"standard"`` or ``"minimum"``.
    total_height_m : float
        Total turbine height, used by the placeholders ``"h"`` and
        ``"max(h,100)"``.

    Returns
    -------
    float
        The distance in metres.
    """
    val = INFRA_RULES[name][mode]
    if val == "max(h,100)":
        return max(total_height_m, 100.0)
    if val == "h":
        return total_height_m
    return float(val)


def _voltage_tokens_kv(voltage: object) -> set[int]:
    """Parse OSM voltage values into integer kV tokens (values below 1000 are volts, not kV)."""
    tokens: set[int] = set()
    for raw in str(voltage or "").replace(",", ";").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            volts = float(raw)
        except ValueError:
            continue
        if volts >= 1000:
            tokens.add(int(round(volts / 1000.0)))
    return tokens


def _non_tunnel_mask(gdf: gpd.GeoDataFrame) -> np.ndarray:
    """Mark features that are not tunnels.

    Only the OSM ``tunnel`` tag is checked.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Line features.

    Returns
    -------
    numpy.ndarray
        Boolean array, ``True`` where ``tunnel`` is empty, ``no``, ``false`` or
        ``0``; all ``True`` if the column is missing.
    """
    if gdf.empty or "tunnel" not in gdf.columns:
        return np.ones(len(gdf), dtype=bool)
    tunnel = gdf["tunnel"].fillna("").astype(str).str.lower().str.strip()
    return tunnel.isin(["", "no", "false", "0"]).to_numpy()


def _power_line_mask(power: gpd.GeoDataFrame, allowed_kv: set[int]) -> np.ndarray:
    """Select power lines of the given voltages.

    Parameters
    ----------
    power : geopandas.GeoDataFrame
        OSM power features.
    allowed_kv : set of int
        Accepted voltages in kV.

    Returns
    -------
    numpy.ndarray
        Boolean array, ``True`` for (Multi)LineStrings tagged ``power=line``
        (if the column exists) with at least one voltage token in
        ``allowed_kv``.
    """
    if power.empty:
        return np.zeros(0, dtype=bool)
    geom_ok = power.geometry.geom_type.isin(["LineString", "MultiLineString"]).to_numpy()
    power_class = power.get("power", pd.Series("", index=power.index)).fillna("").astype(str).str.lower()
    power_ok = power_class.eq("line").to_numpy() if "power" in power.columns else geom_ok
    voltage_ok = power.get("voltage", pd.Series("", index=power.index)).apply(lambda v: bool(_voltage_tokens_kv(v) & allowed_kv)).to_numpy()
    return geom_ok & power_ok & voltage_ok


# build_infrastructure_masks() (Roads/rail/power/cableway/military) stand
# hier bis W7.5 als woertliche Kopie aus der alten Kette
# (create_osm_wka_distance_zones.py), aber ohne Aufrufer: die neue Kette
# ruft ausschliesslich pipeline/layers/osm.py:build_infrastructure_masks()
# (eigene, mit _read_prep_layer() statt osm_layer_path()+read_layer())
# auf - siehe deren Docstring "Original-Zeilen ~966-972". W7.5 hat diese
# tote Kopie entfernt (Auftrag: "ein Legacy-Zwilling derselben
# [PEOPLE_CARRYING_AERIALWAY_TYPES-]Liste ... ihn stehen zu lassen waere
# eine zweite Wahrheit") - sie las dieselbe Konstante wie die aktive
# Fassung, war also fuer die Daten kein Divergenzrisiko, aber als
# unerreichbarer, ungetesteter Zweitleser einer entscheidungsrelevanten
# Konstante trotzdem irrefuehrend. Die drei privaten Hilfsfunktionen
# _voltage_tokens_kv/_non_tunnel_mask/_power_line_mask bleiben: sie werden
# von der aktiven Fassung importiert (pipeline/layers/osm.py,
# pipeline/layers/geo.py) und sind nicht tot.


def build_airport_masks(cfg: dict, grid: dict, args) -> dict[str, np.ndarray]:
    """Unchanged from create_osm_wka_distance_zones.py.

    `args` needs attributes: osm_pbf, osm_pbf_cache_dir, mode, total_height_m.
    """
    transport_bounds = expand_bounds(grid["bounds"], rule_distance("airport_lateral_check_6km", args.mode, args.total_height_m))
    transport = read_layer(osm_layer_path(cfg, args.osm_pbf, args.osm_pbf_cache_dir, "transport", transport_bounds), bounds=transport_bounds, columns=OSM_PBF_COLUMNS["transport"])
    if transport.empty:
        zero = np.zeros(grid["shape"], dtype=bool)
        return {"airport_area": zero, "airport_lateral_check_6km": zero}

    aeroway = transport.get("fclass", pd.Series("", index=transport.index)).fillna("").astype(str).str.lower()
    area_types = {"aerodrome", "runway", "taxiway", "apron", "terminal", "hangar"}
    airport_area = transport[aeroway.isin(area_types)]

    ids = {str(x) for x in cfg.get("buffers", {}).get("_major_airport_ids", cfg.get("buffers", {}).get("major_airport_osm_ids", []))}
    id_col = "osm_id" if "osm_id" in transport.columns else ("@id" if "@id" in transport.columns else None)
    if ids and id_col:
        major = transport[transport[id_col].astype(str).isin(ids)]
    else:
        major = transport.iloc[0:0]
    if major.empty:
        major = transport[aeroway.eq("aerodrome")]
    return {
        "airport_area": raster_mask(airport_area, 0.0, grid, "airport_area"),
        "airport_lateral_check_6km": raster_mask(major, rule_distance("airport_lateral_check_6km", args.mode, args.total_height_m), grid, "airport_lateral_check_6km"),
    }


def _runway_axes(runways: gpd.GeoDataFrame) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Längsachse (Endpunkt-Paar) je Landebahn.

    OSM mappt Landebahnen teils als Linien (oft segmentiert), teils als
    Flächen: Liniensegmente werden per linemerge zu durchgehenden Bahnen
    verbunden, Flächen liefern ihre Achse über die Mittelpunkte der kurzen
    Seiten des minimal umschriebenen Rechtecks. Ist dieselbe Bahn doppelt
    gemappt (Linie UND Fläche), entstehen doppelte Achsen - harmlos, die
    Sektoren werden ohnehin vereinigt.
    """
    lines: list = []
    polys: list = []
    for geom in runways.geometry:
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "LineString":
            lines.append(geom)
        elif geom.geom_type == "MultiLineString":
            lines.extend(geom.geoms)
        elif geom.geom_type in ("Polygon", "MultiPolygon"):
            polys.extend(geom.geoms if geom.geom_type == "MultiPolygon" else [geom])

    axes: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if lines:
        merged = linemerge(unary_union(lines))
        parts = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
        for line in parts:
            coords = list(line.coords)
            axes.append(((coords[0][0], coords[0][1]), (coords[-1][0], coords[-1][1])))
    for poly in polys:
        corners = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
        mid = lambda p, q: ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
        if LineString([corners[0], corners[1]]).length <= LineString([corners[1], corners[2]]).length:
            axes.append((mid(corners[0], corners[1]), mid(corners[2], corners[3])))
        else:
            axes.append((mid(corners[1], corners[2]), mid(corners[3], corners[0])))
    return axes


def _corridor_wedge(apex: tuple[float, float], direction: tuple[float, float], length_m: float, half_angle_deg: float) -> Polygon:
    """Kreissektor mit Spitze `apex`, geöffnet ±half_angle_deg um `direction`."""
    base = math.atan2(direction[1], direction[0])
    half = math.radians(half_angle_deg)
    arc = [
        (apex[0] + length_m * math.cos(base + t), apex[1] + length_m * math.sin(base + t))
        for t in np.linspace(-half, half, 13)
    ]
    return Polygon([apex, *arc])


def build_airport_corridor_masks(cfg: dict, grid: dict, args) -> dict[str, np.ndarray]:
    """v2-Flughafenbänder: Hauptflughafen-Areale + landebahn-orientierte Korridore.

    Ersetzt build_airport_masks() in der v2-Kette: statt aller OSM-Flugplätze
    plus 6-km-Lateral-Check nur noch die Hauptflughäfen aus
    config buffers.major_airport_osm_ids, und je Landebahn-Ende ein
    AIRPORT_CORRIDOR_LENGTH_M langer Sektor ±AIRPORT_CORRIDOR_HALF_ANGLE_DEG
    um die verlängerte Bahnachse. `args` needs: osm_pbf, osm_pbf_cache_dir.
    """
    zero = np.zeros(grid["shape"], dtype=bool)
    transport_bounds = expand_bounds(grid["bounds"], AIRPORT_CORRIDOR_LENGTH_M)
    transport = read_layer(
        osm_layer_path(cfg, args.osm_pbf, args.osm_pbf_cache_dir, "transport", transport_bounds),
        bounds=transport_bounds, columns=OSM_PBF_COLUMNS["transport"],
    )
    if transport.empty:
        print("[warn]  airport corridors: transport layer empty", flush=True)
        return {"airport_area_major": zero, "airport_runway_corridor_5km": zero}

    aeroway = transport.get("fclass", pd.Series("", index=transport.index)).fillna("").astype(str).str.lower()
    is_aerodrome = aeroway.eq("aerodrome") & transport.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ids = {str(x) for x in cfg.get("buffers", {}).get("_major_airport_ids", cfg.get("buffers", {}).get("major_airport_osm_ids", []))}
    id_col = next((c for c in ("osm_id", "id", "@id") if c in transport.columns), None)
    major = transport[is_aerodrome & transport[id_col].astype(str).isin(ids)] if (ids and id_col) else transport.iloc[0:0]
    if major.empty:
        n = len(ids) or 6
        candidates = transport[is_aerodrome]
        major = candidates.loc[candidates.geometry.area.sort_values(ascending=False).head(n).index]
        print(f"[warn]  airport corridors: major_airport_osm_ids nicht im Transport-Layer gefunden - Fallback auf die {len(major)} größten Aerodrome-Flächen", flush=True)

    runways = transport[aeroway.eq("runway")]
    if not major.empty and not runways.empty:
        major_zone = unary_union(list(major.geometry)).buffer(300.0)
        runways = runways[runways.geometry.intersects(major_zone)]

    wedges: list[Polygon] = []
    for (ax, ay), (bx, by) in _runway_axes(runways):
        length = math.hypot(bx - ax, by - ay)
        if length < AIRPORT_RUNWAY_MIN_LENGTH_M:
            continue
        u = ((bx - ax) / length, (by - ay) / length)
        wedges.append(_corridor_wedge((bx, by), u, AIRPORT_CORRIDOR_LENGTH_M, AIRPORT_CORRIDOR_HALF_ANGLE_DEG))
        wedges.append(_corridor_wedge((ax, ay), (-u[0], -u[1]), AIRPORT_CORRIDOR_LENGTH_M, AIRPORT_CORRIDOR_HALF_ANGLE_DEG))
    print(
        f"[info]  airport corridors: majors={len(major)}, runways={len(runways)}, "
        f"achsen-sektoren={len(wedges)} (±{AIRPORT_CORRIDOR_HALF_ANGLE_DEG:g}°, {AIRPORT_CORRIDOR_LENGTH_M:g} m)",
        flush=True,
    )

    corridor = gpd.GeoDataFrame(geometry=wedges, crs=TARGET_CRS)
    return {
        "airport_area_major": raster_mask(major[["geometry"]].copy(), 0.0, grid, "airport_area_major") if not major.empty else zero,
        "airport_runway_corridor_5km": raster_mask(corridor, 0.0, grid, "airport_runway_corridor_5km") if wedges else zero,
    }


# ---------------------------------------------------------------------------
# Valid-area, nature, geography, official-zoning masks (unchanged)
# ---------------------------------------------------------------------------

def build_valid_area_mask(cfg: dict, grid: dict) -> np.ndarray:
    """Mask of the area covered by the administrative boundaries.

    Parameters
    ----------
    cfg : dict
        Loaded config.
    grid : dict
        Target grid.

    Returns
    -------
    numpy.ndarray
        Boolean mask; all ``True`` if no boundaries are found.
    """
    admin = admin_boundaries(cfg, grid["bounds"])
    if admin.empty:
        return np.ones(grid["shape"], dtype=bool)
    return raster_mask(admin, 0.0, grid, "valid_area")


def _build_official_nature_mask(cfg: dict, grid: dict) -> np.ndarray:
    """Official protection-area exclusions: national parks, NSG, ESG/Natura2000, Ramsar."""
    paths = cfg.get("paths", {})
    nsg_zip = Path(paths.get("nsg_zip", ""))
    nsg_gpkg = paths.get("nsg_gpkg")
    nsg_layers = paths.get("nsg_layers", [])
    if not nsg_zip.exists() or not nsg_gpkg or not nsg_layers:
        return np.zeros(grid["shape"], dtype=bool)

    parts = []
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with zipfile.ZipFile(nsg_zip) as z:
                z.extract(nsg_gpkg, tmpdir)
            gpkg_path = Path(tmpdir) / nsg_gpkg
            for layer in nsg_layers:
                try:
                    gdf = gpd.read_file(gpkg_path, layer=layer)
                except Exception as exc:
                    print(f"Warnung: Naturschutz-Layer {layer} konnte nicht gelesen werden: {exc}")
                    continue
                if not gdf.empty:
                    parts.append(gdf[["geometry"]])
        except Exception as exc:
            print(f"Warnung: Naturschutzdaten konnten nicht gelesen werden: {exc}")
            return np.zeros(grid["shape"], dtype=bool)

    if not parts:
        return np.zeros(grid["shape"], dtype=bool)
    nature = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=parts[0].crs)
    nature = nature[nature.geometry.notnull() & ~nature.geometry.is_empty]
    nature = nature.to_crs(TARGET_CRS)
    nature = nature[nature.geometry.intersects(box(*grid["bounds"]))].copy()
    return raster_mask(nature, 0.0, grid, "nature_protection_areas")


def _build_osm_nature_mask(cfg: dict, grid: dict, args) -> np.ndarray:
    """OSM-derived nature/protection polygons. `args` needs: osm_pbf, osm_pbf_cache_dir."""
    osm = read_layer(osm_layer_path(cfg, args.osm_pbf, args.osm_pbf_cache_dir, "nature", grid["bounds"]), bounds=grid["bounds"], columns=OSM_PBF_COLUMNS["nature"])
    if osm.empty:
        return np.zeros(grid["shape"], dtype=bool)

    geom_area = osm.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    boundary = osm.get("boundary", pd.Series("", index=osm.index)).fillna("").astype(str).str.lower()
    leisure = osm.get("leisure", pd.Series("", index=osm.index)).fillna("").astype(str).str.lower()
    protect_class = osm.get("protect_class", pd.Series("", index=osm.index)).fillna("").astype(str).str.lower()
    title = osm.get("protection_title", pd.Series("", index=osm.index)).fillna("").astype(str).str.lower()
    name = osm.get("name", pd.Series("", index=osm.index)).fillna("").astype(str).str.lower()
    text = title + " " + name

    requested_text = text.str.contains(
        r"naturschutz|natura\s*2000|natura2000|vogelschutz|bird|landschaftsschutz|nationalpark|national park|nature reserve|nature_reserve",
        regex=True,
        na=False,
    )
    requested_tags = (
        boundary.eq("national_park")
        | leisure.eq("nature_reserve")
        | protect_class.isin({"1", "1a", "1b", "2", "4", "5"})
    )
    selected = osm[geom_area & (requested_text | requested_tags)].copy()
    return raster_mask(selected, 0.0, grid, "osm_nature_protection_areas")


def build_nature_masks(cfg: dict, grid: dict, args) -> dict[str, np.ndarray]:
    """Build the official and the OSM nature-protection masks.

    Parameters
    ----------
    cfg : dict
        Loaded config.
    grid : dict
        Target grid.
    args : argparse.Namespace
        Needs ``osm_pbf`` and ``osm_pbf_cache_dir``.

    Returns
    -------
    dict of str to numpy.ndarray
        The masks ``nature_protection_areas`` and
        ``osm_nature_protection_areas``.
    """
    return {
        "nature_protection_areas": _build_official_nature_mask(cfg, grid),
        "osm_nature_protection_areas": _build_osm_nature_mask(cfg, grid, args),
    }


def _read_raster_on_grid(path: Path, grid: dict, resampling: Resampling, dst_nodata=np.nan) -> np.ndarray:
    """Read band 1 of a raster, reprojected onto the target grid.

    Parameters
    ----------
    path : Path
        Source raster.
    grid : dict
        Target grid.
    resampling : rasterio.enums.Resampling
        Resampling method.
    dst_nodata : float, optional
        Fill value, default ``nan``.

    Returns
    -------
    numpy.ndarray
        ``float32`` array of the grid's shape; all ``dst_nodata`` if ``path``
        does not exist.
    """
    arr = np.full(grid["shape"], dst_nodata, dtype=np.float32)
    if not path.exists():
        return arr
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=arr,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=grid["transform"],
            dst_crs=grid["crs"],
            resampling=resampling,
            src_nodata=src.nodata,
            dst_nodata=dst_nodata,
        )
    return arr


def build_geography_masks(cfg: dict, grid: dict) -> dict[str, np.ndarray]:
    """Geographic/technical exclusions: slope, elevation and weak wind. Unchanged."""
    paths = cfg.get("paths", {})
    exc = cfg.get("exclusion", {})
    derived = cfg.get("_derived", {})
    dem = _read_raster_on_grid(Path(paths.get("dgm", "")), grid, Resampling.bilinear)
    dem[dem == 0.0] = np.nan

    cell_x = abs(float(grid["transform"].a))
    cell_y = abs(float(grid["transform"].e))
    dem_filled = np.where(np.isfinite(dem), dem, np.nanmedian(dem[np.isfinite(dem)]) if np.isfinite(dem).any() else 0.0)

    block = 4
    height, width = dem_filled.shape
    pad_h = (-height) % block
    pad_w = (-width) % block
    if pad_h or pad_w:
        dem_work = np.pad(dem_filled, ((0, pad_h), (0, pad_w)), mode="edge")
    else:
        dem_work = dem_filled
    h4, w4 = dem_work.shape
    dem_100 = dem_work.reshape(h4 // block, block, w4 // block, block).mean(axis=(1, 3))
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32) / (8.0 * cell_x * block)
    ky = np.array([[1, 2, 1], [0, 0, 0], [-1, -2, -1]], dtype=np.float32) / (8.0 * cell_y * block)
    dzdx = ndimage.convolve(dem_100, kx, mode="nearest")
    dzdy = ndimage.convolve(dem_100, ky, mode="nearest")
    slope_100_pct = np.hypot(dzdx, dzdy) * 100.0
    slope_pct = np.repeat(np.repeat(slope_100_pct, block, axis=0), block, axis=1)[:height, :width]
    slope_threshold_pct = float(derived.get("slope_threshold_pct", np.tan(np.radians(float(exc.get("slope_max_deg", 20.0)))) * 100.0))
    elev_max = float(exc.get("elevation_max", 2500.0))

    pd150 = _read_raster_on_grid(Path(paths.get("wind_pd_150", "")), grid, Resampling.bilinear)
    wind_min = float(derived.get("power_density_min_150", cfg.get("wind", {}).get("pd_min", 180.0)))

    return {
        "geography_slope_too_steep": (slope_pct > slope_threshold_pct) & np.isfinite(dem),
        "geography_elevation_too_high": (dem > elev_max) & np.isfinite(dem),
        "geography_wind_too_low": (~np.isfinite(pd150)) | (pd150 < wind_min),
    }


def water_bodies_mask(water: gpd.GeoDataFrame, grid: dict, min_area_ha: float = WATER_MIN_AREA_HA) -> np.ndarray:
    """Fußabdruck-Maske größerer Wasserkörper (Seen, Stauseen, Flussläufe).

    Reiner Rechenkern von build_water_masks(), getrennt für Testbarkeit. Nur
    Polygone - waterway=river/stream-Mittellinien kommen als LineStrings aus
    dem Export und würden sonst jeden Bach als 25-m-Zellenband stempeln. Die
    Mindestfläche wirkt über min_area_filter auf VERBUNDENE Rasterkomponenten,
    nicht pro Feature, weil OSM Flussufer in viele kleine Teilpolygone stückelt.

    Never mutates its input - operates on a filtered view only.
    """
    if water.empty:
        return np.zeros(grid["shape"], dtype=bool)
    is_polygon = water.geometry.notnull() & ~water.geometry.is_empty & water.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    polygons = water.loc[is_polygon, ["geometry"]]
    if polygons.empty:
        return np.zeros(grid["shape"], dtype=bool)
    footprint = raster_mask(polygons, 0.0, grid, "water_bodies")
    return min_area_filter(footprint, grid, min_area_ha)


def build_water_masks(cfg: dict, grid: dict, args) -> dict[str, np.ndarray]:
    """Größere offene Wasserflächen als Geography-Ausschluss. `args` needs: osm_pbf, osm_pbf_cache_dir."""
    water = read_layer(osm_layer_path(cfg, args.osm_pbf, args.osm_pbf_cache_dir, "water", grid["bounds"]), bounds=grid["bounds"])
    mask = water_bodies_mask(water, grid, WATER_MIN_AREA_HA)
    print(
        f"[info]  water bodies: features={len(water):,}, "
        f"cells(>= {WATER_MIN_AREA_HA:g} ha verbunden)={int(mask.sum()):,}",
        flush=True,
    )
    return {"geography_water_bodies": mask}


def build_official_zoning_masks(cfg: dict, grid: dict, args) -> dict[str, np.ndarray]:
    """One merged reference overlay of all official wind zones.

    NÖ zoning geojson unioned with the Bundesland zones registered in
    ``calc.wind_zones`` (Stmk/Sbg Vorrang, Bgld Eignung, Ktn RED III).

    `args` needs: official_zoning_geojson, vorrangzonen_dir, vorrangzonen.
    """
    mask = np.zeros(grid["shape"], dtype=bool)
    # --official-zoning-geojson defaults (see create_widmung_v2_distance_zones.py)
    # to data/zonen/zonierung_noe.json: die amtliche data.gv.at-Rohquelle der NÖ-Windkraft-
    # zonierung (71 Zonen, EPSG:4326). Das ist seit Sep 2026 der reguläre Weg für
    # Band 37 - keine Abhängigkeit mehr zur Legacy-Webmap-Pipeline. Der frühere
    # Default output/webmap_export/windkraft_export_v1/vector/official_zoning.geojson
    # war NUR eine 1:1-Attribut-Ableitung derselben 71 Features (siehe
    # export_visualizer_bundle.py::export_official_zoning), nicht umgekehrt; er
    # kann bei Bedarf weiterhin per CLI-Flag übergeben werden, ein per CLI
    # übergebener Pfad hat wie bisher Vorrang. Fehlt die Datei, wird wie bisher
    # nur gewarnt statt hart abzustürzen.
    path = Path(args.official_zoning_geojson)
    if path.exists():
        zones = read_layer(path, bounds=grid["bounds"])
        mask = mask | raster_mask(zones, 0.0, grid, "official_wind_zoning")
    else:
        print(f"[warn]  official zoning geojson missing: {path}", flush=True)
    bl = admin_boundaries(cfg, grid["bounds"])
    bl_dissolved = bl.dissolve(by="BL").reset_index()[["BL", "geometry"]] if not bl.empty and "BL" in bl.columns else None
    zones = load_wind_zones(args.vorrangzonen_dir, args.vorrangzonen, bl_dissolved)
    if zones is not None:
        mask = mask | raster_mask(zones, 0.0, grid, "official_wind_zones_by_bl")
    return {"official_wind_zoning": mask}


def min_area_filter(mask: np.ndarray, grid: dict, min_area_ha: float) -> np.ndarray:
    """Keep connected True regions with area >= min_area_ha.

    4er-Nachbarschaft (nur Kantenkontakt verbindet): eine Eckberührung ist real
    eine 0-m-Verbindung - zwei nur diagonal verbundene Teilflächen sind keine
    durchgängige Fläche. Entscheidung Aug 2026; vorher 8er-Nachbarschaft
    (np.ones((3,3))). Gespiegelt in create_osm_wka_distance_zones.py.
    """
    if min_area_ha <= 0 or not mask.any():
        return mask.astype(bool)
    cell_area_m2 = abs(float(grid["transform"].a) * float(grid["transform"].e))
    min_cells = int(np.ceil(float(min_area_ha) * 10_000.0 / cell_area_m2))
    labels, n_labels = ndimage.label(mask, structure=ndimage.generate_binary_structure(2, 1))
    if n_labels == 0:
        return np.zeros(mask.shape, dtype=bool)
    counts = np.bincount(labels.ravel())
    keep = counts >= min_cells
    keep[0] = False
    return keep[labels]


# Gauß-Sigmas (Meter) für die verschmierten Eignungsflächen-Bänder: sie zeigen
# die räumliche Unsicherheit der Zonengrenzen. Der Zellwert ist der
# Gauß-gewichtete Eignungsanteil der Umgebung in Prozent (0-100): 100 tief in
# einer großen Zone, ~50 an der Kante, schmale Splitter verwaschen.
UNCERTAINTY_BLUR_SIGMAS_M = (100.0, 200.0, 250.0, 300.0)


def uncertainty_blur(mask: np.ndarray, grid: dict, sigma_m: float) -> np.ndarray:
    """Gauß-verschmierte Eignungsmaske als Unsicherheitsband (uint8, 0-100).

    Sigma in Metern, umgerechnet auf Zellen. Never mutates its input.
    """
    cell_m = max(abs(float(grid["transform"].a)), abs(float(grid["transform"].e)))
    blurred = ndimage.gaussian_filter(mask.astype(np.float32), sigma=float(sigma_m) / cell_m)
    return np.clip(np.rint(blurred * 100.0), 0.0, 100.0).astype(np.uint8)


# ---------------------------------------------------------------------------
# Checkpoint system (generalized: no argparse coupling, unlike the original
# script's layer_done()/write_layer(), which hardwired settlement-clustering
# tag names — see Risk 11 in the design plan)
# ---------------------------------------------------------------------------

def layer_profile(grid: dict) -> dict:
    """Rasterio profile of a checkpoint layer.

    Parameters
    ----------
    grid : dict
        Target grid.

    Returns
    -------
    dict
        Single-band ``uint8`` GeoTIFF profile, deflate-compressed, tiled,
        nodata 0.
    """
    return {
        "driver": "GTiff",
        "height": grid["shape"][0],
        "width": grid["shape"][1],
        "count": 1,
        "dtype": "uint8",
        "crs": grid["crs"],
        "transform": grid["transform"],
        "compress": "deflate",
        "tiled": True,
        "nodata": 0,
    }


def layer_path(layer_dir: Path, name: str) -> Path:
    """Path of a checkpoint layer.

    Parameters
    ----------
    layer_dir : Path
        Layer directory.
    name : str
        Layer name.

    Returns
    -------
    Path
        ``layer_dir / "<name>.tif"``.
    """
    return layer_dir / f"{name}.tif"


def layer_done(path: Path, name: str, grid: dict, extra_ok: Callable[[dict], bool] | None = None) -> bool:
    """Check whether a checkpoint layer exists and matches the grid.

    Parameters
    ----------
    path : Path
        Checkpoint file.
    name : str
        Expected band description.
    grid : dict
        Expected grid (shape, CRS, transform).
    extra_ok : callable, optional
        Additional check on the file's tags.

    Returns
    -------
    bool
        ``True`` if the file has one band with matching shape, CRS, transform
        and description, and ``extra_ok`` (if given) accepts its tags. Any
        exception while reading counts as ``False``.
    """
    if not path.exists():
        return False
    try:
        with rasterio.open(path) as src:
            basic_ok = (
                src.count == 1
                and src.shape == grid["shape"]
                and src.crs == grid["crs"]
                and src.transform == grid["transform"]
                and src.descriptions[0] == name
            )
            if not basic_ok:
                return False
            if extra_ok is not None:
                return extra_ok(src.tags())
            return True
    except Exception:
        return False


def write_layer(path: Path, name: str, arr: np.ndarray, grid: dict, extra_tags: dict | None = None) -> None:
    """Write a checkpoint layer.

    Parameters
    ----------
    path : Path
        Target file; parent directories are created.
    name : str
        Band description and ``LAYER_NAME`` tag.
    arr : numpy.ndarray
        Mask, written as ``uint8``.
    grid : dict
        Target grid.
    extra_tags : dict, optional
        Tags added to ``LAYER_NAME`` and ``DISTANCE_ENGINE``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **layer_profile(grid)) as dst:
        dst.write(arr.astype("uint8"), 1)
        dst.set_band_description(1, name)
        tags = {"LAYER_NAME": name, "DISTANCE_ENGINE": "fft"}
        if extra_tags:
            tags.update(extra_tags)
        dst.update_tags(**tags)


def read_layer_mask(path: Path) -> np.ndarray:
    """Read band 1 of a layer as a boolean mask.

    Parameters
    ----------
    path : Path
        Layer file.

    Returns
    -------
    numpy.ndarray
        Boolean mask.
    """
    with rasterio.open(path) as src:
        return src.read(1).astype(bool)


def ensure_group_layers(
    layer_dir: Path,
    names: list[str],
    build_label: str,
    build_fn: Callable[[], dict[str, np.ndarray]],
    grid: dict,
    force: bool,
    extra_ok: Callable[[dict], bool] | None = None,
    extra_tags: dict | None = None,
) -> None:
    """Build and write a group of checkpoint layers unless all are done.

    A layer counts as done if ``layer_done()`` accepts it and ``force`` is not
    set. If any layer of the group is missing, ``build_fn`` runs once and
    only the missing layers are written; the others are kept.

    Parameters
    ----------
    layer_dir : Path
        Layer directory.
    names : list of str
        Layer names of the group.
    build_label : str
        Label for progress messages.
    build_fn : callable
        Returns a dict of masks by layer name; a missing name is written as an
        empty mask.
    grid : dict
        Target grid.
    force : bool
        Rebuild all layers of the group.
    extra_ok : callable, optional
        Additional tag check passed to ``layer_done()``.
    extra_tags : dict, optional
        Tags passed to ``write_layer()``.
    """
    missing = [name for name in names if force or not layer_done(layer_path(layer_dir, name), name, grid, extra_ok)]
    if not missing:
        print(f"[skip]  {build_label}: all {len(names)} layers already done", flush=True)
        return
    print(f"[need]  {build_label}: {', '.join(missing)}", flush=True)
    with timed(f"build {build_label}"):
        group = build_fn()
    for name in names:
        p = layer_path(layer_dir, name)
        if name in missing:
            with timed(f"write checkpoint layer {name}"):
                write_layer(p, name, group.get(name, np.zeros(grid["shape"], dtype=bool)), grid, extra_tags)
        else:
            print(f"[keep]  checkpoint layer {name}", flush=True)
    del group
    gc.collect()


def output_profile(path: Path, grid: dict, band_count: int) -> dict:
    """Rasterio profile of the multi-band output GeoTIFF.

    Creates the parent directory of ``path``.

    Parameters
    ----------
    path : Path
        Output file.
    grid : dict
        Target grid.
    band_count : int
        Number of bands.

    Returns
    -------
    dict
        ``uint8`` GeoTIFF profile, deflate-compressed, tiled, band-interleaved,
        nodata 0.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return {
        "driver": "GTiff",
        "height": grid["shape"][0],
        "width": grid["shape"][1],
        "count": band_count,
        "dtype": "uint8",
        "crs": grid["crs"],
        "transform": grid["transform"],
        "compress": "deflate",
        "tiled": True,
        # BAND statt GTiff-Default PIXEL: compose_exclusion_geotiff schreibt
        # bandweise - bei PIXEL muss GDAL dafür jeden Tile (alle Bänder) pro
        # Band-Write neu komprimieren, O(Bänder²); Einzelband-Leser (Viewer,
        # validate) zahlen sonst die volle Bandzahl als Lese-Verstärkung.
        "interleave": "band",
        "nodata": 0,
    }


# ---------------------------------------------------------------------------
# Final multiband composition (shared by the Widmung v1 and v2 pipelines)
# ---------------------------------------------------------------------------

def composed_band_names(
    condition_bands: list[str],
    min_fragment_area_ha: float,
    trailing_bands: list[str],
    variant_source_band: str | None,
    variants: dict,
    blur_sigmas_m: tuple = (),
) -> list[str]:
    """Band order of compose_exclusion_geotiff(), without touching any raster."""
    names = list(condition_bands) + [
        "exclusion_human", "exclusion_nature", "exclusion_geography",
        "all_exclusions", "available_after_all_exclusions_raw",
        f"available_cleaned_min_{min_fragment_area_ha:g}ha",
    ]
    names.extend(f"available_blur_sigma_{s:g}m" for s in blur_sigmas_m)
    names.extend(trailing_bands)
    if variant_source_band:
        for vname in variants:
            names.extend(variant_band_names(variant_source_band, vname, min_fragment_area_ha))
    return names


def compose_exclusion_geotiff(
    path: Path,
    grid: dict,
    layer_dir: Path,
    condition_bands: list[str],
    human_bands: list[str],
    nature_band_names: list[str],
    geography_band_names: list[str],
    valid_area: np.ndarray,
    min_fragment_area_ha: float,
    trailing_bands: list[str],
    variant_source_band: str | None,
    variants: dict,
    tags: dict,
    build_overviews: bool = True,
    blur_sigmas_m: tuple = (),
    blur_source: str = "cleaned",
) -> list[str]:
    """Read checkpoint layers band by band and write the final multiband GeoTIFF.

    blur_source: "cleaned" (Default, v1-Verhalten) rechnet die Unsicherheits-
    Blur-Bänder aus der min-area-bereinigten Fläche, "raw" aus
    available_after_all_exclusions_raw - dann zeigen die Sigmas auch die
    Splitterflächen unter der 10-ha-Schwelle.

    Layout: condition bands, the three category aggregates, all/raw/cleaned,
    optional Gauß-verschmierte Unsicherheitsbänder (uint8 0-100, je Sigma aus
    blur_sigmas_m), the trailing reference bands, then four bands per
    settlement-buffer variant (buffer, human total, raw and cleaned available
    area).

    Bands are streamed one at a time and released immediately - at 25 m over all
    of Austria a single band is ~300 MB, so holding them all would not fit.
    ``human_rest`` accumulates the human exclusions WITHOUT the variant source
    band, which is what makes the per-variant recompose cheap.
    """
    human = np.zeros(grid["shape"], dtype=bool)
    human_rest = np.zeros(grid["shape"], dtype=bool)
    nature = np.zeros(grid["shape"], dtype=bool)
    geography = np.zeros(grid["shape"], dtype=bool)

    band_names = composed_band_names(
        condition_bands, min_fragment_area_ha, trailing_bands, variant_source_band, variants, blur_sigmas_m
    )

    with rasterio.open(path, "w", **output_profile(path, grid, len(band_names))) as dst:
        band_idx = 1
        for name in condition_bands:
            with timed(f"read/write condition layer {name}"):
                arr = read_layer_mask(layer_path(layer_dir, name))
                dst.write(arr.astype("uint8"), band_idx)
                dst.set_band_description(band_idx, name)
                if name in human_bands:
                    human |= arr
                    if name != variant_source_band:
                        human_rest |= arr
                elif name in nature_band_names:
                    nature |= arr
                elif name in geography_band_names:
                    geography |= arr
                del arr
                band_idx += 1

        with timed("write aggregate/final bands"):
            human &= valid_area
            nature &= valid_area
            geography &= valid_area
            for name, arr in [
                ("exclusion_human", human),
                ("exclusion_nature", nature),
                ("exclusion_geography", geography),
            ]:
                dst.write(arr.astype("uint8"), band_idx)
                dst.set_band_description(band_idx, name)
                band_idx += 1

            all_exclusions = (human | nature | geography) & valid_area
            available_raw = (~all_exclusions) & valid_area
            with timed("min area cleanup"):
                available_cleaned = min_area_filter(available_raw, grid, min_fragment_area_ha)
            for name, arr in [
                ("all_exclusions", all_exclusions),
                ("available_after_all_exclusions_raw", available_raw),
                (f"available_cleaned_min_{min_fragment_area_ha:g}ha", available_cleaned),
            ]:
                dst.write(arr.astype("uint8"), band_idx)
                dst.set_band_description(band_idx, name)
                band_idx += 1

            blur_basis = available_raw if blur_source == "raw" else available_cleaned
            blur_basis_name = "available_after_all_exclusions_raw" if blur_source == "raw" else f"available_cleaned_min_{min_fragment_area_ha:g}ha"
            for sigma_m in blur_sigmas_m:
                with timed(f"write uncertainty blur sigma={sigma_m:g}m ({blur_source})"):
                    blurred = uncertainty_blur(blur_basis, grid, sigma_m)
                    dst.write(blurred, band_idx)
                    dst.set_band_description(band_idx, f"available_blur_sigma_{sigma_m:g}m")
                    dst.update_tags(band_idx, GAUSS_SIGMA_M=f"{sigma_m:g}", UNIT="percent_0_100", SOURCE=blur_basis_name)
                    del blurred
                    band_idx += 1

            for name in trailing_bands:
                with timed(f"write trailing band {name}"):
                    arr = read_layer_mask(layer_path(layer_dir, name))
                    dst.write(arr.astype("uint8"), band_idx)
                    dst.set_band_description(band_idx, name)
                    del arr
                    band_idx += 1

            if variant_source_band and variants:
                with timed(f"write {len(variants)} settlement-buffer variants (4 bands each)"):
                    fixed_exclusions = (human_rest | nature | geography) & valid_area
                    for vname, spec in variants.items():
                        vbuf = read_layer_mask(layer_path(layer_dir, f"{variant_source_band}_{vname}")) & valid_area
                        human_v = (human_rest | vbuf) & valid_area
                        avail_v = (~(fixed_exclusions | vbuf)) & valid_area
                        cleaned_v = min_area_filter(avail_v, grid, min_fragment_area_ha)
                        buffers_json = json.dumps(resolve_variant_buffers(spec), ensure_ascii=False, sort_keys=True)
                        names = variant_band_names(variant_source_band, vname, min_fragment_area_ha)
                        for bname, arr in zip(names, [vbuf, human_v, avail_v, cleaned_v]):
                            dst.write(arr.astype("uint8"), band_idx)
                            dst.set_band_description(band_idx, bname)
                            dst.update_tags(band_idx, VARIANT=vname, BUFFER_BY_BL=buffers_json)
                            band_idx += 1
                        del vbuf, human_v, avail_v, cleaned_v

            dst.update_tags(**tags)
            if build_overviews:
                with timed("build overviews"):
                    dst.build_overviews([2, 4, 8, 16], rasterio.enums.Resampling.nearest)
                    dst.update_tags(ns="rio_overview", resampling="nearest")

    return band_names
