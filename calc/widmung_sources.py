"""Declarative registry of the official per-Bundesland Flächenwidmung sources.

One entry per underlying OGD file in ``DATASETS`` (path/layer/columns/CRS quirks),
one entry per semantic selection in ``SOURCES`` (which category values of which
dataset belong to which bucket). Both
``scripts/main/build_official_zoning_layers.py`` (which materializes the buckets)
and ``scripts/analysis/audit_bauland_kategorien.py`` (which reviews the
assignment) read from here, so a category can never be assigned in one and
unknown to the other.

Buckets
-------
``wohn_misch``        gewidmetes Wohnbauland + Mischnutzung -> full settlement
                      setback (1.000 m, NÖ 1.200 m; see SETTLEMENT_BUFFER_BY_BL
                      in abschichtung_common.py)
``haeuser_im_gruenen`` Hofstellen/Camping/Golf/Kleingarten and - as category
                      ``ferienhaus_tourismus`` - Ferienhaus-/Tourismusgebiete
                      -> flat 750 m
``industrie_negativ``  Betriebs-/Industriegebiete. NOT an exclusion of its own:
                      purely a negative mask that stops industrial building
                      clusters from being classified as "bewohnt" (750 m) by
                      scripts/main/build_hig_sources.py.

Matching
--------
Freitext-Widmungsfelder (T/V/ST) must never be filtered with ``isin`` - Vorarlberg
alone has 1.424 suffix variants. Hence the three predicate kinds ``values`` /
``startswith`` / ``contains``; a source's ``match`` list is ANDed.
"""
from __future__ import annotations

import functools
from pathlib import Path

import geopandas as gpd
import pandas as pd

# W6.2: parents[1], nicht mehr parents[2] - diese Datei lag bis W6.2 unter
# windkraft/calc/ (zwei Ebenen unter der Repo-Wurzel), seit der inneren
# Ebene aufgeloest liegt sie unter calc/ (nur noch eine Ebene). Ohne diese
# Korrektur zeigt ROOT eine Ebene zu hoch, ausserhalb des Repos.
ROOT = Path(__file__).resolve().parents[1]
WIDMUNG = ROOT / "data" / "widmung"

WORK_CRS = "EPSG:31287"

# EPSG:31287 plausible bounding box for Austria (sanity check, metres) - the
# Stmk OGD sources contain features shifted ~10° west (Gemeinde 61106).
AT_BBOX = (100_000, 250_000, 700_000, 600_000)

BUCKETS = ("wohn_misch", "haeuser_im_gruenen", "industrie_negativ")

# Kärnten's GeoPackage is extracted out of its ZIP once - see _ensure_ktn_gpkg().
KTN_GPKG_CACHE_NAME = "flawi_ktn_gpkg.gpkg"


# ---------------------------------------------------------------------------
# Datasets: one entry per underlying file
# ---------------------------------------------------------------------------

