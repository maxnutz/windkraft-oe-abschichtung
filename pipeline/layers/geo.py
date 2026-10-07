"""Layer: Natur, Gelände, Zonen und Puffer (Paket W2.4, docs/rewrite/PLAN.md
§7, §13.8; das Paket selbst ist dort neu - siehe dessen Begründung: "W2.4
ist neu und übernimmt die 17 herrenlosen Checkpoints").

``scripts/widmung_v2/04_create_distance_zones.py`` (seit W6.1 aus dem Repo
entfernt; letzter Stand im Commit ``f1d00f7``, abrufbar mit
``git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py``) schrieb
17 der 33 Checkpoint-Layer über sieben ``ensure_group_layers()``-Aufrufe (Konstanten
dort: ``HIG_FAMILY_SOURCE_BANDS``, ``BUFFER_BANDS``, ``NATURE_BANDS``,
``GEOGRAPHY_BANDS``, ``WATER_BANDS``, ``OFFICIAL_ZONING_BANDS``,
``WKA_BESTAND_BAND`` - siehe ``pipeline/contract.py:LAYER_NAMES``-Kommentar,
der genau diese sieben Namen als Quelle nennt). Der Auftragstext für dieses
Paket zählt ``HIG_FAMILY_SOURCE_BANDS`` nicht einzeln auf, aber 4+5+2+3+1+1+1
= 17 geht nur mit allen sieben - ohne die vier HiG-Familienbänder wären es
13, nicht 17. Diese Datei übernimmt deshalb alle sieben Gruppen.

## Wo die Grenze zu W3.1 verläuft

Diese Datei endet, sobald die letzte der 17 Rastermasken als Checkpoint
geschrieben ist. Was ``04_create_distance_zones.py`` (seit W6.1 aus dem Repo
entfernt; letzter Stand im Commit ``f1d00f7``, abrufbar mit
``git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py``) DANACH tut -
``compose_exclusion_geotiff()`` (Bänder zu einem GeoTIFF zusammensetzen,
Kategorie-Aggregate bilden, Blur-Bänder rechnen, `available`/`cleaned`
ableiten) und ``write_band_manifest()`` - ist laut Auftrag ausdrücklich
NICHT Teil dieses Pakets, sondern W3.1 ("Endkomposition"). Diese Datei
importiert deshalb weder Funktion. Ebenso nicht übernommen: die optionalen
Siedlungsabstands-Varianten (``SETTLEMENT_BUFFER_VARIANTS``,
``build_settlement_variant_buffers``, ``--settlement-variants``) - deren
Bänder (``settlement_buffer_<variante>`` u.a.) stehen NICHT in
``pipeline/contract.py:LAYER_NAMES`` (33 Einträge) und sind im Clean-Schema
per Default aus; sie gehören weder zu den 17 Pflicht-Checkpoints noch zur
Endkomposition dieses Pakets.

## Eingaben: derived/prep/ statt data/

Vier der sieben Gruppen lasen bisher Rohdaten direkt: Verwaltungsgrenzen
(VGD-Shapefile), amtlicher Naturschutz (NSG-ZIP), amtliche Windzonen (fünf
Quellen) und drei OSM-Objektgruppen (nature/water/windpower, per Osmium
gegen die PBF-Rohquelle). Diese Datei liest sie jetzt aus der Prep-Stufe:

    admin_boundaries()        -> derived/prep/admin/bundesland_masken.gpkg
                                  (pipeline/prep/admin.py, W1.P1)
    _build_official_nature_mask -> derived/prep/natur/schutzgebiete.gpkg
                                  (pipeline/prep/natur.py, W1.P7)
    official_wind_zoning      -> derived/prep/zonen/{Stmk,Sbg,Bgld,RED3,NOE}.gpkg
                                  (pipeline/prep/zonen.py, W1.P8)
    osm_nature_protection_areas,
    geography_water_bodies,
    wka_bestand_ausserhalb_zonen -> derived/prep/osm/b_layers/{nature,water,
                                  windpower}.parquet (pipeline/prep/osm.py, W1.P5)

``geography_*`` (Hangneigung/Höhe/Wind) ist die Ausnahme: W1.P6
(``pipeline/prep/terrain.py``) schreibt laut eigenem Auftrag bewusst KEINE
umgeformte Ableitung der beiden Rohraster (DGM/Leistungsdichte), nur einen
Prüfbericht - "keine Umformung" steht da wörtlich. ``build_geography_masks()``
wird deshalb UNVERÄNDERT aus ``abschichtung_common`` importiert und liest
weiter über ``cfg["paths"]["dgm"]``/``["wind_pd_150"]`` (die laut
``calc/config.py`` ohnehin schon auf ``contract.RAW["gelaende"]``
zeigen) - es gibt keinen Prep-Pfad, auf den umgestellt werden könnte.

## Zwei getrennte Checkpoint-Verzeichnisse

``main()`` unterscheidet zwei Verzeichnisse, NIE dasselbe:

- ``source_dir`` (CLI-Parameter, seit W6.1 ohne Wirkung mehr - siehe unten)
  war RÜCKFALL für die acht externen Quell-Checkpoints, die ihre eigenen
  Baufunktionen (build_hig_family_sources, build_v2_buffers) tatsächlich
  lesen. Diese Stufe schreibt hierhin NIE.
  W5.P1 (PLAN.md §13.6, dieselbe Entscheidung wie ``pipeline/layers/
  osm.py:_cover_layer_path()``): ``_source_layer_path()`` prüfte für jeden
  dieser acht Namen ZUERST ``contract.LAYERS[name]`` unter ``derived/layers/``
  (out_dir von W2.1/W2.3, falls die in DIESER Kette schon gelaufen sind)
  und fiel erst danach - laut meldend, siehe dort - auf ``source_dir``
  zurück (Default: ``output/abschichtung_widmung_v2/distance_layers``, run1
  - im Worktree ein read-only-Symlink auf das Hauptrepo). **W6.1:** ``output/``
  ist aus dem Repo entfernt (siehe ``~/Documents/master_windkraft/archiv/``);
  ``_source_layer_path()`` bricht jetzt laut ab, statt auf ein
  Verzeichnis zurückzufallen, das es nicht mehr gibt - ``source_dir`` bleibt
  nur noch für die Fehlermeldung erhalten.
- ``out_dir`` (Default: ``pipeline.contract.DERIVED_LAYERS``, also
  ``derived/layers/`` - privat in diesem Worktree, nicht symlinkt) - die 17
  eigenen Checkpoints dieser Stufe. Innerhalb derselben Gruppe gelesene,
  bereits von dieser Stufe selbst geschriebene Bänder (z. B. liest
  ``build_v2_buffers()`` die vier HiG-Familienbänder, die
  ``build_hig_family_sources()`` zuvor in DERSELBEN Ausführung geschrieben
  hat) kommen ausschließlich aus ``out_dir`` - niemals aus ``source_dir``.

## Fingerabdruck-Konvention (siehe PLAN.md §12 "Nebenbefund mit Folgen für
Welle 2": ``layer_done()`` prüft Form/CRS/Transform/Bandname, nicht die
Eingabe - derselbe Mechanismus wie bei den Prep-Stufen sei "auch auf der
Layer-Stufe gebraucht")

Entscheidung dieses Pakets: JA, prüfen. Jeder der sieben
``ensure_group_layers()``-Aufrufe bekommt ``extra_tags``/``extra_ok`` mit
einem Tag ``PREP_FINGERPRINT`` - dem SHA-256 über
``pipeline.fingerprint.compute()`` der tatsächlich gelesenen Dateien
(_prep_inputs() unten: die fünf Zonen-GPKGs, das Naturschutz-GPKG, das
Bundesland-GPKG, die drei OSM-Parquets UND die beiden gelaende-Rohraster -
letztere sind kein Prep-Ergebnis, aber eine echte Eingabe von
``build_geography_masks()`` und ohne sie bliebe genau die Lücke bestehen,
die §12 beschreibt). Dieselbe ``pipeline.fingerprint``-Bibliothek wie die
neun Prep-Stufen, nicht neu erfunden - genau der in ihrem Modul-Docstring
angekündigte Wiederverwendungsfall. Ob W2.1/W2.3 dieselbe Konvention
wählen, war beim Schreiben dieser Datei nicht bekannt (parallele Pakete);
falls nicht, ist das beim Zusammenführen anzugleichen (siehe Berichtstext).

## Punkt 18: erbt admin_boundaries() beim Umstieg die "Rohdatei ist schon
EPSG:31287"-Annahme der drei betroffenen Skripte?

Nein. ``pipeline/prep/admin.py`` schreibt ``bundesland_masken.gpkg`` erst
NACH einem eigenen defensiven ``to_crs(TARGET_CRS)`` (dort Zeile ~68-69),
und GeoPackage trägt sein CRS als Metadatenfeld, nicht als separate,
verlierbare ``.prj``-Sidecar-Datei wie ein Shapefile. ``_admin_boundaries()``
unten ruft zusätzlich selbst defensiv ``to_crs(TARGET_CRS)`` auf (genau das
Sicherheitsnetz, das ``read_layer()`` für jeden Konsumenten schon immer
hatte, Zeile ~542 in ``abschichtung_common.py``) - diese Stufe verlässt sich
also an keiner Stelle stillschweigend auf ein angenommenes CRS.

## Punkt 20: Geometrietyp-Empfindlichkeit gegen den GPKG-Promotionseffekt

Siehe Bericht zu diesem Paket für die vollständige Fundstellenliste. Kurz:
alle GPKG-gelesenen Polygon-Eingaben dieses Moduls (``schutzgebiete.gpkg``,
die vier ``WIND_ZONE_SOURCES``-GPKGs plus ``NOE.gpkg``) laufen ausschließlich
über ``raster_mask()`` (-> ``rasterio.features.rasterize()``) in eine
Rastermaske - keine Verzweigung dazwischen unterscheidet Polygon von
MultiPolygon. Die einzige tatsächliche ``geom_type``-Verzweigung in diesem
Block (``build_wka_bestand_hulls()``, "MultiPolygon" vs. sonst) prüft eine
zur Laufzeit aus gepufferten OSM-Punkten vereinigte Geometrie - nie durch
ein GPKG gerundtript, also vom Promotionseffekt unberührt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box
from shapely.ops import unary_union

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import contract, fingerprint, runtime  # noqa: E402
from pipeline.prep import admin as prep_admin  # noqa: E402
from pipeline.prep import natur as prep_natur  # noqa: E402
from pipeline.prep import zonen as prep_zonen  # noqa: E402

from calc.config import load_config  # noqa: E402
from calc.wind_zones import WIND_ZONE_SOURCES  # noqa: E402
from calc.abschichtung_common import (  # noqa: E402
    CABLEWAY_BUILDING_BUFFER_M,
    GENERAL_BUILDING_BUFFER_M,
    GEOGRAPHY_BANDS,
    HIG_FAMILY_BUFFER_M,
    NATURE_BANDS,
    NONRESIDENTIAL_HULL_BUFFER_M,
    OFFICIAL_ZONING_BANDS,
    PEOPLE_CARRYING_AERIALWAY_TYPES,
    SETTLEMENT_BUFFER_BY_BL,
    TARGET_CRS,
    WATER_BANDS,
    WATER_MIN_AREA_HA,
    _non_tunnel_mask,
    building_points,
    build_geography_masks,
    ensure_group_layers,
    expand_bounds,
    layer_path,
    load_grid,
    province_buffer_cell_mask,
    raster_mask,
    read_layer_mask,
    sample_mask_at_points,
    timed,
    uniform_buffer_cell_mask,
    water_bodies_mask,
)

# ---------------------------------------------------------------------------
# Wortgleich aus scripts/widmung_v2/04_create_distance_zones.py übernommen
# (Regel 4: Werte/Namen unverändert) - dort Zeilen ~126-138, 193-206. Seit
# W6.1 aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
# f1d00f7:scripts/widmung_v2/04_create_distance_zones.py.
# ---------------------------------------------------------------------------

BAND_SCHEMA = "clean-44-ohne-wichtige-objekte-aug-2026"

WKA_BESTAND_BAND = "wka_bestand_ausserhalb_zonen"
WKA_CLUSTER_CHAIN_M = 750.0
WKA_HULL_MARGIN_M = 200.0

HIG_FAMILY_SOURCE_BANDS = [
    "haeuser_im_gruenen_ferienhaus",
    "haeuser_im_gruenen_widmung",
    "haeuser_im_gruenen_streusiedlung",
    "haeuser_im_gruenen_noe_pdf",
]

BUFFER_BANDS = [
    "settlement_buffer",
    "haeuser_im_gruenen",
    "nonresidential_hulls_buffer",
    "cableway_buildings_buffer",
    "general_buildings_buffer",
]

# ---------------------------------------------------------------------------
# I/O-Adapter: derived/prep/ statt data/ (siehe Moduldocstring)
# ---------------------------------------------------------------------------

# key -> Bundesland, wortgleich aus calc.wind_zones.WIND_ZONE_SOURCES
# übernommen (dort die Quelle der Wahrheit für Band official_wind_zoning;
# hier nur die vier Schlüssel gebraucht, die pipeline/prep/zonen.py als
# eigene GPKGs ablegt - NOE läuft separat, siehe unten).
_ZONE_BUNDESLAND = {s.key: s.bundesland for s in WIND_ZONE_SOURCES}


def _read_prep_vector(path: Path, bounds=None) -> gpd.GeoDataFrame:
    """Liest eine bereits von der Prep-Stufe aufbereitete Vektordatei (GPKG
    oder GeoParquet, laut deren Docstrings bereits EPSG:31287) und wendet nur
    noch den bounds-Zuschnitt an, den ``read_layer()`` sonst mitübernimmt -
    Prep filtert laut eigenem Docstring bewusst NICHT nach bounds (Sache der
    Konsumenten, siehe pipeline/prep/admin.py bzw. natur.py bzw. zonen.py).
    Der defensive ``to_crs()`` ist dasselbe Sicherheitsnetz wie in
    ``read_layer()`` - siehe Moduldocstring, Punkt 18.
    """
    if not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    gdf = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
    if gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    if gdf.crs is None or str(gdf.crs).upper() != TARGET_CRS:
        gdf = gdf.to_crs(TARGET_CRS)
    if bounds is not None:
        gdf = gdf[gdf.geometry.intersects(box(*bounds))].copy()
    return gdf


def _admin_boundaries(bounds=None) -> gpd.GeoDataFrame:
    """Ersetzt ``abschichtung_common.admin_boundaries()`` für diese Stufe:
    liest ``derived/prep/admin/bundesland_masken.gpkg`` (9 Bundesländer,
    bereits nach ``BL`` dissolviert - pipeline/prep/admin.py) statt der
    VGD-Rohquelle. Einziger Layer-Konsument der Verwaltungsgrenzen, der
    tatsächlich Bundesland-spezifisch PUFFERT (``province_buffer_cell_mask``),
    ist in diesem Modul; ``scripts/widmung_v2/03_build_osm_layers.py`` liest
    ``admin_boundaries()`` zwar ebenfalls (eine ungepufferte NÖ-Flächenmaske
    für ``general_buildings_source``), das ist aber W2.3s eigener Bereich,
    hier unverändert.
    """
    path = contract.PREP["admin"] / prep_admin.BUNDESLAND_MASKEN_FILENAME
    return _read_prep_vector(path, bounds=bounds)


def _clean_zone_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Wortgleich aus pipeline/prep/zonen.py:_clean_geometries übernommen -
    dieselbe Nachbereinigung, die ``calc.wind_zones.load_zones()``
    nach dem Klippen auf die zuständige Bundesland-Grenze anwendet (leere/
    Null-Geometrien raus, ungültige per ``buffer(0)`` reparieren)."""
    out = gdf[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
    if out.empty:
        return out
    invalid = ~out.geometry.is_valid
    if invalid.any():
        out.loc[invalid, "geometry"] = out.geometry[invalid].buffer(0)
    return out[out.geometry.notnull() & ~out.geometry.is_empty]


def _clip_to_bundesland(gdf: gpd.GeoDataFrame, bundesland: str, bl: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Portierung von ``calc.wind_zones._clip_to_bundesland()``
    (dort ~Zeile 178-190) auf die bereits von pipeline/prep/zonen.py
    aufbereiteten Zonen-Quellen. Grund unverändert: eine Landesverordnung
    hat außerhalb ihres Bundeslands keine Wirkung (siehe dortiger Docstring,
    z. B. der oö. Windkraft-Masterplan reicht ~960 km² über die Grenze)."""
    match = bl[bl["BL"] == bundesland]
    if match.empty:
        print(f"[warn]  keine Grenze für '{bundesland}', Zonenquelle bleibt ungeklippt", flush=True)
        return gdf
    return gpd.clip(gdf, match.geometry.union_all())


def _prep_inputs() -> list[Path]:
    """Alle tatsächlich gelesenen Eingaben dieser Stufe - Prep-Ausgaben UND
    die beiden gelaende-Rohraster (siehe Moduldocstring, Fingerabdruck-
    Konvention)."""
    osm_b = contract.PREP["osm"]["b_layers"]
    zonen_dir = contract.PREP["zonen"]
    return sorted(
        [
            contract.PREP["admin"] / prep_admin.BUNDESLAND_MASKEN_FILENAME,
            contract.PREP["natur"] / prep_natur.OUTPUT_FILENAME,
            *(zonen_dir / f"{key}.gpkg" for key in prep_zonen.SOURCES),
            osm_b / "nature.parquet",
            osm_b / "water.parquet",
            osm_b / "windpower.parquet",
            contract.RAW["gelaende"]["dgm"],
            contract.RAW["gelaende"]["wind_pd_150"],
        ]
    )


def _fingerprint_tag() -> str:
    """Fingerabdruck der Eingaben dieser Stufe als Tag-Wert.

    SHA-256 über ``pipeline.fingerprint.compute()`` der Eingaben aus
    ``_prep_inputs()``; wird als ``PREP_FINGERPRINT``-Tag in die Checkpoints
    dieser Stufe geschrieben.

    Returns
    -------
    str
        Hexdigest.
    """
    data = fingerprint.compute(_prep_inputs())
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Band-Gruppen - Portierung des "Layer-Teils" von
# scripts/widmung_v2/04_create_distance_zones.py (Regel 4: Puffer/Schwellen/
# Heuristiken unverändert, nur die Eingabe-Beschaffung ist neu verdrahtet).
# Seit W6.1 aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
# f1d00f7:scripts/widmung_v2/04_create_distance_zones.py.
# ---------------------------------------------------------------------------


def _source_layer_path(name: str, source_dir: Path) -> Path:
    """Pfad zu einem der acht externen Quell-Checkpoints aus W2.1/W2.3
    (``official_settlement_source`` usw. - siehe ``_check_required_sources()``).

    Ausschließlich der Zielort dieser Pipeline (``contract.LAYERS[name]``
    unter ``derived/layers/``, von W2.1/W2.3 geschrieben) - genau das Muster
    aus ``pipeline/layers/osm.py:_cover_layer_path()`` (W5.P1: dieselbe
    Entscheidung, zweimal getroffen, siehe PLAN.md §13.6).

    **W6.1:** der frühere Rückfall auf den geteilten, NUR LESEND
    zugänglichen ``source_dir`` (Default: ``output/abschichtung_widmung_v2/
    distance_layers``, run1) ist entfernt - dieses Verzeichnis existiert seit
    W6.1 nicht mehr im Repo (``output/`` wurde nach
    ``~/Documents/master_windkraft/archiv/`` herausbewegt). Fehlt der Layer
    unter ``derived/layers/``, bricht dieser Aufruf jetzt sofort mit benannter
    Meldung ab, statt still auf ein Verzeichnis zurückzufallen, das es nicht
    mehr gibt. ``source_dir`` bleibt Parameter (für die Fehlermeldung und
    CLI-Kompatibilität von ``--source-dir``), wird aber nicht mehr gelesen."""
    build_path = contract.LAYERS[name]
    if not build_path.exists():
        raise FileNotFoundError(
            f"'{name}' fehlt unter {build_path} (derived/layers/, W2.1/W2.3 noch "
            "nicht gelaufen) - der Rueckfall auf die alte Kette "
            f"({source_dir}) ist seit W6.1 entfernt, dieses Verzeichnis "
            "existiert nicht mehr im Repo. Erst 'make layer-hig layer-osm' "
            "laufen lassen (pipeline/layers/hig.py, osm.py)."
        )
    return build_path


def build_hig_family_sources(grid: dict, source_dir: Path) -> dict[str, np.ndarray]:
    """Wie 04_create_distance_zones.py:build_hig_family_sources() (seit W6.1
    aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py), Eingabe
    ``admin_boundaries()`` jetzt über ``_admin_boundaries()`` (Prep statt
    VGD-Rohquelle); die vier Quell-Checkpoints (ferienhaus_tourismus_source
    usw.) kommen aus ``derived/layers/`` (W2.1), mit Rückfall auf das externe,
    geteilte Checkpoint-Verzeichnis (source_dir) - siehe
    ``_source_layer_path()``."""

    def source(name: str) -> np.ndarray:
        return read_layer_mask(_source_layer_path(name, source_dir))

    bl = _admin_boundaries(grid["bounds"])
    noe = bl[bl["BL"].eq("Niederösterreich")] if "BL" in bl.columns else bl.iloc[0:0]
    noe_mask = raster_mask(noe[["geometry"]].copy(), 0.0, grid, "NÖ Landesfläche") if not noe.empty else np.zeros(grid["shape"], dtype=bool)

    return {
        "haeuser_im_gruenen_ferienhaus": source("ferienhaus_tourismus_source"),
        "haeuser_im_gruenen_widmung": source("official_hig_source") & ~noe_mask,
        "haeuser_im_gruenen_streusiedlung": source("hig_hulls_source") & ~noe_mask,
        "haeuser_im_gruenen_noe_pdf": source("noe_pdf_750m_zones"),
    }


def build_v2_buffers(grid: dict, source_dir: Path, out_dir: Path) -> dict[str, np.ndarray]:
    """Wie 04_create_distance_zones.py:build_v2_buffers() (seit W6.1 aus dem
    Repo entfernt, letzter Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py). Liest die vier
    HiG-Familienbänder aus ``out_dir`` (von build_hig_family_sources() in
    DERSELBEN Ausführung geschrieben), die übrigen Quell-Checkpoints
    (official_settlement_source, nonresidential_hulls_source,
    cableway_buildings_source, general_buildings_source) aus ``derived/layers/``
    (W2.1/W2.3), mit Rückfall auf den externen ``source_dir`` - siehe
    ``_source_layer_path()``."""

    def own(name: str) -> np.ndarray:
        return read_layer_mask(layer_path(out_dir, name))

    def ext(name: str) -> np.ndarray:
        return read_layer_mask(_source_layer_path(name, source_dir))

    settlement = ext("official_settlement_source")
    hig_family = own("haeuser_im_gruenen_ferienhaus") | own("haeuser_im_gruenen_widmung") | own("haeuser_im_gruenen_streusiedlung")
    noe_pdf = own("haeuser_im_gruenen_noe_pdf")

    bl = _admin_boundaries(expand_bounds(grid["bounds"], max(SETTLEMENT_BUFFER_BY_BL.values()) + 25.0))

    # Die NÖ-PDF-Zonen sind bereits Objekt + 750 m und gehen deshalb UNGEPUFFERT
    # ins Aggregat - ein zweiter 750-m-Puffer würde daraus 1.500-m-Zonen machen.
    hig_aggregate = uniform_buffer_cell_mask(hig_family, HIG_FAMILY_BUFFER_M, grid, "haeuser_im_gruenen_750m") | noe_pdf

    return {
        "settlement_buffer": province_buffer_cell_mask(settlement, bl, SETTLEMENT_BUFFER_BY_BL, grid),
        "haeuser_im_gruenen": hig_aggregate,
        "nonresidential_hulls_buffer": uniform_buffer_cell_mask(ext("nonresidential_hulls_source"), NONRESIDENTIAL_HULL_BUFFER_M, grid, "nonresidential_hulls_25m"),
        "cableway_buildings_buffer": uniform_buffer_cell_mask(ext("cableway_buildings_source"), CABLEWAY_BUILDING_BUFFER_M, grid, "cableway_buildings_50m"),
        "general_buildings_buffer": uniform_buffer_cell_mask(ext("general_buildings_source"), GENERAL_BUILDING_BUFFER_M, grid, "general_buildings_25m"),
    }


def _build_official_nature_mask(grid: dict) -> np.ndarray:
    """Wie 04_create_distance_zones.py:_build_official_nature_mask() (seit
    W6.1 aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py), Eingabe
    jetzt ``derived/prep/natur/schutzgebiete.gpkg`` (920 Flächen, geometrieonly,
    schon EPSG:31287) statt des NSG-ZIPs. Der notnull/not-empty-Filter ist
    hier ein No-op (Prep garantiert das schon beim Schreiben), bleibt aber
    stehen - dieselbe Prüfung wie im Original, nicht mehr und nicht weniger."""
    path = contract.PREP["natur"] / prep_natur.OUTPUT_FILENAME
    nature = _read_prep_vector(path, bounds=grid["bounds"])
    if nature.empty:
        return np.zeros(grid["shape"], dtype=bool)
    nature = nature[nature.geometry.notnull() & ~nature.geometry.is_empty]
    return raster_mask(nature, 0.0, grid, "nature_protection_areas")


def _build_osm_nature_mask(grid: dict) -> np.ndarray:
    """Wie 04_create_distance_zones.py:_build_osm_nature_mask() (seit W6.1
    aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py), Eingabe
    jetzt ``derived/prep/osm/b_layers/nature.parquet`` statt eines frischen
    Osmium-Exports gegen die PBF-Rohquelle. Tag-/Text-Heuristik wortgleich."""
    path = contract.PREP["osm"]["b_layers"] / "nature.parquet"
    osm = _read_prep_vector(path, bounds=grid["bounds"])
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


def build_nature_masks(grid: dict) -> dict[str, np.ndarray]:
    """Baut die beiden Natur-Masken.

    Parameters
    ----------
    grid : dict
        Zielraster.

    Returns
    -------
    dict of str to numpy.ndarray
        Die Masken ``nature_protection_areas`` (aus
        ``_build_official_nature_mask()``) und ``osm_nature_protection_areas``
        (aus ``_build_osm_nature_mask()``).
    """
    return {
        "nature_protection_areas": _build_official_nature_mask(grid),
        "osm_nature_protection_areas": _build_osm_nature_mask(grid),
    }


def build_water_masks(grid: dict) -> dict[str, np.ndarray]:
    """Wie 04_create_distance_zones.py:build_water_masks() (seit W6.1 aus
    dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py), Eingabe jetzt
    ``derived/prep/osm/b_layers/water.parquet``. ``water_bodies_mask()`` selbst
    (Fußabdruck + Mindestfläche über verbundene Rasterflächen) unverändert
    aus abschichtung_common importiert - reine Rechenlogik, kein I/O."""
    path = contract.PREP["osm"]["b_layers"] / "water.parquet"
    water = _read_prep_vector(path, bounds=grid["bounds"])
    mask = water_bodies_mask(water, grid, WATER_MIN_AREA_HA)
    print(
        f"[info]  water bodies: features={len(water):,}, "
        f"cells(>= {WATER_MIN_AREA_HA:g} ha verbunden)={int(mask.sum()):,}",
        flush=True,
    )
    return {"geography_water_bodies": mask}


def build_official_zoning_masks(grid: dict) -> dict[str, np.ndarray]:
    """Wie 04_create_distance_zones.py:build_official_zoning_masks() (seit
    W6.1 aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py). Die
    fünfte Quelle (NÖ) kommt jetzt aus ``derived/prep/zonen/NOE.gpkg`` statt
    ``--official-zoning-geojson``; die vier ``WIND_ZONE_SOURCES``-Quellen aus
    ``derived/prep/zonen/{Stmk,Sbg,Bgld,RED3}.gpkg`` statt
    ``load_wind_zones()`` (das wiederum roh Shapefiles/ZIPs liest). Das
    Klippen auf die zuständige Bundesland-Grenze (per ``_clip_to_bundesland``)
    bleibt Sache dieser Stufe - pipeline/prep/zonen.py klippt laut eigenem
    Docstring bewusst nicht."""
    mask = np.zeros(grid["shape"], dtype=bool)

    noe_path = contract.PREP["zonen"] / "NOE.gpkg"
    noe_zones = _read_prep_vector(noe_path, bounds=grid["bounds"])
    if not noe_zones.empty:
        mask = mask | raster_mask(noe_zones, 0.0, grid, "official_wind_zoning")
    else:
        print(f"[warn]  official zoning (NÖ) prep-Ausgabe leer: {noe_path}", flush=True)

    bl = _admin_boundaries(grid["bounds"])
    zone_frames = []
    for key, bundesland in _ZONE_BUNDESLAND.items():
        gdf = _read_prep_vector(contract.PREP["zonen"] / f"{key}.gpkg")
        if gdf.empty:
            continue
        if not bl.empty and "BL" in bl.columns:
            gdf = _clean_zone_geometries(_clip_to_bundesland(gdf, bundesland, bl))
        if gdf.empty:
            print(f"[warn]  wind zone source '{key}' has no usable features after clipping", flush=True)
            continue
        print(f"[info]  wind zones {bundesland} ({key}): n={len(gdf)}, {gdf.area.sum() / 1e6:.1f} km²", flush=True)
        zone_frames.append(gdf[["geometry"]])

    if zone_frames:
        zones = gpd.GeoDataFrame(pd.concat(zone_frames, ignore_index=True), geometry="geometry", crs=TARGET_CRS)
        mask = mask | raster_mask(zones, 0.0, grid, "official_wind_zones_by_bl")
    else:
        print("[warn]  zones enabled but no WIND_ZONE_SOURCES-source could be loaded", flush=True)

    return {"official_wind_zoning": mask}


def _build_valid_area_mask(grid: dict) -> np.ndarray:
    """Wie abschichtung_common.build_valid_area_mask(), Eingabe über
    ``_admin_boundaries()`` statt ``admin_boundaries(cfg, ...)``."""
    admin = _admin_boundaries(grid["bounds"])
    if admin.empty:
        return np.ones(grid["shape"], dtype=bool)
    return raster_mask(admin, 0.0, grid, "valid_area")


def build_wka_bestand_hulls(grid: dict, out_dir: Path, valid_area: np.ndarray) -> dict[str, np.ndarray]:
    """Wie 04_create_distance_zones.py:build_wka_bestand_hulls() (seit W6.1
    aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py). Eingabe
    ``windpower``-OSM-Layer jetzt aus ``derived/prep/osm/b_layers/
    windpower.parquet`` statt frischem Osmium-Export; ``official_wind_zoning``
    kommt aus ``out_dir`` (von build_official_zoning_masks() in DERSELBEN
    Ausführung geschrieben - muss deshalb vor dieser Gruppe laufen, siehe
    main()). Die Cluster-/Hüllen-Geometrie (unary_union, convex_hull) ist
    reine zur-Laufzeit-Algebra über Punkte - unverändert, siehe Moduldocstring
    zu Punkt 20."""
    zero = np.zeros(grid["shape"], dtype=bool)
    path = contract.PREP["osm"]["b_layers"] / "windpower.parquet"
    turbines = _read_prep_vector(path, bounds=grid["bounds"])
    if turbines.empty:
        print("[warn]  wka bestand: windpower layer leer", flush=True)
        return {WKA_BESTAND_BAND: zero}

    points = building_points(turbines)
    in_zone = sample_mask_at_points(read_layer_mask(layer_path(out_dir, "official_wind_zoning")), points, grid)
    in_austria = sample_mask_at_points(valid_area, points, grid)
    outside = points[in_austria & ~in_zone]
    print(
        f"[info]  wka bestand: turbinen={len(points):,}, in Österreich={int(in_austria.sum()):,}, "
        f"in amtlicher Zone={int((in_austria & in_zone).sum()):,}, außerhalb={len(outside):,}",
        flush=True,
    )
    if not len(outside):
        return {WKA_BESTAND_BAND: zero}

    pts = gpd.GeoSeries(gpd.points_from_xy(outside[:, 0], outside[:, 1]), crs=TARGET_CRS)
    clusters = unary_union(pts.buffer(WKA_CLUSTER_CHAIN_M / 2.0).tolist())
    blobs = list(clusters.geoms) if clusters.geom_type == "MultiPolygon" else [clusters]
    hulls = []
    for blob in blobs:
        members = pts[pts.within(blob)]
        hulls.append(unary_union(members.tolist()).convex_hull.buffer(WKA_HULL_MARGIN_M))
    print(f"[info]  wka bestand: {len(hulls)} Park-Hüllen um {len(outside)} Anlagen", flush=True)
    hull_gdf = gpd.GeoDataFrame(geometry=hulls, crs=TARGET_CRS)
    hull_mask = raster_mask(hull_gdf, 0.0, grid, WKA_BESTAND_BAND)
    # Geometriefehler (nicht Zählfehler, siehe PLAN.md/Auftrag W7.1): die
    # in_zone-Prüfung oben ist eine reine Punktabfrage je Anlage - die Hülle
    # selbst (konvexe Hülle + 200 m Rand über einen ganzen Cluster) wurde
    # bisher OHNE Abzug gegen official_wind_zoning rasterisiert und konnte
    # bei einem grenznahen Cluster in eine amtliche Zone hineinragen, obwohl
    # jede einzelne Anlage außerhalb liegt. official_wind_zoning liegt schon
    # vor - siehe Kommentar bei main(), OFFICIAL_ZONING_BANDS läuft vor
    # dieser Gruppe.
    official_zoning = read_layer_mask(layer_path(out_dir, "official_wind_zoning"))
    before = int(hull_mask.sum())
    hull_mask = hull_mask & ~official_zoning
    after = int(hull_mask.sum())
    print(
        f"[info]  wka bestand: Hüllen gegen official_wind_zoning zugeschnitten: "
        f"Zellen vorher={before:,}, nachher={after:,}, entfernt={before - after:,}",
        flush=True,
    )
    return {WKA_BESTAND_BAND: hull_mask}


# ---------------------------------------------------------------------------
# W7.1 (docs/rewrite/PLAN.md, Neuzuschnitt): vier der sechs angehängten
# Bänder 39-44 (Schnittstelle-Manifest 2.2 §2) - reine ODER-Vereinigungen
# bereits geschriebener Checkpoints dieser Kette (derived/layers/, alle vor
# dieser Stelle in main() gebaut). Bänder 40/41 (general_buildings_roh_osm/
# _dkm) liegen in pipeline/layers/osm.py - dort entstehen ihre Eingaben.
# Angehängt ans Ende von contract.LAYER_NAMES, kein bestehender Index 1-38
# verschiebt sich (siehe dortiger Kommentar).
# ---------------------------------------------------------------------------

APPENDED_BAND_NAMES = [
    "haeuser_im_gruenen_source",
    "sources_human",
    "sources_nature",
    "sources_geography",
]


def build_appended_source_aggregates(grid: dict, out_dir: Path) -> dict[str, np.ndarray]:
    """Band 39 (haeuser_im_gruenen_source): ungepuffertes Union der drei
    HiG-Quellbänder - wortgleich zu build_v2_buffers():hig_family, hier
    erneut aus dem eigenen, schon geschriebenen Checkpoint gelesen statt der
    dortigen lokalen Variable, damit dieses Band unabhängig von der
    Puffer-Gruppe bleibt (Checkpoint-Reihenfolge darf von der Bandreihenfolge
    abweichen, siehe Moduldocstring-Abschnitt oben).

    Band 42 (sources_human): laut schnittstelle-manifest-2.2.md §2 die
    ODER-Vereinigung der Bänder 1, 3-6, 8, 10, 12, 18, 19
    (official_settlement_source, die vier HiG-Quellen, nonresidential_/
    cableway_/general_buildings_source, military_restricted_area,
    airport_area_major) PLUS die ungepufferten (0 m) Linien aus
    roads/railways/aerialways.parquet ohne Tunnel - dieselbe
    Tunnel-Ausschlussregel wie bei den gepufferten Bändern 14-16
    (_non_tunnel_mask, unverändert aus calc.abschichtung_common), ohne
    Puffer (reine Quellgeometrie). roads/railways bleiben ohne fclass-Filter
    (jede Strasse/Bahnlinie zählt); aerialways bekommt seit W7.5 denselben
    fclass-Filter wie Band 10 (cableway_buildings_source) und Band 17
    (cableway_people_150m) - nur PEOPLE_CARRYING_AERIALWAY_TYPES
    (Personenseilbahnen), keine Material-/Warenseilbahnen. Vorher (bis
    W7.5) ging hier jede aerialway-Linie ungefiltert ein.

    Band 43 (sources_nature) = 21 ∪ 22, Band 44 (sources_geography) =
    23 ∪ 24 ∪ 25 ∪ 26 - beide reine Checkpoint-Unionen.

    Maßgeblich für alle drei Zusammensetzungen ist die Schnittstelle, nicht
    diese Beschreibung (siehe Bericht bei Abweichung)."""

    def own(name: str) -> np.ndarray:
        return read_layer_mask(layer_path(out_dir, name))

    hig_family = (
        own("haeuser_im_gruenen_ferienhaus")
        | own("haeuser_im_gruenen_widmung")
        | own("haeuser_im_gruenen_streusiedlung")
    )

    sources_human = (
        own("official_settlement_source")
        | hig_family
        | own("haeuser_im_gruenen_noe_pdf")
        | own("nonresidential_hulls_source")
        | own("cableway_buildings_source")
        | own("general_buildings_source")
        | own("military_restricted_area")
        | own("airport_area_major")
    )
    bounds = grid["bounds"]
    for key in ("roads", "railways", "aerialways"):
        gdf = _read_prep_vector(contract.PREP["osm"]["b_layers"] / f"{key}.parquet", bounds=bounds)
        if not gdf.empty:
            gdf = gdf[_non_tunnel_mask(gdf)]
        if key == "aerialways" and not gdf.empty:
            # W7.5: nur Personenseilbahnen (dieselbe Konstante wie Band 10/17,
            # calc.abschichtung_common.PEOPLE_CARRYING_AERIALWAY_TYPES) -
            # vorher ging hier jede aerialway-fclass ungefiltert ein.
            fclass = gdf.get("fclass", pd.Series("", index=gdf.index)).fillna("").astype(str).str.lower()
            gdf = gdf[fclass.isin(PEOPLE_CARRYING_AERIALWAY_TYPES)]
        if not gdf.empty:
            sources_human = sources_human | raster_mask(gdf, 0.0, grid, f"sources_human:{key}")

    sources_nature = own("nature_protection_areas") | own("osm_nature_protection_areas")
    sources_geography = (
        own("geography_slope_too_steep")
        | own("geography_elevation_too_high")
        | own("geography_wind_too_low")
        | own("geography_water_bodies")
    )

    return {
        "haeuser_im_gruenen_source": hig_family,
        "sources_human": sources_human,
        "sources_nature": sources_nature,
        "sources_geography": sources_geography,
    }


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente der Layer-Stufe ``geo``.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        ``config``, ``source_dir`` (seit W6.1 wirkungslos), ``out_dir`` (Vorgabe
        ``contract.DERIVED_LAYERS``), ``bbox`` und ``force_layers``.
    """
    p = argparse.ArgumentParser(
        description=(
            "Layer-Stufe W2.4: Natur, Gelände, Zonen und Puffer - die 17 "
            "Checkpoints aus scripts/widmung_v2/04_create_distance_zones.py "
            "(seit W6.1 aus dem Repo entfernt, letzter Stand im Commit "
            "f1d00f7: git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py), "
            "Eingaben aus derived/prep/ statt data/. Keine Endkomposition "
            "(GeoTIFF/Manifest) - das ist W3.1."
        )
    )
    p.add_argument("--config", default="config.json")
    p.add_argument(
        "--source-dir",
        default="(seit W6.1 wirkungslos - kein echter Pfad, siehe --help)",
        help="Seit W6.1 wirkungslos: der Rueckfall auf das frühere "
             "output/abschichtung_widmung_v2/distance_layers ist entfernt, die acht "
             "externen Quell-Checkpoints kommen ausschliesslich aus derived/layers/ "
             "(W2.1/W2.3) - fehlender Checkpoint dort bricht laut ab. Parameter bleibt "
             "nur fuer die Fehlermeldung erhalten. W6.2: der Default trug bis hierher "
             "weiterhin output/abschichtung_widmung_v2/distance_layers, obwohl dieses "
             "Verzeichnis seit W6.1 nicht mehr im Repo existiert und der Wert nirgends "
             "mehr gelesen wird (Punkt aus dem W6.2-Auftrag: 'wirkungslos, aber sie "
             "luegen') - der neue Default behauptet keinen Pfad mehr, sondern benennt "
             "sich selbst als Platzhalter.",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Ziel für die 17 eigenen Checkpoints. Default: pipeline.contract.DERIVED_LAYERS (derived/layers/).",
    )
    p.add_argument("--bbox", default=None, help="EPSG:31287 bbox minx,miny,maxx,maxy für Smoke-Tests")
    p.add_argument("--force-layers", action="store_true")
    return p.parse_args(argv)


def _check_required_sources(source_dir: Path) -> None:
    """Schwächere Fassung von 04_create_distance_zones.py:
    _check_required_sources() (seit W6.1 aus dem Repo entfernt, letzter
    Stand im Commit f1d00f7 - git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py): dessen
    REQUIRED_SOURCE_LAYERS (15 Namen) prüft
    auch die sieben Straßen-/Bahn-/Flughafen-/Militär-Checkpoints, die 04
    selbst NICHT baut, sondern nur als Vorbedingung für die spätere
    Endkomposition (compose_exclusion_geotiff(), trailing_bands) einfordert -
    genau der Teil, der laut Moduldocstring hier NICHT dazugehört (W3.1).
    Diese Stufe prüft nur die acht externen Checkpoints, die ihre eigenen
    Baufunktionen (build_hig_family_sources, build_v2_buffers) tatsächlich
    LESEN - eine engere, aber für diesen Auftrag vollständige Vorbedingung.

    W5.P1: prüfte zuerst ``derived/layers/`` (W2.1/W2.3 in DIESER Kette
    gelaufen) und erst danach ``source_dir`` - genau das Muster aus
    ``pipeline/layers/osm.py:_require_hig_layers()``. **W6.1:** der
    Rückfall auf ``source_dir`` (Default: ``output/abschichtung_widmung_v2/
    distance_layers``) ist entfernt, dieses Verzeichnis existiert seit W6.1
    nicht mehr im Repo - diese Funktion prüft jetzt ausschließlich
    ``derived/layers/`` und bricht laut ab, wenn dort etwas fehlt.
    ``source_dir`` bleibt Parameter (für die Fehlermeldung und
    CLI-Kompatibilität), wird aber nicht mehr gelesen."""
    required = [
        "official_settlement_source",
        "ferienhaus_tourismus_source",
        "official_hig_source",
        "hig_hulls_source",
        "noe_pdf_750m_zones",
        "nonresidential_hulls_source",
        "cableway_buildings_source",
        "general_buildings_source",
    ]
    missing = [name for name in required if not contract.LAYERS[name].exists()]
    if missing:
        raise FileNotFoundError(
            f"Fehlende externe Quell-Checkpoints: {', '.join(missing)}. "
            f"Unter {contract.DERIVED_LAYERS} nicht gefunden - diese kommen aus "
            "W2.1/W2.3 (pipeline/layers/hig.py, osm.py; heute: "
            "'make layer-hig layer-osm'). Der Rueckfall auf einen frueheren "
            f"Kettenlauf unter {source_dir} ist seit W6.1 entfernt."
        )


def main(argv: list[str] | None = None) -> None:
    """Baut die Checkpoints der Layer-Stufe ``geo``.

    Prüft zuerst mit ``_check_required_sources()``, dass die Quell-Checkpoints
    aus ``hig`` und ``osm`` vorliegen. Baut dann über ``ensure_group_layers()``
    nacheinander HiG-Familie, Puffer, Natur, Geografie, Gewässer, amtliche
    Windzonen, WKA-Bestand und die angehängten Bänder 39, 42-44. Eine Gruppe
    wird übersprungen, wenn ihre Checkpoints vorliegen und die Tags
    (``PREP_FINGERPRINT``, ``BAND_SCHEMA``) passen, außer mit ``--force-layers``.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.

    Raises
    ------
    FileNotFoundError
        Wenn externe Quell-Checkpoints unter ``derived/layers/`` fehlen.
    """
    args = parse_args(argv)
    cfg = load_config(args.config)
    grid = load_grid(cfg, args.bbox)

    source_dir = Path(args.source_dir)
    out_dir = Path(args.out_dir) if args.out_dir else contract.DERIVED_LAYERS
    runtime.ensure_dir(out_dir)

    _check_required_sources(source_dir)

    derived_tags = {"PREP_FINGERPRINT": _fingerprint_tag(), "BAND_SCHEMA": BAND_SCHEMA}

    def _tags_ok(expected: dict):
        return lambda tags: all(tags.get(k) == v for k, v in expected.items())

    with timed("W2.4 geo/natur/zonen/puffer layers"):
        # Die HiG-Familie muss vor den Puffern stehen: build_v2_buffers()
        # liest ihre eben geschriebenen Checkpoints aus out_dir.
        ensure_group_layers(
            out_dir, HIG_FAMILY_SOURCE_BANDS, "HiG family sources",
            lambda: build_hig_family_sources(grid, source_dir), grid, args.force_layers,
            extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
        )
        ensure_group_layers(
            out_dir, BUFFER_BANDS, "v2 buffers",
            lambda: build_v2_buffers(grid, source_dir, out_dir), grid, args.force_layers,
            extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
        )
        ensure_group_layers(
            out_dir, NATURE_BANDS, "nature masks",
            lambda: build_nature_masks(grid), grid, args.force_layers,
            extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
        )
        ensure_group_layers(
            out_dir, GEOGRAPHY_BANDS, "geography masks",
            lambda: build_geography_masks(cfg, grid), grid, args.force_layers,
            extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
        )
        ensure_group_layers(
            out_dir, WATER_BANDS, "water masks",
            lambda: build_water_masks(grid), grid, args.force_layers,
            extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
        )
        # Vor wka bestand: der testet seine Punkte an official_wind_zoning.
        ensure_group_layers(
            out_dir, OFFICIAL_ZONING_BANDS, "official wind zoning reference",
            lambda: build_official_zoning_masks(grid), grid, args.force_layers,
            extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
        )
        valid_area = _build_valid_area_mask(grid)
        wka_tags = {
            **derived_tags,
            "WKA_PARAMS": f"chain={WKA_CLUSTER_CHAIN_M:g}|margin={WKA_HULL_MARGIN_M:g}|"
                          f"zoning={layer_path(out_dir, OFFICIAL_ZONING_BANDS[0]).stat().st_mtime_ns}",
        }
        ensure_group_layers(
            out_dir, [WKA_BESTAND_BAND], "wka bestand hulls",
            lambda: build_wka_bestand_hulls(grid, out_dir, valid_area), grid, args.force_layers,
            extra_ok=_tags_ok(wka_tags), extra_tags=wka_tags,
        )
        # W7.1: angehängte Bänder 39, 42-44 (Schnittstelle 2.2 §2) - laufen
        # nach allen sieben Gruppen oben, weil sie deren Checkpoints lesen;
        # die Bandreihenfolge (contract.LAYER_NAMES) bleibt davon unberührt,
        # nur die Baureihenfolge ist hier zuletzt.
        ensure_group_layers(
            out_dir, APPENDED_BAND_NAMES, "appended bands 39, 42-44 (Schnittstelle 2.2)",
            lambda: build_appended_source_aggregates(grid, out_dir), grid, args.force_layers,
            extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
        )

    all_names = [*HIG_FAMILY_SOURCE_BANDS, *BUFFER_BANDS, *NATURE_BANDS, *GEOGRAPHY_BANDS, *WATER_BANDS, *OFFICIAL_ZONING_BANDS, WKA_BESTAND_BAND, *APPENDED_BAND_NAMES]
    print(f"pipeline.layers.geo: {len(all_names)} Checkpoints in {out_dir}")


if __name__ == "__main__":
    main()
