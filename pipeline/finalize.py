"""Finalisierung: das 38-Band-GeoTIFF plus Manifest (Paket W3.1,
docs/rewrite/PLAN.md §7, §13.9).

Reiner Komponist. Diese Stufe baut KEINEN einzigen Layer selbst - alle 33
Checkpoints unter ``derived/layers/`` sind bereits von der Layer-Welle
geschrieben (W2.1 ``pipeline/layers/hig.py``, W2.3 ``pipeline/layers/osm.py``,
W2.4 ``pipeline/layers/geo.py`` - siehe ``make layers``). Was hier passiert,
ist genau der Teil von ``scripts/widmung_v2/04_create_distance_zones.py``
(seit W6.1 aus dem Repo entfernt; letzter Stand im Commit ``f1d00f7``,
abrufbar mit ``git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py``),
der NACH dessen sieben ``ensure_group_layers()``-Aufrufen kommt: die 26
Bedingungsbänder plus die beiden Referenzbänder einlesen, die Kategorie- und
Ergebnisaggregate bilden, die vier Unschärfebänder rechnen und alles
zusammen mit dem Manifest schreiben (``compose_exclusion_geotiff()`` +
``write_band_manifest()``, beide unverändert aus
``calc/abschichtung_common.py`` bzw.
``calc/band_manifest.py`` importiert - reine Rechenlogik, hier
nicht neu erfunden).

## Wo die Grenze zu Welle 2 verläuft

``pipeline/layers/geo.py`` endet, sobald der letzte der 17 dort gebauten
Checkpoints steht (siehe dessen Moduldocstring: "Diese Datei endet, sobald
die letzte der 17 Rastermasken als Checkpoint geschrieben ist"). Diese Datei
beginnt genau dort - sie ruft KEIN ``ensure_group_layers()`` auf, sondern
verlangt alle 33 Checkpoints als harte Vorbedingung (``_check_layers()``
unten) und bricht ab, wenn einer fehlt, statt ihn selbst zu bauen. Das ist
Regel aus PLAN.md §3: "Jede Stufe darf nur aus der vorigen lesen."

## Regel 4: Werte unverändert übernommen, nicht verbessert

``BANDS`` (die 26 Bedingungsbänder samt Beschreibung), ``HUMAN_BANDS``, die
Tag-Konstruktion und alle Puffer-/Schwellenwert-Konstanten sind wortgleich
aus ``scripts/widmung_v2/04_create_distance_zones.py`` übernommen (seit W6.1
aus dem Repo entfernt; letzter Stand im Commit ``f1d00f7``, abrufbar mit
``git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py``) - exakt
dasselbe Vorgehen wie in ``pipeline/layers/geo.py`` (dessen Moduldocstring:
"Regel 4: Werte/Namen unverändert") und aus demselben Grund: das alte
Skript war kein Paket (numerischer Dateiname, kein gültiger Modulpfad für
einen normalen ``import``). Die Altkette selbst (``make widmung-v2``) ist
seit W6.1 entfernt (docs/rewrite/FORTSCHRITT.md) - dieser Abschnitt
beschreibt weiterhin die Herkunft der Werte, nicht mehr eine parallel
lauffähige Kette.

## Bewusst NICHT übernommen (mit Begründung, wie schon bei W2.4)

- **Siedlungsabstands-Varianten** (``--settlement-variants``,
  ``SETTLEMENT_BUFFER_VARIANTS``, ``build_settlement_variant_buffers``):
  ``pipeline/layers/geo.py`` baut deren Quell-Checkpoints
  (``settlement_buffer_<variante>``) gar nicht erst - sie stehen nicht in
  ``pipeline.contract.LAYER_NAMES`` (33 Einträge) und sind im Clean-Schema
  per Default aus. Ohne Quell-Checkpoint keine Variantenbänder hier.
- **``--drop-human-band`` (Sensitivitätsläufe):** eine Lauf-Option des alten
  Skripts für Sonderauswertungen außerhalb der Standardkette, kein Teil der
  38-Band-Abschichtung selbst.
- **``--official-zoning-geojson``/``--vorrangzonen-dir``/``--osm-pbf``:** die
  amtlichen Windzonen und der WKA-Bestand sind bereits fertige Checkpoints
  aus W2.4 (``official_wind_zoning``, ``wka_bestand_ausserhalb_zonen``) -
  diese Stufe liest sie nur noch, baut sie nicht mehr aus Rohdaten.

## valid_area: kein Checkpoint, aber auch keine Rohdatei

``valid_area`` (die Staatsgebietsmaske, mit der ``compose_exclusion_geotiff()``
die Aggregat-/Ergebnisbänder verschneidet) steht nicht in
``pipeline.contract.LAYER_NAMES`` - sie war auch im alten Schema kein
persistierter Bandtyp, sondern wurde in ``main()`` jedes Mal frisch aus den
Verwaltungsgrenzen gebaut. Diese Datei importiert dafür
``pipeline.layers.geo._build_valid_area_mask()`` (liest
``derived/prep/admin/bundesland_masken.gpkg``, siehe dessen Modul) statt sie
ein drittes Mal zu duplizieren oder auf die VGD-Rohquelle
(``abschichtung_common.build_valid_area_mask()``) zurückzugreifen - Letzteres
wäre ein Sprung von Stufe 5 direkt auf Stufe 1 und würde §3 verletzen ("nie
zwei Stufen überspringen").

## §13.9 / Regel 8: die erste erklärte Abweichung

``geography_water_bodies`` weicht bekanntermaßen in 543.106 von 336.038.001
Zellen ab, ausschließlich zusätzlich (PLAN.md §13.9) - die neue Prep-Stufe
filtert gegen die ungeklippte OSM-Rohquelle und findet eine
grenzüberschreitende Bodensee-Relation, die der alte ``osmium extract
--bbox``-Weg verliert. Diese Datei ändert daran nichts (Regel 8: "wird
weitergetragen, nicht weggemacht") - sie liest den Checkpoint, den
``pipeline/layers/geo.py`` geschrieben hat, unverändert. Die Ausbreitung
dieser einen Abweichung auf die übrigen 37 Bänder ist Gegenstand des
Berichts zu diesem Paket, nicht dieser Datei.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import contract, runtime  # noqa: E402
from pipeline.layers.geo import _build_valid_area_mask  # noqa: E402

from calc.config import load_config  # noqa: E402
from calc.abschichtung_common import (  # noqa: E402
    AIRPORT_CORRIDOR_HALF_ANGLE_DEG,
    AIRPORT_CORRIDOR_LENGTH_M,
    CABLEWAY_BUILDING_BUFFER_M,
    GENERAL_BUILDING_BUFFER_M,
    GEOGRAPHY_BANDS,
    HIG_CHAIN_M,
    HIG_FAMILY_BUFFER_M,
    HIG_MIN_ADRESSEN,
    NATURE_BANDS,
    NONRESIDENTIAL_HULL_BUFFER_M,
    OFFICIAL_ZONING_BANDS,
    SETTLEMENT_BUFFER_BY_BL,
    UNCERTAINTY_BLUR_SIGMAS_M,
    WATER_BANDS,
    WATER_MIN_AREA_HA,
    compose_exclusion_geotiff,
    layer_path,
    load_grid,
    timed,
)
from calc.band_manifest import write_band_manifest  # noqa: E402

PIPELINE_TAG = "widmung_v2"
# Wortgleich aus scripts/widmung_v2/04_create_distance_zones.py (dort Zeile
# ~130; seit W6.1 aus dem Repo entfernt, letzter Stand im Commit f1d00f7 -
# git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py) und
# pipeline/layers/geo.py (dort Zeile ~176) übernommen - derselbe Tag-Wert
# muss über alle drei Stellen hinweg gleich bleiben, sonst würde
# layer_done() (Fingerabdruck-Vergleich) Checkpoints fälschlich als veraltet
# ansehen.
BAND_SCHEMA = "clean-44-ohne-wichtige-objekte-aug-2026"

WKA_BESTAND_BAND = "wka_bestand_ausserhalb_zonen"
WKA_CLUSTER_CHAIN_M = 750.0
WKA_HULL_MARGIN_M = 200.0

# W7.1 (Neuzuschnitt, schnittstelle-manifest-2.2.md §2): Bänder 39-44,
# angehängt hinter den beiden bisherigen trailing_bands (37/38). NICHT Teil
# von BANDS unten (den 26 condition_bands) - BANDS wird VOR den Kategorie-/
# Ergebnisaggregaten (Index 27-32) geschrieben, ein Eintrag dort würde die
# Indizes 27-38 verschieben. Diese sechs sind reine Checkpoint-Durchreichen
# wie official_wind_zoning/wka_bestand_ausserhalb_zonen selbst (siehe deren
# trailing_bands-Verwendung unten) - sie kommen deshalb an dieselbe Stelle,
# nur danach. Reihenfolge = contract.LAYER_NAMES[-6:], siehe dort.
APPENDED_BAND_NAMES = [
    "haeuser_im_gruenen_source",
    "general_buildings_roh_osm",
    "general_buildings_roh_dkm",
    "sources_human",
    "sources_nature",
    "sources_geography",
]


@dataclass(frozen=True)
class Band:
    name: str
    description: str


# Wortgleich aus scripts/widmung_v2/04_create_distance_zones.py:BANDS (dort
# Zeile ~147-174; seit W6.1 aus dem Repo entfernt, letzter Stand im Commit
# f1d00f7 - git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py)
# übernommen - die 26 Bedingungsbänder in exakt der Reihenfolge, in der
# compose_exclusion_geotiff() sie schreibt.
# W7.6 (09.09.2026, docs/LAYER-MANIFEST.md §4a/§5, MANIFEST-TEXTE.md §1):
# alle 26 Beschreibungen final ausformuliert (Spalte "neu"), gegen den Code
# geprüft statt aus der Vorlage übernommen (siehe Bericht zu W7.6) - insb.
# Band 17 gegen PEOPLE_CARRYING_AERIALWAY_TYPES (W7.5, heute) verifiziert,
# nicht die zurückgenommene Fassung eingesetzt. Bänder 14-16: Wortlaut sagt
# die tatsächliche Wirkung ("als Tunnel ausgewiesene Abschnitte bleiben
# unberücksichtigt"), nicht eine Absicht, die der Code nicht einlöst (siehe
# calc.abschichtung_common._non_tunnel_mask() - liest ausschließlich die
# Spalte "tunnel", nie "layer"/"covered"; caveats-Eintrag
# "tunnelfilter_unvollstaendig" in calc/band_manifest.py).
BANDS = [
    Band("official_settlement_source", "Amtlich gewidmetes Wohn-, Misch-, Kern- und Dorfgebiet aller neun Bundesländer, ohne Abstand."),
    Band("settlement_buffer", "Abstand von 1.000 m um amtliches Wohn-, Misch-, Kern- und Dorfgebiet aller neun Bundesländer; in Niederösterreich 1.200 m."),
    Band("haeuser_im_gruenen_ferienhaus", "Ferienhaus- und Tourismusgebiete aus den amtlichen Widmungen des Burgenlands und Tirols, ohne Abstand."),
    Band("haeuser_im_gruenen_widmung", "Amtliche Widmungen für Hofstellen, Camping, Golf, Kleingärten und Auffüllungsgebiete, ohne Abstand. Ohne Niederösterreich, dort tragen die Zonen des Sektoralen Raumordnungsprogramms den Abstand bereits."),
    Band("haeuser_im_gruenen_streusiedlung", "Hüllen bewohnter Streusiedlungen, gebildet aus mindestens fünf adressierten Objekten mit höchstens 200 m Abstand zueinander, ohne Abstand nach außen. Ohne Niederösterreich."),
    Band("haeuser_im_gruenen_noe_pdf", "Mindestabstandszonen des niederösterreichischen Sektoralen Raumordnungsprogramms um Gebäude, Adressen und Grünland-Widmungen. Sie enthalten den Abstand von 750 m bereits und werden deshalb nicht erneut gepuffert."),
    Band("haeuser_im_gruenen", "Abstand von 750 m um bewohnte Einzellagen außerhalb des Baulands: Ferienhaus- und Tourismusgebiete, amtliche Widmungen für Hofstellen, Camping, Golf, Kleingärten und Auffüllungsgebiete sowie Streusiedlungen. In Niederösterreich gelten stattdessen die Mindestabstandszonen des Sektoralen Raumordnungsprogramms, die den Abstand bereits enthalten."),
    Band("nonresidential_hulls_source", "Unbewohnte und industrieartige Kataster-Hüllen, ohne Abstand."),
    Band("nonresidential_hulls_buffer", "Abstand von 25 m um unbewohnte und industrieartige Kataster-Hüllen; das entspricht praktisch dem Fußabdruck."),
    Band("cableway_buildings_source", "Gebäude aus OpenStreetMap in unmittelbarer Nähe einer Seilbahnlinie, vor allem Liftstationen, ohne Abstand."),
    Band("cableway_buildings_buffer", "Abstand von 50 m um Gebäude an Seilbahnlinien."),
    Band("general_buildings_source", "Übrige Gebäude aus OpenStreetMap wie Garagen, Schuppen, Ställe, Industriebauten und untypisierte Gebäude, dazu bewohnte Einzellagen und niederösterreichische Streusiedlungs-Bauflächen aus Kataster und Gebäuderegister, ohne Abstand."),
    Band("general_buildings_buffer", "Abstand von 25 m um sonstige Gebäude und Einzellagen; das entspricht praktisch dem Fußabdruck."),
    Band("road_motorway_trunk", "Abstand von 150 m beiderseits von Autobahnen und Schnellstraßen aus OpenStreetMap. Als Tunnel ausgewiesene Abschnitte bleiben unberücksichtigt."),
    Band("road_federal_state", "Abstand von 150 m beiderseits von Bundes- und Landesstraßen aus OpenStreetMap, einschließlich der nachgeordneten Landesstraßen. Als Tunnel ausgewiesene Abschnitte bleiben unberücksichtigt."),
    Band("rail_main", "Abstand von 150 m beiderseits von Haupt- und Schmalspurbahnen aus OpenStreetMap. Als Tunnel ausgewiesene Abschnitte bleiben unberücksichtigt."),
    Band("cableway_people_150m", "Abstand von 150 m um Personenseilbahnen aus OpenStreetMap: Gondelbahnen, Kabinen- und Pendelbahnen, Sessellifte und Kombibahnen. Schlepplifte und Materialseilbahnen erzeugen keine Zone."),
    Band("military_restricted_area", "Militärische Sperrgebiete aus OpenStreetMap, ohne zusätzlichen Abstand."),
    Band("airport_area_major", "Areale der Hauptflughäfen aus OpenStreetMap, ohne zusätzlichen Abstand."),
    Band("airport_runway_corridor_5km", "Korridore ab beiden Landebahn-Enden der Hauptflughäfen: 5 km lang, ±15 Grad um die verlängerte Bahnachse."),
    Band("nature_protection_areas", "Nationalparks, Naturschutzgebiete, Europaschutzgebiete nach Natura 2000 und Ramsar-Gebiete aus den amtlichen Datensätzen, ohne Abstand."),
    Band("osm_nature_protection_areas", "Als Schutzgebiet ausgewiesene Flächen aus OpenStreetMap, ohne Abstand."),
    Band("geography_slope_too_steep", "Hangneigung über 15 Grad, ermittelt aus dem Geländemodell mit 25 m Auflösung."),
    Band("geography_elevation_too_high", "Seehöhe über 2.500 m, ermittelt aus dem Geländemodell mit 25 m Auflösung."),
    Band("geography_wind_too_low", "Windleistungsdichte in 150 m Höhe unter rund 160 W/m², aus dem Globalen Windatlas. Der Grenzwert entspricht 150 W/m² in 130 m Höhe, mit dem Windprofil auf 150 m hochgerechnet."),
    Band("geography_water_bodies", "Seen, Stauseen und Flüsse aus OpenStreetMap, zusammenhängende Wasserflächen ab 1 ha."),
]

# Wortgleich aus scripts/widmung_v2/04_create_distance_zones.py:HUMAN_BANDS
# (seit W6.1 aus dem Repo entfernt, letzter Stand im Commit f1d00f7 - git
# show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py).
HUMAN_BANDS = [
    "settlement_buffer",
    "haeuser_im_gruenen",
    "nonresidential_hulls_buffer",
    "cableway_buildings_buffer",
    "general_buildings_buffer",
    "road_motorway_trunk",
    "road_federal_state",
    "rail_main",
    "cableway_people_150m",
    "military_restricted_area",
    "airport_area_major",
    "airport_runway_corridor_5km",
]

# Alle 33 Checkpoints, die diese Stufe als harte Vorbedingung verlangt -
# identisch mit pipeline.contract.LAYER_NAMES (dort vorab erklärt); hier noch
# einmal als flache Liste, weil _check_layers() nur Namen braucht, keine
# Pfade (die liefert layer_path() je nach --layer-dir).
REQUIRED_LAYERS = list(contract.LAYER_NAMES)


def _check_layers(layer_dir: Path) -> None:
    """Harte Vorbedingung statt stillem Fallback (PLAN.md §3, Stufe 5).

    Anders als das alte Skript (das acht fehlende Checkpoints selbst baut,
    siehe _check_required_sources() in 04_create_distance_zones.py - seit
    W6.1 aus dem Repo entfernt, letzter Stand im Commit f1d00f7: git show
    f1d00f7:scripts/widmung_v2/04_create_distance_zones.py) prüft diese
    Stufe ALLE 33 Checkpoints und baut keinen einzigen davon - Bauen ist
    Aufgabe der Layer-Welle (W2.1/W2.3/W2.4), nicht dieser Stufe.
    """
    missing = [name for name in REQUIRED_LAYERS if not layer_path(layer_dir, name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Fehlende Checkpoint-Layer in {layer_dir}: {', '.join(missing)}. "
            "Diese Stufe baut keine Layer selbst - 'make layers' zuerst laufen lassen "
            "(pipeline/layers/hig.py, osm.py, geo.py)."
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente der Finalisierungsstufe.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        ``config``, ``layer_dir`` (Vorgabe ``contract.DERIVED_LAYERS``),
        ``output`` (Vorgabe ``contract.PRODUCTS["abschichtung_tif"]``),
        ``min_fragment_area_ha`` (Vorgabe 10.0) und ``no_overviews``.
    """
    p = argparse.ArgumentParser(
        description=(
            "Finalisierung W3.1: komponiert das 38-Band-GeoTIFF plus Manifest "
            "aus den Checkpoint-Layern der neuen Kette (derived/layers/, siehe "
            "'make layers'). Baut selbst keine Layer."
        )
    )
    p.add_argument("--config", default="config.json")
    p.add_argument(
        "--layer-dir",
        default=None,
        help="Quelle der 33 Checkpoint-Layer. Default: pipeline.contract.DERIVED_LAYERS (derived/layers/).",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Ziel-GeoTIFF. Default: pipeline.contract.PRODUCTS['abschichtung_tif'] (out/abschichtung.tif).",
    )
    # Kein --bbox (anders als beim alten Skript und bei pipeline/layers/*.py):
    # die 33 Checkpoints unter --layer-dir sind bereits auf dem vollen
    # Österreich-Gitter geschrieben (derived/layers/, siehe make layers). Ein
    # hier verkleinertes Gitter würde beim ersten read_layer_mask() sofort an
    # der Shape scheitern (getestet: (2000,2000) vs. (14001,24001)) - ein
    # bbox-Smoke-Test müsste auch die Checkpoints selbst neu (verkleinert)
    # bauen, und genau das ist nicht Aufgabe dieser Stufe (siehe
    # _check_layers()). Wer diese Stufe klein testen will, braucht ein
    # eigenes --layer-dir mit bbox-verkleinerten Checkpoints.
    p.add_argument("--min-fragment-area-ha", type=float, default=10.0)
    p.add_argument("--no-overviews", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Komponiert das GeoTIFF samt Band-Manifest aus den Checkpoint-Layern.

    Lädt Konfiguration und Raster, prüft mit ``_check_layers()``, dass alle
    Checkpoints vorliegen, baut die Maske der gültigen Fläche, schreibt das
    GeoTIFF über ``compose_exclusion_geotiff()`` und direkt danach das Sidecar
    über ``write_band_manifest()`` aus derselben Bandliste und denselben
    Datei-Tags. Baut selbst keine Layer.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.

    Raises
    ------
    FileNotFoundError
        Wenn Checkpoint-Layer unter dem Layer-Verzeichnis fehlen.
    """
    args = parse_args(argv)
    layer_dir = Path(args.layer_dir) if args.layer_dir else contract.DERIVED_LAYERS
    output = Path(args.output) if args.output else contract.PRODUCTS["abschichtung_tif"]

    with timed("load config/grid"):
        cfg = load_config(args.config)
        grid = load_grid(cfg, None)

    _check_layers(layer_dir)

    with timed("build valid area mask (derived/prep/admin)"):
        valid_area = _build_valid_area_mask(grid)

    # Wortgleich aus scripts/widmung_v2/04_create_distance_zones.py:main()
    # (dort Zeile ~472-499; seit W6.1 aus dem Repo entfernt, letzter Stand im
    # Commit f1d00f7 - git show f1d00f7:scripts/widmung_v2/04_create_distance_zones.py)
    # übernommen, abzüglich der Varianten-/Drop-Tags (siehe Moduldocstring:
    # bewusst nicht übernommen -> DROPPED_HUMAN_BANDS bleibt "keine",
    # SETTLEMENT_BUFFER_VARIANTS bleibt "{}").
    tags = {
        "MIN_FRAGMENT_AREA_HA": str(args.min_fragment_area_ha),
        "PIPELINE": PIPELINE_TAG,
        "BAND_SCHEMA": BAND_SCHEMA,
        "SETTLEMENT_BUFFER_BY_BL": json.dumps(SETTLEMENT_BUFFER_BY_BL, ensure_ascii=False, sort_keys=True),
        "HIG_FAMILY_BUFFER_M": str(HIG_FAMILY_BUFFER_M),
        "NONRESIDENTIAL_HULL_BUFFER_M": str(NONRESIDENTIAL_HULL_BUFFER_M),
        "HIG_CHAIN_M": str(HIG_CHAIN_M),
        "HIG_MIN_ADRESSEN": str(HIG_MIN_ADRESSEN),
        "DROPPED_HUMAN_BANDS": "keine",
        "CABLEWAY_BUILDING_BUFFER_M": str(CABLEWAY_BUILDING_BUFFER_M),
        "GENERAL_BUILDING_BUFFER_M": str(GENERAL_BUILDING_BUFFER_M),
        "HIG_SOURCE": "amtliche Widmung + NÖ-SekROP-PDF + DKM-Hüllen + BEV-Adressregister (build_hig_sources.py); NÖ-Streusiedlung durch PDF ersetzt",
        "WICHTIGE_OBJEKTE": "in haeuser_im_gruenen (750 m); vorher eigenes 250-m-Band",
        "BEWOHNTE_EINZELLAGEN": "in general_buildings_source (25 m); vorher eigenes 25-m-Bandpaar",
        "POWER_LINES": "kein Ausschlusskriterium (Clean-Schema Aug 2026)",
        "AIRPORT_CORRIDOR_LENGTH_M": str(AIRPORT_CORRIDOR_LENGTH_M),
        "AIRPORT_CORRIDOR_HALF_ANGLE_DEG": str(AIRPORT_CORRIDOR_HALF_ANGLE_DEG),
        "WIEN": "amtliche Widmung Stadt Wien (GENFLWIDMUNGOGD), kein Vollausschluss mehr",
        "WATER_MIN_AREA_HA": str(WATER_MIN_AREA_HA),
        "UNCERTAINTY_BLUR_SIGMAS_M": ",".join(f"{s:g}" for s in UNCERTAINTY_BLUR_SIGMAS_M),
        "SETTLEMENT_BUFFER_VARIANTS": "{}",
        "SETTLEMENT_BUFFER_VARIANT_NAMES": "",
        "UNCERTAINTY_BLUR_SOURCE": "available_after_all_exclusions_raw",
        "WKA_CLUSTER_CHAIN_M": str(WKA_CLUSTER_CHAIN_M),
        "WKA_HULL_MARGIN_M": str(WKA_HULL_MARGIN_M),
        "DISTANCE_ENGINE": "fft",
        # W7.6 (docs/LAYER-MANIFEST.md §7, MANIFEST-TEXTE.md §6): die drei
        # Geografie-Schwellenwerte standen bisher nur im Quelltext (config.json
        # "exclusion"/"wind"), null Treffer in parameters. Gelesen aus cfg,
        # nicht als Literal hier hingeschrieben - dieselbe Quelle, aus der
        # calc/abschichtung_common.py:build_geography_masks() tatsächlich
        # rechnet (dortige Fallbacks 20.0/2500.0/180.0 greifen nur, wenn cfg
        # die Schlüssel nicht trägt; config.json trägt sie, siehe dort).
        "SLOPE_MAX_DEG": str(float(cfg["exclusion"]["slope_max_deg"])),
        "ELEVATION_MAX_M": str(float(cfg["exclusion"]["elevation_max"])),
        "PD_MIN_W_M2": str(float(cfg["wind"]["pd_min"])),
        "PD_MIN_REFERENCE_HEIGHT_M": str(float(cfg["wind"]["pd_min_height_m"])),
        # Kein zweiter Schwellenwert, sondern derselbe, auf die tatsächliche
        # Vergleichshöhe (150 m) umgerechnet - cfg["_derived"]["power_density_min_150"]
        # aus calc/config.py:load_config() (pd_min * (150/pd_min_height_m)**(3*shear_alpha)),
        # dieselbe Formel, mit der Band 25 (geography_wind_too_low) tatsächlich
        # vergleicht (calc/abschichtung_common.py: wind_min = derived["power_density_min_150"]).
        "PD_MIN_AT_150M_W_M2": f"{cfg['_derived']['power_density_min_150']:.4f}",
    }

    with timed(f"compose final GeoTIFF {output}"):
        runtime.ensure_dir(output.parent)
        band_names = compose_exclusion_geotiff(
            path=output,
            grid=grid,
            layer_dir=layer_dir,
            condition_bands=[b.name for b in BANDS],
            human_bands=HUMAN_BANDS,
            nature_band_names=NATURE_BANDS,
            geography_band_names=[*GEOGRAPHY_BANDS, *WATER_BANDS],
            valid_area=valid_area,
            min_fragment_area_ha=args.min_fragment_area_ha,
            trailing_bands=[*OFFICIAL_ZONING_BANDS, WKA_BESTAND_BAND, *APPENDED_BAND_NAMES],
            variant_source_band=None,
            variants={},
            tags=tags,
            build_overviews=not args.no_overviews,
            blur_sigmas_m=UNCERTAINTY_BLUR_SIGMAS_M,
            blur_source="raw",
        )

    # Sidecar direkt aus dem, was gerade geschrieben wurde - wie im alten
    # Skript: band_names ist die tatsächliche Bandreihenfolge, tags sind die
    # Datei-Tags. Kein zweites Öffnen des Rasters, keine zweite Wahrheit.
    manifest_path = write_band_manifest(
        output, band_names, tags, grid, {b.name: b.description for b in BANDS}
    )

    print(
        f"Wrote {output} from checkpoint layers in {layer_dir} "
        f"({len(BANDS)} condition bands + 3 category aggregates + all/raw/cleaned + "
        f"{len(UNCERTAINTY_BLUR_SIGMAS_M)} uncertainty blur bands + "
        f"official_zoning + wka_bestand + {len(APPENDED_BAND_NAMES)} angehängte Bänder "
        f"(W7.1) = {len(band_names)} bands), shape={grid['shape']}"
    )
    print(f"Wrote {manifest_path} ({len(band_names)} bands, schema {tags['BAND_SCHEMA']})")


if __name__ == "__main__":
    main()