DATASETS = {
    "bgld": {
        "bundesland": "Burgenland",
        "source": "data/widmung/burgenland/WIDMUNGSFLAECHEN.zip, Layer BGLD_FLAECHENWIDMUNG",
        "code_col": "WIDCODE",
        "label_col": "BEZEICH",
    },
    "ktn": {
        "bundesland": "Kärnten",
        "source": "data/widmung/kaernten/flawi_ktn_gpkg.zip, Layer WIDG",
        "code_col": "WIDMUNG",
        "label_col": "KATEGORIE",
        "extra_cols": ["WIDCODE"],
    },
    "noe": {
        "bundesland": "Niederösterreich",
        "source": "data/widmung/niederoesterreich/RRU_WI_HUELLE.gpkg",
        "code_col": "WI_ART",
        "label_col": "WI_ART_WERTE",
    },
    "ooe": {
        "bundesland": "Oberösterreich",
        "source": "data/widmung/oberoesterreich/FLWI_WIDMUNGEN_F.zip",
        "code_col": "KENNZAHL",
        "label_col": None,
    },
    "sbg": {
        "bundesland": "Salzburg",
        "source": "data/widmung/salzburg/Flaechenwidmung_Shapefile.zip",
        "code_col": "Typname",
        "label_col": None,
    },
    "stmk_bauland": {
        "bundesland": "Steiermark",
        "source": "data/widmung/steiermark/Bauland.zip",
        "code_col": "KATEGO",
        "label_col": "GRUPPE_4",
    },
    "stmk_flaewi": {
        "bundesland": "Steiermark",
        "source": "data/widmung/steiermark/Flaewi.shp.zip, Layer FWP_NUTZ",
        "code_col": "WIDMUNG",
        "label_col": None,
    },
    "tir": {
        "bundesland": "Tirol",
        "source": "data/widmung/tirol/FLW_Flaechenwidmung_*.gpkg",
        "code_col": "WIDMUNG",
        "label_col": None,
        "extra_cols": ["FESTLEGUNG"],
    },
    "vbg": {
        "bundesland": "Vorarlberg",
        "source": "data/widmung/vorarlberg/fwp_flaeche.gpkg",
        "code_col": "wi_em_txt",
        "label_col": None,
    },
    # Wien liefert im offenen WFS nur die GENERALISIERTE Flächenwidmung - der
    # parzellenscharfe Flächenwidmungs- und Bebauungsplan ist dort nicht
    # enthalten. Für ein 25-m-Raster mit 1.000-m-Puffern ist das unerheblich:
    # 26.419 Flächen mit eigener Widmungsklasse je Fläche, 352,9 km² von Wiens
    # 414,8 km² (der Rest ist Straßenraum ohne Widmung).
    "wien": {
        "bundesland": "Wien",
        "source": "data/widmung/wien/genflwidmung_wien.geojson (Stadt Wien OGD, WFS ogdwien:GENFLWIDMUNGOGD)",
        "code_col": "WIDMUNGSKLASSE_TXT",
        "label_col": "WIDMUNG_TXT",
    },
}


# ---------------------------------------------------------------------------
# Category code sets (numeric/enumerated fields only - never Freitext)
# ---------------------------------------------------------------------------

# Burgenland WIDCODE, verified against BEZEICH (see audit_bauland_kategorien.py).
# Neben Wohn-/Dorf-/Geschäfts-/Gemischtem Baugebiet auch die zugehörigen
# Aufschließungsgebiete (10011/10012/10016) und den förderbaren Wohnbau
# (10009/10019) - 10011 war schon im Plan, die anderen sind dieselbe Kategorie
# in anderer Ausprägung und wären sonst als einzige ohne Siedlungsabstand.
BGLD_WOHN_CODES = {10001, 10002, 10003, 10006, 10009, 10011, 10012, 10016, 10019}
# 10030 "Baugebiete für Erholungs- oder Tourismuseinrichtungen" (753 Flächen,
# 5,8 km²) plus 10007, its older-spelling twin "Erholungs- oder
# Fremdenverkehrseinrichtungen" (149 Flächen, 1,8 km²), plus 10017, the
# Aufschließungsgebiet variant - same category, three code generations. Without
# them those Seesiedlungen keep the full 1.200-m-Siedlungsabstand instead of 750 m.
BGLD_FERIENHAUS_CODES = {10030, 10007, 10017}
BGLD_INDUSTRIE_CODES = {10004, 10005, 10015}

OOE_WOHN_CODES = {11005, 11006, 11007, 11101, 11102, 11103, 11104, 11105, 11106, 11201, 11202, 11203}
OOE_MISCH_CODES = {11001, 11002, 11009, 11010}
OOE_HOFSTELLE_CODES = {13010, 13020}
OOE_CAMPING_CODES = {13105}
OOE_GOLF_CODES = {13107}
OOE_KLEINGARTEN_CODES = {13201}
OOE_INDUSTRIE_CODES = {11003, 11011}

SBG_WOHN_CODES = {"BAEW", "BADG", "BARW", "BAKG", "BALK", "BAZG", "BAFW"}
SBG_INDUSTRIE_CODES = {"BAGG", "BAIG", "BABG"}

KTN_WOHN_VALUES = {"Wohngebiet", "Reines Wohngebiet"}
# Dorfgebiet/Gemischtes Baugebiet/Kurgebiet sind Mischnutzung, keine reinen
# Gewerbe-/Industriegebiete - analog zu OÖ/Stmk, die Wohn und Misch ebenfalls trennen.
# Geschäftsgebiet zählt hier mit, weil Burgenlands Pendant (10003) ebenfalls im
# Wohn-Bucket steckt; Kurgebiet bleibt bewusst voller Siedlungsabstand (die
# Verschiebung nach ferienhaus_tourismus wäre eine Reduktion und braucht eine
# ausdrückliche Entscheidung - siehe Audit-Tabelle).
KTN_MISCH_VALUES = {"Dorfgebiet", "Gemischtes Baugebiet", "Geschäftsgebiet", "Kurgebiet", "Reines Kurgebiet"}
# == KURZBEZEICHNUNG H/H-Z/H-A; verifiziert 1:1 deckungsgleich (10.585/401/9 Flächen je Seite).
KTN_HOFSTELLE_WIDCODES = {"B2", "B21", "B22"}
KTN_INDUSTRIE_VALUES = {"Industriegebiet", "Gewerbegebiet", "Reines Gewerbegebiet", "Betriebsgebiet"}

