"""Layer-Stufe: OSM-Restlayer + Infrastruktur-/Flughafenmasken (Paket W2.3,
docs/rewrite/PLAN.md §7). Übernommen aus
``scripts/widmung_v2/03_build_osm_layers.py`` - genau der Teil, der die
neun Checkpoints in ``OSM_LAYER_NAMES``/``INFRA_LAYER_NAMES``/
``AIRPORT_LAYER_NAMES`` erzeugt (die übrigen Checkpoints der v2-Kette
kommen aus ``02_build_hig_sources.py`` bzw. den Paketen W2.1/W2.4).

Erzeugt: cableway_buildings_source, general_buildings_source (OSM-Gebäude,
zwei disjunkte Restklassen unter den amtlich nicht abgedeckten Gebäuden),
road_motorway_trunk, road_federal_state, rail_main, cableway_people_150m,
military_restricted_area (Infrastrukturmasken) sowie airport_area_major,
airport_runway_corridor_5km (Flughafen-Korridorbänder). Fachliche Regeln
(Distanzen, Fahrzeugtypen, Militärflächen-Typenliste usw.) sind Regel 4
gemäß UNVERÄNDERT aus ``calc/abschichtung_common.py`` übernommen
- teils per Import der dortigen (auch privaten) Hilfsfunktionen, um zwei
Kopien derselben Regel zu vermeiden (Muster wie
``pipeline/prep/adressen.py``), teils als direkter Nachbau der drei
Erzeuger-Funktionen ``build_osm_building_sources``/
``build_infrastructure_masks``/``build_airport_corridor_masks`` mit exakt
einer Änderung: wo das Original ``osm_layer_path()`` (ruft ``osmium``
gegen die PBF-Rohdatei) + ``read_layer()`` aufruft, liest diese Stufe
stattdessen aus den bereits von der Prep-Stufe erzeugten Parquet-Dateien
unter ``derived/prep/osm/b_layers/`` (``pipeline/prep/osm.py``, Paket
W1.P5) - siehe ``_read_prep_layer()`` unten. fclass/type-Ableitung und
Reprojektion nach EPSG:31287 sind bereits Teil der Prep-Stufe
(``pipeline/prep/osm.py:_read_exported()``, wortgleich zu
``read_layer()``); hier bleibt nur der bounds-Zuschnitt übrig, den
``read_layer()`` ebenfalls zuletzt anwendet.

Bewusst NICHT nachgebaut (reine Plumbing-Vereinfachung, keine fachliche
Änderung): die tote ``powerlines_gpkg``-Rückfallkette in
``build_infrastructure_masks`` (Original-Zeilen ~966-972) - ``cfg["paths"]``
trägt diesen Schlüssel seit Paket W1.2 nicht mehr (siehe
``config.json:_comment_powerlines_gpkg_entfernt``), der Zweig griff also
schon im Original nie. Ebenso entfallen die CLI-Parameter
``--osm-pbf``/``--osm-pbf-cache-dir``: die Quelle ist in dieser Stufe fest
``pipeline.contract.PREP["osm"]["b_layers"]``, nicht mehr konfigurierbar.

## Punkt 27 - power_380_400kv bleibt unpersistiert (nur nachgebaut, siehe
Auftrag): ``INFRA_LAYER_NAMES`` lässt ``power_380_400kv`` bewusst aus (seit
dem Clean-Schema Aug 2026 kein Ausschlusskriterium mehr der v2-Kette,
Kommentar im Original bei Zeile 92-93). ``build_infrastructure_masks``
berechnet die Maske trotzdem bei jedem Lauf (liest ``powerlines.parquet``,
klassifiziert, rasterisiert) - nur um sie nicht zu schreiben. Diese Stufe
baut das unverändert nach: keine Bedingung, die den Aufwand überspringt,
wenn ``power_380_400kv`` ohnehin nicht gebraucht wird. Kosten/Alternative:
siehe Abschlussbericht zu W2.3 (nicht hier entschieden - Regel 4).

## Fingerabdruck-Konvention für die Layer-Stufe (Antwort auf die in
``pipeline/fingerprint.py`` als Nebenbefund offene Frage, PLAN.md §12)

Ja: diese Stufe prüft den Fingerabdruck ihrer tatsächlich gelesenen
Prep-Eingaben (die acht ``derived/prep/osm/b_layers/*.parquet``-Dateien, die
``build_osm_building_sources``/``build_infrastructure_masks``/
``build_airport_corridor_masks`` lesen - ``buildings``, ``windpower``,
``aerialways``, ``powerlines``, ``roads``, ``railways``, ``military``,
``transport``; NICHT ``nature``/``water``, die diese Stufe nie liest),
bevor sie einen vorhandenen Checkpoint beim Wiederaufsetzen als fertig
akzeptiert. Begründung: ``layer_done()``
(``calc/abschichtung_common.py:1421``) prüft nur Form/CRS/
Transform/Bandname des *Ausgabe*-Rasters - das erkennt zuverlässig einen
Gitterwechsel, aber nicht, dass sich die *Eingabe* (ein erneuter
Prep-Lauf mit geänderter PBF-Stichtagsversion o.ä.) seit dem Bau des
Checkpoints geändert hat. Genau das beschreibt ``pipeline/fingerprint.py``
als Zweck des Mechanismus, den es schon für die Prep-Stufen selbst gibt -
und zwei der neun Prep-Module (siehe dessen Docstring) nutzen ihn bereits
dafür, sich selbst zu überspringen. Diese Stufe wendet denselben
Mechanismus eine Ebene höher an, statt eine eigene, leicht andere Prüfung
zu erfinden.

Ablage: EIN Tag ``PREP_FINGERPRINT`` (SHA-256 über
``pipeline.fingerprint.compute()`` der acht Parquets, siehe
``_fingerprint_tag()`` unten) in jedem der neun Checkpoints dieser Stufe,
geprüft über den ``extra_ok``/``extra_tags``-Mechanismus von
``layer_done()`` - **nicht** eine separate Fingerabdruck-Datei. So ursprünglich
(vor dem Zusammenführen von W2.1/W2.3/W2.4) in diesem Modul umgesetzt:
``FP_DIR = contract.DERIVED_LAYERS / "_fingerprints" / "osm"`` plus
``fingerprint.matches()``/``.write()`` gegen eine
``.fingerprint.json``-Datei, entkoppelt vom eigentlichen Ausgabe-Raster.
W2.1 (``pipeline/layers/hig.py``) und W2.4 (``pipeline/layers/geo.py``)
lösten dieselbe Frage stattdessen mit einem Tag - der Kommentar bei
``04_create_distance_zones.py:446-447`` benennt für genau diesen Zweck
schon eine Tag-Lücke im Ausgabeformat. Diese Stufe wurde beim
Zusammenführen auf die Tag-Variante umgestellt: ein Tag wandert mit der
Datei, eine Nebendatei kann von ihr getrennt gelöscht, verschoben oder
vergessen werden, ohne dass das auffällt.

Verworfen bleibt (unabhängig von Tag vs. Datei): ein harter Abbruch bei
Fingerabdruck-Mismatch (den ``pipeline/fingerprint.py`` als Möglichkeit
nennt - "kann dann hart abbrechen"). Diese Stufe rechnet stattdessen
automatisch neu, weil ihr ``layer_done()``-Aufruf beim Mismatch
``extra_ok`` fehlschlagen lässt und der Checkpoint dadurch in
``missing`` landet: ein Mismatch bedeutet praktisch "die Prep-Stufe ist
seit dem letzten Layer-Lauf erneut gelaufen", was routinemäßig vorkommt
(z. B. neue PBF-Stichtagsversion) und keinen Abbruch rechtfertigt - anders
als etwa ein fehlendes Werkzeug (``osmium``), das die Prep-Stufe selbst
schon hart abbrechen lässt.

Granularität: EIN Fingerabdruck-Tag für alle drei Checkpoint-Gruppen dieser
Stufe (``OSM_LAYER_NAMES``, ``INFRA_LAYER_NAMES``, ``AIRPORT_LAYER_NAMES``),
nicht drei getrennte - dieselbe Prep-Ausgabe (``b_layers/``) speist alle
drei, eine Trennung nach einzelnen Parquet-Dateien wäre genauer (z. B.
würde eine reine ``transport.parquet``-Änderung nur ``AIRPORT_LAYER_NAMES``
betreffen), aber unnötig komplex für den Nutzen - ein Fingerabdruck-Mismatch
löst ohnehin nur einen erneuten Rechenlauf der betroffenen Checkpoints aus
(``ensure_group_layers()`` baut pro Gruppe nur ``missing`` neu), keinen
Datenverlust.

Was NICHT geprüft wird: die HIG-Vorbedingungs-Checkpoints
(``OFFICIAL_COVER_LAYERS``, aus dem HIG-Paket W2.1) haben ihren EIGENEN,
schon im Original vorhandenen Mechanismus (``HIG_SOURCE_FINGERPRINT``-Tag,
mtime-basiert, siehe ``_cover_fingerprint()`` unten, unverändert
portiert) - der bleibt parallel bestehen, als eigener Tag neben
``PREP_FINGERPRINT``, unberührt von der hier beschriebenen
Prep-Fingerabdruck-Prüfung. Ebenfalls nicht geprüft: der Inhalt der
Prep-Parquet-Dateien (nur Größe/Änderungszeit, siehe
``pipeline/fingerprint.py``-Docstring - ein Hash über die bis zu 900 MB
große ``buildings.parquet`` bei jedem Lauf wäre selbst der Aufwand, den
die Prep-Stufe vermeiden soll) und nicht die Konfiguration
(``config.json``, ``--mode``/``--total-height-m`` usw.) - eine geänderte
Regel-Distanz erkennt nach wie vor nur ``--force-layers`` von Hand, wie im
Original.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from pipeline import contract, fingerprint, runtime
from calc.config import load_config
from calc.abschichtung_common import (
    AIRPORT_CORRIDOR_HALF_ANGLE_DEG,
    AIRPORT_CORRIDOR_LENGTH_M,
    AIRPORT_RUNWAY_MIN_LENGTH_M,
    BUILDING_CLASSIFICATION_REVISION,
    CABLEWAY_BUILDING_MATCH_RADIUS_M,
    MILITARY_AREA_TYPES,
    PEOPLE_CARRYING_AERIALWAY_TYPES,
    TARGET_CRS,
    _corridor_wedge,
    _non_tunnel_mask,
    _power_line_mask,
    _runway_axes,
    admin_boundaries,
    building_points,
    drop_wind_power_buildings,
    ensure_group_layers,
    expand_bounds,
    load_grid,
    raster_mask,
    read_layer_mask,
    rule_distance,
    sample_mask_at_points,
    timed,
)

# ---------------------------------------------------------------------------
# Wortgleich aus scripts/widmung_v2/03_build_osm_layers.py übernommen
# (Zeilen 78-101). Seit W6.1 aus dem Repo entfernt, letzter Stand im Commit
# f1d00f7 - git show f1d00f7:scripts/widmung_v2/03_build_osm_layers.py.
# ---------------------------------------------------------------------------

# Von build_hig_sources.py (bzw. künftig pipeline/layers/hig.py, Paket W2.1)
# geschriebene Quellen, die hier abgedeckte Flächen markieren.
# bewohnt_einzellage_source zählt mit: diese Gebäude haben schon ihr eigenes
# 25-m-Band und sollen nicht noch einmal als general_buildings klassifiziert werden.
OFFICIAL_COVER_LAYERS = [
    "official_settlement_source",
    "ferienhaus_tourismus_source",
    "official_hig_source",
    "noe_pdf_750m_zones",
    "hig_hulls_source",
    "bewohnt_einzellage_source",
]

OSM_LAYER_NAMES = [
    "cableway_buildings_source",
    "general_buildings_source",
]

# W7.1 (Schnittstelle-Manifest 2.2 §2, Bänder 40/41): die rohen OSM-/DKM-
# Teilmengen, aus denen general_buildings_source (oben) sich zusammensetzt -
# angehängt ans Ende von contract.LAYER_NAMES, NICHT Teil von
# OSM_LAYER_NAMES (das bleibt die unveränderte 33er/38er-Kette, siehe
# tests/test_contract.py). Beide entstehen als Nebenprodukt derselben
# build_osm_building_sources()-Berechnung, hier nur zusätzlich benannt.
APPENDED_OSM_BAND_NAMES = ["general_buildings_roh_osm", "general_buildings_roh_dkm"]

# power_380_400kv fehlt bewusst: Stromleitungen sind seit dem Clean-Schema
# (Aug 2026) kein Ausschlusskriterium der v2-Kette mehr. Siehe Punkt 27 im
# Moduldocstring - build_infrastructure_masks() (unten) berechnet die Maske
# trotzdem, sie wird nur nicht persistiert.
INFRA_LAYER_NAMES = [
    "road_motorway_trunk",
    "road_federal_state",
    "rail_main",
    "cableway_people_150m",
    "military_restricted_area",
]
AIRPORT_LAYER_NAMES = ["airport_area_major", "airport_runway_corridor_5km"]

# Die acht b_layers-Objektgruppen, die diese Stufe tatsächlich liest (siehe
# build_osm_building_sources/build_infrastructure_masks/
# build_airport_corridor_masks unten) - Basis des Prep-Fingerabdrucks, siehe
# Moduldocstring. "nature"/"water" fehlen bewusst: kein Konsument hier.
PREP_INPUT_KEYS = [
    "buildings", "windpower", "aerialways", "powerlines",
    "roads", "railways", "military", "transport",
]

def _prep_inputs() -> list[Path]:
    """Die acht tatsächlich gelesenen ``b_layers``-Parquets dieser Stufe.

    Returns
    -------
    list of Path
        Je ein Pfad ``<key>.parquet`` unter ``contract.PREP["osm"]["b_layers"]``
        für jeden Schlüssel in ``PREP_INPUT_KEYS``.
    """
    b_dir = contract.PREP["osm"]["b_layers"]
    return [b_dir / f"{key}.parquet" for key in PREP_INPUT_KEYS]


def _fingerprint_tag() -> str:
    """SHA-256 über ``pipeline.fingerprint.compute()`` der acht tatsächlich
    gelesenen ``b_layers``-Parquets, als ``PREP_FINGERPRINT``-Tag in jedem
    Checkpoint dieser Stufe abgelegt - dieselbe Konvention wie
    ``pipeline/layers/hig.py``/``pipeline/layers/geo.py`` (siehe
    Moduldocstring "Fingerabdruck-Konvention"), an die diese Stufe beim
    Zusammenführen von W2.1/W2.3/W2.4 angeglichen wurde. Ersetzt die
    ursprüngliche, hier verworfene Variante mit einer separaten
    ``.fingerprint.json`` unter ``derived/layers/_fingerprints/osm/``: ein Tag
    wandert mit der Rasterdatei, eine Nebendatei kann von ihr getrennt
    verlorengehen."""
    data = fingerprint.compute(_prep_inputs())
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def _read_prep_layer(key: str, bounds: tuple[float, float, float, float] | None) -> gpd.GeoDataFrame:
    """OSM-Objektgruppe aus der Prep-Stufe lesen, statt sie selbst aus der
    PBF-Datei zu extrahieren (osm_layer_path()+read_layer() im
    Originalskript).

    fclass/type-Ableitung und Reprojektion nach TARGET_CRS sind bereits Teil
    von ``pipeline/prep/osm.py:_read_exported()`` (wortgleich zu
    ``read_layer()``, Zeilen ~517-546 in abschichtung_common.py) - hier
    bleibt nur der bounds-Zuschnitt übrig, den ``read_layer()`` ebenfalls
    zuletzt anwendet (``gdf[gdf.geometry.intersects(bbox_geom)]``).
    Spaltenauswahl (``OSM_PBF_COLUMNS``) wird bewusst NICHT nachgebaut - sie
    ist im Original reine Speicher-Optimierung beim Lesen aus einem
    bbox-Ausschnitt (``columns=`` an pyogrio) und ändert kein Ergebnis: jede
    nachgelagerte Klassifikationsregel greift ohnehin per
    ``gdf.get(spalte, vorgabe)`` mit Vorgabewert, zusätzliche Spalten stören
    nicht (siehe pipeline/prep/osm.py-Docstring, Abschnitt "Was NICHT
    nachgebaut wird" - dieselbe Abgrenzung, hier auf der Leserseite).
    """
    path = contract.PREP["osm"]["b_layers"] / f"{key}.parquet"
    if not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    gdf = gpd.read_parquet(path)
    if gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    if bounds is not None:
        bbox_geom = box(*bounds)
        gdf = gdf[gdf.geometry.intersects(bbox_geom)].copy()
    return gdf


def _cover_layer_path(name: str, legacy_cover_dir: Path) -> Path:
    """Pfad zu einem OFFICIAL_COVER_LAYERS-Checkpoint (Vorbedingung aus dem
    HIG-Paket W2.1, ``pipeline/layers/hig.py``).

    Ausschließlich der Zielort dieser Pipeline (``contract.LAYERS[name]``
    unter ``derived/layers/``, von W2.1 geschrieben).

    **W6.1:** der frühere Rückfall auf den geteilten, NUR LESEND
    zugänglichen ``output/abschichtung_widmung_v2/distance_layers/``
    (Symlink auf das Hauptrepo, run1) ist entfernt - dieses Verzeichnis
    existiert seit W6.1 nicht mehr im Repo (``output/`` wurde nach
    ``~/Documents/master_windkraft/archiv/`` herausbewegt). Fehlt der Layer
    unter ``derived/layers/``, bricht dieser Aufruf jetzt sofort mit benannter
    Meldung ab, statt still auf ein Verzeichnis zurückzufallen, das es nicht
    mehr gibt. ``legacy_cover_dir`` bleibt Parameter (für die Fehlermeldung
    und CLI-Kompatibilität von ``--legacy-cover-dir``), wird aber nicht mehr
    gelesen; diese Stufe schrieb ohnehin NIE dorthin, nur nach
    ``contract.DERIVED_LAYERS`` (siehe main()).
    """
    build_path = contract.LAYERS[name]
    if not build_path.exists():
        raise FileNotFoundError(
            f"'{name}' fehlt unter {build_path} (derived/layers/) - der Rueckfall "
            f"auf die alte Kette ({legacy_cover_dir}) ist seit W6.1 entfernt, "
            "dieses Verzeichnis existiert nicht mehr im Repo. HIG-Quellen "
            "zuerst bauen (Paket W2.1, pipeline/layers/hig.py; heute: "
            "'make layer-hig')."
        )
    return build_path


def _require_hig_layers(legacy_cover_dir: Path) -> None:
    """W6.1: prüft nur noch ``contract.LAYERS`` direkt (nicht über
    ``_cover_layer_path()``, die bei einem fehlenden Layer jetzt selbst
    abbricht) - so sammelt diese Funktion weiterhin ALLE fehlenden
    Checkpoints in einer Meldung, statt beim ersten fehlenden abzubrechen."""
    missing = [n for n in OFFICIAL_COVER_LAYERS if not contract.LAYERS[n].exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing checkpoint(s): {', '.join(missing)}. "
            f"Unter {contract.DERIVED_LAYERS} nicht gefunden - der Rueckfall auf "
            f"die alte Kette ({legacy_cover_dir}) ist seit W6.1 entfernt, dieses "
            "Verzeichnis existiert nicht mehr. HIG-Quellen zuerst bauen "
            "(Paket W2.1, pipeline/layers/hig.py; heute: 'make layer-hig')."
        )


def _official_cover_mask(grid: dict, legacy_cover_dir: Path) -> np.ndarray:
    """Vereinigung der HiG-Vorbedingungs-Checkpoints.

    Parameters
    ----------
    grid : dict
        Zielraster.
    legacy_cover_dir : Path
        Seit W6.1 wirkungslos; nur für die Fehlermeldung von
        ``_cover_layer_path()``.

    Returns
    -------
    numpy.ndarray
        Bool-Maske, ODER-verknüpft über alle Layer in ``OFFICIAL_COVER_LAYERS``.
    """
    mask = np.zeros(grid["shape"], dtype=bool)
    for name in OFFICIAL_COVER_LAYERS:
        mask |= read_layer_mask(_cover_layer_path(name, legacy_cover_dir))
    return mask


def _as_mask(gdf: gpd.GeoDataFrame, grid: dict, label: str) -> np.ndarray:
    """Rastert einen GeoDataFrame ohne Puffer auf das Zielraster.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Zu rasternde Geometrien.
    grid : dict
        Zielraster.
    label : str
        Bezeichnung, an ``raster_mask()`` durchgereicht.

    Returns
    -------
    numpy.ndarray
        Bool-Maske; leer (alles ``False``), wenn ``gdf`` leer ist.
    """
    if gdf.empty:
        return np.zeros(grid["shape"], dtype=bool)
    return raster_mask(gpd.GeoDataFrame(gdf, geometry="geometry", crs=TARGET_CRS), 0.0, grid, label)


def build_osm_building_sources(cfg: dict, grid: dict, args: argparse.Namespace) -> dict[str, np.ndarray]:
    """Zwei disjunkte OSM-Gebäudeklassen unter den amtlich nicht abgedeckten
    Gebäuden. Fachlich unverändert aus ``03_build_osm_layers.py``
    (seit W6.1 aus dem Repo entfernt; letzter Stand im Commit ``f1d00f7``,
    abrufbar mit ``git show f1d00f7:scripts/widmung_v2/03_build_osm_layers.py``):
    build_osm_building_sources (Zeilen 126-200) - einzige Änderung: OSM-Lesen
    über ``_read_prep_layer()`` statt ``osm_layer_path()``+``read_layer()``.
    """
    legacy_cover_dir = Path(args.legacy_cover_dir)
    _require_hig_layers(legacy_cover_dir)
    covered_mask = _official_cover_mask(grid, legacy_cover_dir)

    source_bounds = expand_bounds(grid["bounds"], CABLEWAY_BUILDING_MATCH_RADIUS_M)
    buildings = _read_prep_layer("buildings", source_bounds)
    buildings = buildings[buildings.geometry.notnull() & ~buildings.geometry.is_empty].copy()

    # Windkraftanlagen raus, BEVOR eine der zwei Kategorien greift - siehe
    # drop_wind_power_buildings()-Docstring in abschichtung_common.py.
    wind_power = _read_prep_layer("windpower", source_bounds)
    buildings = drop_wind_power_buildings(buildings, wind_power)

    aerialways = _read_prep_layer("aerialways", source_bounds)
    # W7.5: nur Personenseilbahnen (PEOPLE_CARRYING_AERIALWAY_TYPES) zählen
    # als Seilbahngebäude - Warengondeln/Materialseilbahnen (fclass "goods"
    # u.ä.) sollen dieses Band nicht speisen. Bisher ungefiltert (alle
    # aerialway-fclass-Werte); dieselbe Konstante wie
    # cableway_people_150m (build_infrastructure_masks() unten) und
    # sources_human (pipeline/layers/geo.py) - eine Liste, drei Leser.
    af = (
        aerialways.get("fclass", pd.Series("", index=aerialways.index)).fillna("").astype(str).str.lower()
        if not aerialways.empty
        else pd.Series([], dtype=str)
    )
    aerialways = aerialways[af.isin(PEOPLE_CARRYING_AERIALWAY_TYPES)] if not aerialways.empty else aerialways

    # Bewohnte Einzellagen (< 5 adressierte Objekte) und die Bauflächen der
    # NÖ-Streusiedlungs-Hüllen zählen zu den allgemeinen Gebäuden (25 m).
    dkm_footprints = read_layer_mask(_cover_layer_path("bewohnt_einzellage_source", legacy_cover_dir))
    bl = admin_boundaries(cfg, grid["bounds"])
    noe = bl[bl["BL"].eq("Niederösterreich")] if "BL" in bl.columns else bl.iloc[0:0]
    if not noe.empty:
        noe_mask = raster_mask(noe[["geometry"]].copy(), 0.0, grid, "NÖ Landesfläche")
        dkm_footprints = dkm_footprints | (
            read_layer_mask(_cover_layer_path("hig_hulls_source", legacy_cover_dir)) & noe_mask
        )

    zero = np.zeros(grid["shape"], dtype=bool)
    if buildings.empty:
        return {
            "cableway_buildings_source": zero,
            "general_buildings_source": dkm_footprints,
            # W7.1, Bänder 40/41: kein OSM-Gebäudebestand gelesen -> die
            # OSM-Teilmenge ist leer, general_buildings_source besteht in
            # diesem Fall vollständig aus dem DKM-Fußabdruck.
            "general_buildings_roh_osm": zero,
            "general_buildings_roh_dkm": dkm_footprints,
        }

    points = building_points(buildings)
    covered = sample_mask_at_points(covered_mask, points, grid)

    # 1) Seilbahngebäude: Restgebäude nahe einer Aerialway-Linie.
    remaining = ~covered
    cableway_proximity = (
        raster_mask(aerialways, CABLEWAY_BUILDING_MATCH_RADIUS_M, grid, "cableway_proximity")
        if not aerialways.empty
        else zero
    )
    is_cableway = remaining & sample_mask_at_points(cableway_proximity, points, grid)
    cableway_source = _as_mask(buildings.loc[is_cableway, ["geometry"]], grid, "cableway_buildings")

    # 2) Alles Übrige ist geringfügig/unklassifiziert - plus die DKM/BEV-
    #    Fußabdrücke (Einzellagen + NÖ-Streusiedlungs-Bauflächen).
    is_general = remaining & (~is_cableway)
    # W7.1, Bänder 40/41: general_buildings_roh_osm (nur der OSM-Teil) und
    # general_buildings_roh_dkm (nur der DKM/BEV-Fußabdruck) - die beiden
    # rohen Teilmengen, deren Union general_buildings_source (Band 12) ist.
    general_osm_only = _as_mask(buildings.loc[is_general, ["geometry"]], grid, "general_buildings_source")
    general_source = general_osm_only | dkm_footprints

    print(
        "[info]  v2 OSM-Gebäudeklassifikation: "
        f"osm_buildings={len(buildings):,}, amtlich_abgedeckt={int(covered.sum()):,}, "
        f"cableway={int(is_cableway.sum()):,}, "
        f"general={int(is_general.sum()):,} + dkm_fussabdruck_zellen={int(dkm_footprints.sum()):,}",
        flush=True,
    )
    return {
        "cableway_buildings_source": cableway_source,
        "general_buildings_source": general_source,
        "general_buildings_roh_osm": general_osm_only,
        "general_buildings_roh_dkm": dkm_footprints,
    }


def _cover_fingerprint(legacy_cover_dir: Path) -> str:
    """Content fingerprint of the HIG sources' outputs, for checkpoint
    invalidation. Unverändert aus ``03_build_osm_layers.py:_cover_fingerprint``
    (seit W6.1 aus dem Repo entfernt; letzter Stand im Commit ``f1d00f7``,
    abrufbar mit ``git show f1d00f7:scripts/widmung_v2/03_build_osm_layers.py``)
    - einzige Änderung: Pfadauflösung über _cover_layer_path() statt
    layer_path(layer_dir, name), siehe dort."""
    parts = []
    for name in OFFICIAL_COVER_LAYERS:
        p = _cover_layer_path(name, legacy_cover_dir)
        parts.append(f"{name}:{p.stat().st_mtime_ns}" if p.exists() else f"{name}:missing")
    return "|".join(parts)


def build_infrastructure_masks(cfg: dict, grid: dict, args: argparse.Namespace) -> dict[str, np.ndarray]:
    """Roads/rail/power/cableway/military masks. Fachlich unverändert aus der
    (seit W7.5 entfernten) toten Kopie abschichtung_common.py:
    build_infrastructure_masks - siehe dort für die Begründung der
    Entfernung; diese Fassung hier ist die einzige aktive, aufgerufene -
    einzige Änderungen ggü. der entfernten Kopie: OSM-Lesen über ``_read_prep_layer()`` statt
    ``osm_layer_path()``+``read_layer()``, und die tote
    ``powerlines_gpkg``-Rückfallkette entfällt (siehe Moduldocstring - der
    Zweig griff schon im Original nie, ``cfg["paths"]`` kennt den Schlüssel
    seit W1.2 nicht mehr).
    """
    bounds = grid["bounds"]
    masks: dict[str, np.ndarray] = {}

    power_bounds = expand_bounds(bounds, 150.0)
    power = _read_prep_layer("powerlines", power_bounds)
    if not power.empty:
        high_voltage = _power_line_mask(power, {380, 400})
        print(f"[info]  power line filter: features={len(power):,}, 380/400kV_lines={int(high_voltage.sum()):,}", flush=True)
        masks["power_380_400kv"] = raster_mask(
            power[high_voltage], rule_distance("power_380_400kv", args.mode, args.total_height_m), grid, "power_380_400kv"
        )
    else:
        masks["power_380_400kv"] = np.zeros(grid["shape"], dtype=bool)

    max_road_buffer = max(
        rule_distance("road_motorway_trunk", args.mode, args.total_height_m),
        rule_distance("road_federal_state", args.mode, args.total_height_m),
    )
    road_bounds = expand_bounds(bounds, max_road_buffer)
    roads = _read_prep_layer("roads", road_bounds)
    f = roads.get("fclass", pd.Series("", index=roads.index)).fillna("").astype(str) if not roads.empty else pd.Series([], dtype=str)
    road_not_tunnel = _non_tunnel_mask(roads)
    masks["road_motorway_trunk"] = raster_mask(
        roads[road_not_tunnel & f.isin(["motorway", "motorway_link", "trunk", "trunk_link"])] if not roads.empty else roads,
        rule_distance("road_motorway_trunk", args.mode, args.total_height_m), grid, "road_motorway_trunk",
    )
    masks["road_federal_state"] = raster_mask(
        roads[road_not_tunnel & f.isin(["primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"])] if not roads.empty else roads,
        rule_distance("road_federal_state", args.mode, args.total_height_m), grid, "road_federal_state",
    )

    rail_bounds = expand_bounds(bounds, rule_distance("rail_main", args.mode, args.total_height_m))
    rail = _read_prep_layer("railways", rail_bounds)
    rf = rail.get("fclass", pd.Series("", index=rail.index)).fillna("").astype(str) if not rail.empty else pd.Series([], dtype=str)
    rail_not_tunnel = _non_tunnel_mask(rail)
    masks["rail_main"] = raster_mask(
        rail[rail_not_tunnel & rf.isin(["rail", "narrow_gauge"])] if not rail.empty else rail,
        rule_distance("rail_main", args.mode, args.total_height_m), grid, "rail_main",
    )

    cable_bounds = expand_bounds(bounds, rule_distance("cableway_people_150m", args.mode, args.total_height_m))
    aerialways = _read_prep_layer("aerialways", cable_bounds)
    af = aerialways.get("fclass", pd.Series("", index=aerialways.index)).fillna("").astype(str).str.lower() if not aerialways.empty else pd.Series([], dtype=str)
    people_aerialways = aerialways[af.isin(PEOPLE_CARRYING_AERIALWAY_TYPES)] if not aerialways.empty else aerialways
    masks["cableway_people_150m"] = raster_mask(
        people_aerialways, rule_distance("cableway_people_150m", args.mode, args.total_height_m), grid, "cableway_people_150m"
    )

    military_bounds = bounds
    military = _read_prep_layer("military", military_bounds)
    if not military.empty:
        geom_area = military.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
        landuse = military.get("landuse", pd.Series("", index=military.index)).fillna("").astype(str).str.lower()
        mtype = military.get("military", pd.Series("", index=military.index)).fillna("").astype(str).str.lower()
        military_area = military[geom_area & (landuse.eq("military") | mtype.isin(MILITARY_AREA_TYPES))]
    else:
        military_area = military
    masks["military_restricted_area"] = raster_mask(military_area, 0.0, grid, "military_restricted_area")

    return masks


def build_airport_corridor_masks(cfg: dict, grid: dict, args: argparse.Namespace) -> dict[str, np.ndarray]:
    """v2-Flughafenbänder: Hauptflughafen-Areale + landebahn-orientierte
    Korridore. Fachlich unverändert aus
    abschichtung_common.py:build_airport_corridor_masks (Zeilen 1094-1153) -
    einzige Änderung: OSM-Lesen über ``_read_prep_layer()`` statt
    ``osm_layer_path()``+``read_layer()``.
    """
    zero = np.zeros(grid["shape"], dtype=bool)
    transport_bounds = expand_bounds(grid["bounds"], AIRPORT_CORRIDOR_LENGTH_M)
    transport = _read_prep_layer("transport", transport_bounds)
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente der Layer-Stufe ``osm``.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        ``config``, ``legacy_cover_dir`` (seit W6.1 wirkungslos), ``bbox``, ``mode``
        und ``total_height_m`` (beide mit heutigem ``INFRA_RULES`` wirkungslos),
        ``force_layers`` und ``skip_infra``.
    """
    p = argparse.ArgumentParser(
        description="OSM-Restlayer (Seilbahnen, sonstige Gebäude) + Infrastruktur/Flughäfen für "
        "die Widmungs-Abschichtung v2 - Layer-Stufe, liest aus derived/prep/osm/b_layers/."
    )
    p.add_argument("--config", default="config.json")
    p.add_argument(
        "--legacy-cover-dir", default="(seit W6.1 wirkungslos - kein echter Pfad, siehe --help)",
        help="Seit W6.1 wirkungslos: der Rueckfall auf das frühere "
        "output/abschichtung_widmung_v2/distance_layers ist entfernt, die "
        "HIG-Vorbedingungs-Checkpoints (OFFICIAL_COVER_LAYERS) kommen ausschliesslich "
        "aus derived/layers/ (Paket W2.1) - fehlender Checkpoint dort bricht laut ab. "
        "Parameter bleibt nur fuer die Fehlermeldung erhalten. W6.2: der Default trug "
        "bis hierher weiterhin output/abschichtung_widmung_v2/distance_layers, obwohl "
        "dieses Verzeichnis seit W6.1 nicht mehr im Repo existiert und der Wert nirgends "
        "mehr gelesen wird (Punkt aus dem W6.2-Auftrag: 'wirkungslos, aber sie luegen') - "
        "der neue Default behauptet keinen Pfad mehr, sondern benennt sich selbst als "
        "Platzhalter.",
    )
    p.add_argument("--bbox", default=None, help="EPSG:31287 bbox minx,miny,maxx,maxy für Smoke-Tests")
    p.add_argument(
        "--mode", choices=["standard", "minimum"], default="standard",
        help="Wird an build_infrastructure_masks() durchgereicht (angeschlossen), ist aber "
        "wirkungslos mit dem heutigen Inhalt von INFRA_RULES (abschichtung_common.py ~263-270): "
        "dort gilt für jede Regel standard == minimum. Unverändert aus dem Original übernommen.",
    )
    p.add_argument(
        "--total-height-m", type=float, default=250.0,
        help="Wird an build_infrastructure_masks() durchgereicht (angeschlossen), ist aber "
        "wirkungslos mit dem heutigen Inhalt von INFRA_RULES: keine Regel nutzt dort die "
        "Höhen-Platzhalter. Unverändert aus dem Original übernommen.",
    )
    p.add_argument("--force-layers", action="store_true")
    p.add_argument("--skip-infra", action="store_true", help="Infrastruktur-/Flughafenmasken überspringen")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Baut die Checkpoints der Layer-Stufe ``osm``.

    Prüft mit ``_require_hig_layers()``, dass die HiG-Checkpoints vorliegen, und
    baut dann über ``ensure_group_layers()`` die Gebäudeklassifikation samt
    angehängten Bändern 40/41 sowie, ohne ``--skip-infra``, die Infrastruktur-
    und Flughafenmasken. Eine Gruppe wird übersprungen, wenn ihre Checkpoints
    vorliegen und die Tags passen, außer mit ``--force-layers``.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.

    Raises
    ------
    FileNotFoundError
        Wenn HiG-Checkpoints unter ``derived/layers/`` fehlen.
    """
    args = parse_args(argv)
    with timed("load config/grid"):
        cfg = load_config(args.config)
        grid = load_grid(cfg, args.bbox)

    legacy_cover_dir = Path(args.legacy_cover_dir)
    _require_hig_layers(legacy_cover_dir)

    # PREP_FINGERPRINT-Tag statt separater Fingerabdruck-Datei (siehe
    # Moduldocstring "Fingerabdruck-Konvention") - dieselbe Konvention wie
    # pipeline/layers/hig.py/geo.py: EIN Tag für alle drei Checkpoint-Gruppen
    # dieser Stufe, weil dieselbe Prep-Ausgabe (b_layers/) alle drei speist.
    derived_tags = {"PREP_FINGERPRINT": _fingerprint_tag()}

    def _tags_ok(expected: dict):
        return lambda tags: all(tags.get(k) == v for k, v in expected.items())

    # Die Revision muss mit in die Checkpoint-Tags: sonst gelten vorhandene
    # derived/layers/*.tif weiter als gültig und eine geänderte Klassifikation
    # würde stillschweigend nicht wirksam. Unverändert aus dem Original,
    # jetzt zusätzlich zu PREP_FINGERPRINT statt an dessen Stelle.
    osm_tags = {
        **derived_tags,
        "HIG_SOURCE_FINGERPRINT": _cover_fingerprint(legacy_cover_dir),
        "BUILDING_CLASSIFICATION_REVISION": BUILDING_CLASSIFICATION_REVISION,
    }

    runtime.ensure_dir(contract.DERIVED_LAYERS)
    with timed("derived/update checkpoint layers"):
        ensure_group_layers(
            contract.DERIVED_LAYERS, [*OSM_LAYER_NAMES, *APPENDED_OSM_BAND_NAMES],
            "v2 OSM building classification + angehängte Bänder 40/41",
            lambda: build_osm_building_sources(cfg, grid, args), grid, args.force_layers,
            extra_ok=_tags_ok(osm_tags), extra_tags=osm_tags,
        )
        if not args.skip_infra:
            ensure_group_layers(
                contract.DERIVED_LAYERS, INFRA_LAYER_NAMES, "infrastructure masks",
                lambda: build_infrastructure_masks(cfg, grid, args), grid, args.force_layers,
                extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
            )
            ensure_group_layers(
                contract.DERIVED_LAYERS, AIRPORT_LAYER_NAMES, "airport corridor masks",
                lambda: build_airport_corridor_masks(cfg, grid, args), grid, args.force_layers,
                extra_ok=_tags_ok(derived_tags), extra_tags=derived_tags,
            )

    print(f"Checkpoint layers updated in {contract.DERIVED_LAYERS}.")


if __name__ == "__main__":
    main()