TIR_WOHN_PREFIXES = ("Wohngebiet § 38", "Gemischtes Wohngebiet § 38")
# § 40 (3) Kerngebiet is Wohnbauland-äquivalent (Ortszentren). The prefix also
# catches the "... mit beschränkter Wohnnutzung § 40 (6)" variant.
TIR_KERNGEBIET_PREFIXES = ("Kerngebiet § 40 (3)",)
# § 40 (2)/(5) Mischgebiete sind Dorf-/Mischnutzung im Sinne von Plan §3 Zeile 1
# ("Wohn/Dorf/Kern/Gemischt") und mit zusammen ~44 km² Tirols größte Lücke.
TIR_MISCH_PREFIXES = ("Allgemeines Mischgebiet", "Landwirtschaftliches Mischgebiet")
# § 40 (4) Tourismusgebiet, likewise including the § 40 (6) variant.
TIR_TOURISMUS_PREFIXES = ("Tourismusgebiet § 40 (4)",)
TIR_INDUSTRIE_PREFIXES = ("Gewerbe- u. Industriegebiet",)
TIR_LEISURE_WIDMUNG = [
    "Sonderfläche standortgebunden § 43 (1) a",
    "Sonderfläche Sportanlage § 44 (1)",
    "Sonderfläche Sportanlage § 50, Festlegung der Art der Sportanlage",
    "Sonderfläche UVP-pflichtige Anlage § 49a",
]

# Vorarlbergs wi_em_txt ist NICHT einheitlich getrennt: "Bauerwartungsfläche"
# steht vor Wohn-/Mischgebiet mit ZWEI Leerzeichen, vor Betriebsgebiet mit einem.
# Ein startswith("Bauerwartungsfläche Wohngebiet") traf deshalb nie ein einziges
# Feature (3,4 km² stille Lücke) - daher hier Regex mit \s+ statt Präfixliste.
# Mischgebiet (32,7 km²) und Kerngebiet (4,4 km²) sind Wohn-/Mischnutzung im
# Sinne von Plan §3 Zeile 1 und waren bisher gar nicht erfasst.
VBG_WOHN_PATTERN = r"^Bau(?:erwartungs)?fläche\s+(?:Wohngebiet|Mischgebiet|Kerngebiet)"
VBG_INDUSTRIE_PATTERN = r"^Bau(?:erwartungs)?fläche\s+Betriebsgebiet"

# Wien, WIDMUNGSKLASSE_TXT (19 Klassen). "Wohngebiet*" deckt auch
# -Geschäftsviertel und die Förderungs-Varianten ab (zusammen 86,5 km²).
# Beim gemischten Baugebiet (25,0 km²) muss das Betriebsbaugebiet ausgenommen
# werden: es erlaubt Wohnen nur für Aufsichtspersonal und gehört wie in den
# anderen Bundesländern in die Industrie-Negativmaske, nicht in den vollen
# Siedlungsabstand - daher Negative-Lookahead statt Präfixliste.
WIEN_WOHN_PREFIXES = ("Wohngebiet",)
WIEN_MISCH_PATTERN = r"^Gemischtes Baugebiet(?!-Betriebsbaugebiet)"
WIEN_INDUSTRIE_PATTERN = r"^(?:Industriegebiet|Gemischtes Baugebiet-Betriebsbaugebiet)"
# Nicht enthalten und bewusst so: "Ländliches Gebiet" (14,3 km²) ist Wiens
# Grünland-Landwirtschaftsklasse, keine Bauland-Kategorie - einzelne Hofstellen
# darin findet die DKM/BEV-Hüllenerkennung. Ebenso außen vor: Schutzgebiet,
# Erholungsgebiet (außer Kleingarten), Sondergebiet, Friedhof (die kommen als
# OSM-Einzelobjekte mit 250 m), Verkehrsband.

# Steiermark Flaewi.shp.zip / FWP_NUTZ.WIDMUNG prefixes, verified disjoint code
# families (afg 1.229 / klg 693 / Ca 4 Flächen) - all three included together.
STMK_LEISURE_PREFIXES = {
    "auffuellungsgebiet": ("afg", "AF"),
    "kleingarten": ("klg", "Klg"),
    "camping": ("Ca", "L(SF-Ca)"),
}


# ---------------------------------------------------------------------------
# Sources: one entry per (dataset, semantic category) selection
# ---------------------------------------------------------------------------

def _v(col: str, values) -> dict:
    """Build a ``values`` predicate (exact match against a set).

    Parameters
    ----------
    col : str
        Column to test.
    values : iterable
        Accepted values.

    Returns
    -------
    dict
        ``{"col": col, "values": set(values)}``.
    """
    return {"col": col, "values": set(values)}


def _sw(col: str, prefixes) -> dict:
    """Build a ``startswith`` predicate.

    Parameters
    ----------
    col : str
        Column to test.
    prefixes : iterable of str
        Accepted prefixes.

    Returns
    -------
    dict
        ``{"col": col, "startswith": tuple(prefixes)}``.
    """
    return {"col": col, "startswith": tuple(prefixes)}


def _c(col: str, pattern: str) -> dict:
    """Build a ``contains`` predicate (regular expression search).

    Parameters
    ----------
    col : str
        Column to test.
    pattern : str
        Regular expression.

    Returns
    -------
    dict
        ``{"col": col, "contains": pattern}``.
    """
    return {"col": col, "contains": pattern}


SOURCES = [
    # --- Burgenland: WIDMUNGSFLAECHEN.zip, WIDCODE (neu in v2) ---
    {"key": "bgld_wohn", "dataset": "bgld", "bucket": "wohn_misch", "category": "wohn",
     "match": [_v("WIDCODE", BGLD_WOHN_CODES)]},
    {"key": "bgld_ferienhaus", "dataset": "bgld", "bucket": "haeuser_im_gruenen", "category": "ferienhaus_tourismus",
     "match": [_v("WIDCODE", BGLD_FERIENHAUS_CODES)]},
    {"key": "bgld_industrie", "dataset": "bgld", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_v("WIDCODE", BGLD_INDUSTRIE_CODES)]},

    # --- Niederösterreich: RRU_WI_HUELLE.gpkg, WI_ART ---
    {"key": "noe_wohn", "dataset": "noe", "bucket": "wohn_misch", "category": "wohn",
     "match": [_v("WI_ART", {"SBL", "SBLNB"})]},
    {"key": "noe_hofstelle", "dataset": "noe", "bucket": "haeuser_im_gruenen", "category": "hofstelle",
     "match": [_v("WI_ART", {"Gho"})]},
    {"key": "noe_camping", "dataset": "noe", "bucket": "haeuser_im_gruenen", "category": "camping",
     "match": [_v("WI_ART", {"Gc"})]},
    {"key": "noe_kleingarten", "dataset": "noe", "bucket": "haeuser_im_gruenen", "category": "kleingarten",
     "match": [_v("WI_ART", {"Gkg"})]},

    # --- Oberösterreich: FLWI_WIDMUNGEN_F.zip, KENNZAHL ---
    {"key": "ooe_wohn", "dataset": "ooe", "bucket": "wohn_misch", "category": "wohn",
     "match": [_v("KENNZAHL", OOE_WOHN_CODES)]},
    {"key": "ooe_misch", "dataset": "ooe", "bucket": "wohn_misch", "category": "misch",
     "match": [_v("KENNZAHL", OOE_MISCH_CODES)]},
    {"key": "ooe_hofstelle", "dataset": "ooe", "bucket": "haeuser_im_gruenen", "category": "hofstelle",
     "match": [_v("KENNZAHL", OOE_HOFSTELLE_CODES)]},
    {"key": "ooe_camping", "dataset": "ooe", "bucket": "haeuser_im_gruenen", "category": "camping",
     "match": [_v("KENNZAHL", OOE_CAMPING_CODES)]},
    {"key": "ooe_golf", "dataset": "ooe", "bucket": "haeuser_im_gruenen", "category": "golf",
     "match": [_v("KENNZAHL", OOE_GOLF_CODES)]},
    {"key": "ooe_kleingarten", "dataset": "ooe", "bucket": "haeuser_im_gruenen", "category": "kleingarten",
     "match": [_v("KENNZAHL", OOE_KLEINGARTEN_CODES)]},
    {"key": "ooe_industrie", "dataset": "ooe", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_v("KENNZAHL", OOE_INDUSTRIE_CODES)]},

    # --- Steiermark: Bauland.zip (wohn/misch/industrie) + Flaewi.shp.zip (grün/freizeit) ---
    {"key": "stmk_wohn", "dataset": "stmk_bauland", "bucket": "wohn_misch", "category": "wohn",
     "match": [_v("GRUPPE_4", {"wohn_Nutz"})]},
    {"key": "stmk_misch", "dataset": "stmk_bauland", "bucket": "wohn_misch", "category": "misch",
     "match": [_v("GRUPPE_4", {"gem_Nutz"})]},
    # GRUPPE_4 == "betr_Nutz" fasst I1/GG samt A-/SG-Varianten zusammen (13.937 Flächen).
    {"key": "stmk_industrie", "dataset": "stmk_bauland", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_v("GRUPPE_4", {"betr_Nutz"})]},
    {"key": "stmk_auffuellungsgebiet", "dataset": "stmk_flaewi", "bucket": "haeuser_im_gruenen", "category": "auffuellungsgebiet",
     "match": [_sw("WIDMUNG", STMK_LEISURE_PREFIXES["auffuellungsgebiet"])]},
    {"key": "stmk_kleingarten", "dataset": "stmk_flaewi", "bucket": "haeuser_im_gruenen", "category": "kleingarten",
     "match": [_sw("WIDMUNG", STMK_LEISURE_PREFIXES["kleingarten"])]},
    {"key": "stmk_camping", "dataset": "stmk_flaewi", "bucket": "haeuser_im_gruenen", "category": "camping",
     "match": [_sw("WIDMUNG", STMK_LEISURE_PREFIXES["camping"])]},

    # --- Kärnten: flawi_ktn_gpkg.zip, Layer WIDG ---
    {"key": "ktn_wohn", "dataset": "ktn", "bucket": "wohn_misch", "category": "wohn",
     "match": [_v("WIDMUNG", KTN_WOHN_VALUES)]},
    {"key": "ktn_misch", "dataset": "ktn", "bucket": "wohn_misch", "category": "misch",
     "match": [_v("WIDMUNG", KTN_MISCH_VALUES)]},
    {"key": "ktn_hofstelle", "dataset": "ktn", "bucket": "haeuser_im_gruenen", "category": "hofstelle",
     "match": [_v("WIDCODE", KTN_HOFSTELLE_WIDCODES)]},
    {"key": "ktn_camping", "dataset": "ktn", "bucket": "haeuser_im_gruenen", "category": "camping",
     "match": [_v("KATEGORIE", {"Grünland"}), _v("WIDMUNG", {"Campingplatz"})]},
    {"key": "ktn_industrie", "dataset": "ktn", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_v("WIDMUNG", KTN_INDUSTRIE_VALUES)]},

    # --- Salzburg: Flaechenwidmung_Shapefile.zip, Typname ---
    {"key": "sbg_wohn", "dataset": "sbg", "bucket": "wohn_misch", "category": "wohn",
     "match": [_v("Typname", SBG_WOHN_CODES)]},
    {"key": "sbg_camping", "dataset": "sbg", "bucket": "haeuser_im_gruenen", "category": "camping",
     "match": [_v("Typname", {"GLCA"})]},
    {"key": "sbg_kleingarten", "dataset": "sbg", "bucket": "haeuser_im_gruenen", "category": "kleingarten",
     "match": [_v("Typname", {"GLKG"})]},
    {"key": "sbg_industrie", "dataset": "sbg", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_v("Typname", SBG_INDUSTRIE_CODES)]},

    # --- Tirol: FLW_Flaechenwidmung...gpkg, WIDMUNG(+FESTLEGUNG) ---
    {"key": "tir_wohn", "dataset": "tir", "bucket": "wohn_misch", "category": "wohn",
     "match": [_sw("WIDMUNG", TIR_WOHN_PREFIXES)]},
    {"key": "tir_kerngebiet", "dataset": "tir", "bucket": "wohn_misch", "category": "kerngebiet",
     "match": [_sw("WIDMUNG", TIR_KERNGEBIET_PREFIXES)]},
    {"key": "tir_misch", "dataset": "tir", "bucket": "wohn_misch", "category": "misch",
     "match": [_sw("WIDMUNG", TIR_MISCH_PREFIXES)]},
    {"key": "tir_tourismus", "dataset": "tir", "bucket": "haeuser_im_gruenen", "category": "ferienhaus_tourismus",
     "match": [_sw("WIDMUNG", TIR_TOURISMUS_PREFIXES)]},
    {"key": "tir_hofstelle", "dataset": "tir", "bucket": "haeuser_im_gruenen", "category": "hofstelle",
     "match": [_c("WIDMUNG", "Hofstelle|Austraghaus")]},
    {"key": "tir_camping", "dataset": "tir", "bucket": "haeuser_im_gruenen", "category": "camping",
     "match": [_v("WIDMUNG", TIR_LEISURE_WIDMUNG), _c("FESTLEGUNG", "(?i)camping")]},
    {"key": "tir_golf", "dataset": "tir", "bucket": "haeuser_im_gruenen", "category": "golf",
     "match": [_v("WIDMUNG", TIR_LEISURE_WIDMUNG), _c("FESTLEGUNG", "(?i)golf")]},
    {"key": "tir_industrie", "dataset": "tir", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_sw("WIDMUNG", TIR_INDUSTRIE_PREFIXES)]},

    # --- Vorarlberg: fwp_flaeche.gpkg, wi_em_txt ---
    {"key": "vbg_wohn", "dataset": "vbg", "bucket": "wohn_misch", "category": "wohn",
     "match": [_c("wi_em_txt", VBG_WOHN_PATTERN)]},
    {"key": "vbg_camping", "dataset": "vbg", "bucket": "haeuser_im_gruenen", "category": "camping",
     "match": [_c("wi_em_txt", "(?i)camping")]},
    {"key": "vbg_golf", "dataset": "vbg", "bucket": "haeuser_im_gruenen", "category": "golf",
     "match": [_c("wi_em_txt", "(?i)golf")]},
    {"key": "vbg_kleingarten", "dataset": "vbg", "bucket": "haeuser_im_gruenen", "category": "kleingarten",
     "match": [_c("wi_em_txt", "(?i)kleingarten")]},
    {"key": "vbg_industrie", "dataset": "vbg", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_c("wi_em_txt", VBG_INDUSTRIE_PATTERN)]},

    # --- Wien: generalisierte Flächenwidmung, WIDMUNGSKLASSE_TXT/WIDMUNG_TXT ---
    {"key": "wien_wohn", "dataset": "wien", "bucket": "wohn_misch", "category": "wohn",
     "match": [_sw("WIDMUNGSKLASSE_TXT", WIEN_WOHN_PREFIXES)]},
    {"key": "wien_misch", "dataset": "wien", "bucket": "wohn_misch", "category": "misch",
     "match": [_c("WIDMUNGSKLASSE_TXT", WIEN_MISCH_PATTERN)]},
    # Kleingärten stecken in der Klasse "Erholungsgebiet" und sind nur über
    # WIDMUNG_TXT trennbar (10,2 km², überwiegend "für ganzjähriges Wohnen");
    # das Gartensiedlungsgebiet (2,7 km²) ist eine eigene Klasse.
    {"key": "wien_kleingarten", "dataset": "wien", "bucket": "haeuser_im_gruenen", "category": "kleingarten",
     "match": [_c("WIDMUNG_TXT", "(?i)kleingarten")]},
    {"key": "wien_gartensiedlung", "dataset": "wien", "bucket": "haeuser_im_gruenen", "category": "kleingarten",
     "match": [_v("WIDMUNGSKLASSE_TXT", {"Gartensiedlungsgebiet"})]},
    {"key": "wien_industrie", "dataset": "wien", "bucket": "industrie_negativ", "category": "industrie",
     "match": [_c("WIDMUNGSKLASSE_TXT", WIEN_INDUSTRIE_PATTERN)]},
]

SOURCES_BY_KEY = {s["key"]: s for s in SOURCES}


def dataset_columns(dataset_key: str) -> list[str]:
    """Every column a dataset must be read with: code, optional label, extras."""
    spec = DATASETS[dataset_key]
    cols = [spec["code_col"]]
    if spec.get("label_col"):
        cols.append(spec["label_col"])
    cols.extend(spec.get("extra_cols", []))
    for src in SOURCES:
        if src["dataset"] != dataset_key:
            continue
        for pred in src["match"]:
            if pred["col"] not in cols:
                cols.append(pred["col"])
    return cols


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def _ensure_ktn_gpkg(cache_dir: Path) -> Path:
    """Extract the Kärnten GeoPackage locally once.

    GDAL's /vsizip reads a GeoPackage (SQLite) via repeated random-access seeks
    into the compressed DEFLATE stream - each B-tree lookup forces re-decompression,
    which turns a normal read into a multi-minute hang on a 400 MB+ file. Extracting
    once to a local cache and reading that is orders of magnitude faster.
    """
    import zipfile

    cached = cache_dir / KTN_GPKG_CACHE_NAME
    if cached.exists():
        return cached
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = WIDMUNG / "kaernten" / "flawi_ktn_gpkg.zip"
    with zipfile.ZipFile(zip_path) as zf:
        inner = next(n for n in zf.namelist() if n.endswith(".gpkg"))
        with zf.open(inner) as src, open(cached, "wb") as dst:
            dst.write(src.read())
    return cached


def _tirol_gpkg() -> Path:
    """Locate the Tirol Flächenwidmung GeoPackage.

    Returns
    -------
    Path
        The first (sorted) ``FLW_Flaechenwidmung_*.gpkg`` under
        ``data/widmung/tirol``.

    Raises
    ------
    FileNotFoundError
        If no such file exists.
    """
    matches = sorted((WIDMUNG / "tirol").glob("FLW_Flaechenwidmung_*.gpkg"))
    if not matches:
        raise FileNotFoundError(f"no FLW_Flaechenwidmung_*.gpkg in {WIDMUNG / 'tirol'}")
    return matches[0]


def _read_raw(dataset_key: str, columns: list[str], cache_dir: Path) -> gpd.GeoDataFrame:
    """Read one dataset from its OGD file, without filtering or reprojection.

    Parameters
    ----------
    dataset_key : str
        Key in ``DATASETS``.
    columns : list of str
        Columns to read.
    cache_dir : Path
        Cache directory for the extracted Kärnten GeoPackage.

    Returns
    -------
    geopandas.GeoDataFrame
        The raw features.

    Raises
    ------
    ValueError
        If ``dataset_key`` is unknown.
    """
    if dataset_key == "bgld":
        return gpd.read_file(WIDMUNG / "burgenland" / "WIDMUNGSFLAECHEN.zip", layer="BGLD_FLAECHENWIDMUNG", columns=columns)
    if dataset_key == "ktn":
        return gpd.read_file(_ensure_ktn_gpkg(cache_dir), layer="WIDG", columns=columns)
    if dataset_key == "noe":
        return gpd.read_file(WIDMUNG / "niederoesterreich" / "RRU_WI_HUELLE.gpkg", columns=columns)
    if dataset_key == "ooe":
        return gpd.read_file(f"zip://{WIDMUNG / 'oberoesterreich' / 'FLWI_WIDMUNGEN_F.zip'}!FLWI_WIDMUNGEN_F.shp", columns=columns)
    if dataset_key == "sbg":
        return gpd.read_file(f"zip://{WIDMUNG / 'salzburg' / 'Flaechenwidmung_Shapefile.zip'}!Flaechenwidmung/Flaechenwidmung.shp", columns=columns)
    if dataset_key == "stmk_bauland":
        return gpd.read_file(f"zip://{WIDMUNG / 'steiermark' / 'Bauland.zip'}!Bauland.shp", columns=columns)
    if dataset_key == "stmk_flaewi":
        return gpd.read_file(WIDMUNG / "steiermark" / "Flaewi.shp.zip", layer="FWP_NUTZ", columns=columns)
    if dataset_key == "tir":
        return gpd.read_file(_tirol_gpkg(), layer="FLW_Flaechenwidmung", columns=columns)
    if dataset_key == "vbg":
        return gpd.read_file(WIDMUNG / "vorarlberg" / "fwp_flaeche.gpkg", layer="fwp_flaeche", columns=columns)
    if dataset_key == "wien":
        return gpd.read_file(WIDMUNG / "wien" / "genflwidmung_wien.geojson", columns=columns)
    raise ValueError(f"unknown dataset key: {dataset_key}")


def clip_to_at_bbox(gdf: gpd.GeoDataFrame, label: str) -> gpd.GeoDataFrame:
    """Drop features whose bounds fall outside Austria (Stmk OGD shift artefacts)."""
    bounds = gdf.geometry.bounds
    inside = ((bounds.minx >= AT_BBOX[0]) & (bounds.miny >= AT_BBOX[1])
              & (bounds.maxx <= AT_BBOX[2]) & (bounds.maxy <= AT_BBOX[3]))
    if (~inside).any():
        print(f"[{label}] {int((~inside).sum())} Features außerhalb AT-BBox verworfen")
    return gdf[inside]


@functools.lru_cache(maxsize=1)
def _read_dataset_cached(dataset_key: str, cache_dir_str: str) -> gpd.GeoDataFrame:
    """Read a dataset, drop empty geometries, reproject and clip to Austria.

    Only the most recent call is cached (``lru_cache(maxsize=1)``).

    Parameters
    ----------
    dataset_key : str
        Key in ``DATASETS``.
    cache_dir_str : str
        Cache directory as a string (hashable for the cache).

    Returns
    -------
    geopandas.GeoDataFrame
        Features in ``WORK_CRS`` with the columns from ``dataset_columns()``,
        clipped by ``clip_to_at_bbox()``, with a fresh index.
    """
    columns = dataset_columns(dataset_key)
    gdf = _read_raw(dataset_key, columns, Path(cache_dir_str))
    gdf = gdf[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
    gdf = gdf.to_crs(WORK_CRS)
    return clip_to_at_bbox(gdf, dataset_key).reset_index(drop=True)


def read_dataset(dataset_key: str, cache_dir: Path) -> gpd.GeoDataFrame:
    """One dataset, all needed columns, EPSG:31287, AT-bbox-clipped.

    ``lru_cache(maxsize=1)`` on purpose: callers iterate dataset by dataset, so
    every file is read exactly once while never holding two of the large frames
    (Stmk FWP_NUTZ alone is ~410 MB) in memory at the same time.
    """
    return _read_dataset_cached(dataset_key, str(cache_dir))


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def predicate_mask(gdf: gpd.GeoDataFrame, pred: dict) -> pd.Series:
    """Evaluate one match predicate against a GeoDataFrame.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        The dataset.
    pred : dict
        Predicate built by ``_v``, ``_sw`` or ``_c``.

    Returns
    -------
    pandas.Series
        Boolean mask. ``startswith`` and ``contains`` compare the column as
        text with missing values treated as empty strings.

    Raises
    ------
    KeyError
        If the predicate column is missing.
    ValueError
        If the predicate kind is not supported.
    """
    col = pred["col"]
    if col not in gdf.columns:
        raise KeyError(f"column {col!r} missing; available: {sorted(gdf.columns)}")
    series = gdf[col]
    if "values" in pred:
        return series.isin(pred["values"])
    text = series.fillna("").astype(str)
    if "startswith" in pred:
        return text.str.startswith(pred["startswith"], na=False)
    if "contains" in pred:
        return text.str.contains(pred["contains"], na=False, regex=True)
    raise ValueError(f"unsupported predicate: {pred!r}")


def source_mask(gdf: gpd.GeoDataFrame, source: dict) -> pd.Series:
    """AND of all predicates of one SOURCES entry."""
    mask = pd.Series(True, index=gdf.index)
    for pred in source["match"]:
        mask &= predicate_mask(gdf, pred)
    return mask


def sources_for_dataset(dataset_key: str, bucket: str | None = None, bl_filter: set[str] | None = None) -> list[dict]:
    """Select the ``SOURCES`` entries of one dataset.

    Parameters
    ----------
    dataset_key : str
        Key in ``DATASETS``.
    bucket : str, optional
        Only sources of this bucket.
    bl_filter : set of str, optional
        Only sources whose dataset belongs to one of these Bundesländer.

    Returns
    -------
    list of dict
        Matching sources in ``SOURCES`` order.
    """
    out = []
    for src in SOURCES:
        if src["dataset"] != dataset_key:
            continue
        if bucket is not None and src["bucket"] != bucket:
            continue
        if bl_filter is not None and DATASETS[src["dataset"]]["bundesland"] not in bl_filter:
            continue
        out.append(src)
    return out


def bundesland_of(source: dict) -> str:
    """Bundesland of a source.

    Parameters
    ----------
    source : dict
        An entry of ``SOURCES``.

    Returns
    -------
    str
        ``DATASETS[source["dataset"]]["bundesland"]``.
    """
    return DATASETS[source["dataset"]]["bundesland"]
