"""Layer: Widmung und Häuser im Grünen (Paket W2.1, docs/rewrite/PLAN.md
§7, §13.8).

``scripts/widmung_v2/02_build_hig_sources.py`` schreibt heute sieben
Checkpoint-Raster (``SOURCE_LAYER_NAMES`` dort) in einem einzigen
``ensure_group_layers()``-Aufruf. Diese Datei übernimmt genau diese sieben
Namen, unverändert:

    official_settlement_source, ferienhaus_tourismus_source,
    official_hig_source, noe_pdf_750m_zones, hig_hulls_source,
    bewohnt_einzellage_source, nonresidential_hulls_source

## Warum "Widmung" und "Häuser im Grünen" EIN Paket sind, nicht zwei

``calc/hig_source_masks.py:widmung_seed()`` (Zeile ~70-75)
verundet die drei Widmungs-Buckets (``official_settlement_source``,
``official_hig_source``, ``ferienhaus_tourismus_source``) zu einer
Vereinigung. Diese Vereinigung geht als Eingabe in
``candidate_filter_mask()`` (dieselbe Datei, direkt darunter) - der
Kandidatenfilter, der in ``build_sources()`` unten den DKM-Scan
(``scan_dkm_candidates``) einschränkt, aus dem wiederum die vier übrigen
Checkpoints (``hig_hulls_source``, ``bewohnt_einzellage_source``,
``nonresidential_hulls_source``, und indirekt ``noe_pdf_750m_zones`` als
Filterzusatz) entstehen. Eine Trennung in zwei Pakete hätte entweder
``widmung_seed()``/``candidate_filter_mask()`` verdoppeln müssen (zwei
Kopien derselben Regel, Bug-Gefahr bei künftiger Änderung - siehe
denselben Einwand im Docstring von ``pipeline/prep/widmung.py``) oder eine
Datei zu zweit besitzen müssen - beides laut Auftrag verboten. Deshalb
EIN ``ensure_group_layers()``-Aufruf für alle sieben Checkpoints, genau
wie im Originalskript.

## Kein externes Quell-Checkpoint-Verzeichnis nötig

Anders als W2.3 (``pipeline/layers/osm.py``) und W2.4
(``pipeline/layers/geo.py``) liest ``build_sources()`` unten KEINEN der 33
Checkpoints aus einem separaten Quellverzeichnis (vormals
``output/abschichtung_widmung_v2/distance_layers``, seit W6.1 nicht mehr im
Repo) - alle Eingaben sind entweder Prep-Ausgaben oder zur Laufzeit
berechnete Zwischenwerte derselben Ausführung. Diese Datei kennt deshalb nur EIN
Ausgabeverzeichnis (``--out-dir``, Default ``pipeline.contract.DERIVED_LAYERS``
= ``derived/layers/``), keine ``--source-dir``/``--layer-dir``-Zweiteilung wie
bei W2.3/W2.4.

## Der eigentliche Zweck: derived/prep/ statt eines fremden Zwischenstands

``02_build_hig_sources.py`` liest heute vier Rohquellen, die diese Datei
jetzt aus der Prep-Stufe bezieht:

    zoning_masks()          -> derived/prep/widmung/{wohn_misch,
                               haeuser_im_gruenen,industrie_negativ}_combined.gpkg
                               (pipeline/prep/widmung.py, W1.P4) statt
                               --zoning-dir (vormals
                               output/abschichtung_widmung_v2/zoning_vectors,
                               ein Zwischenstand aus 01_build_official_zoning_layers.py)
    noe_pdf_mask()           -> derived/prep/noe_sekrop/b_vectorize/pdf_750m_*.geojson
                               (pipeline/prep/noe_sekrop.py, W1.P9) statt
                               --noe-dir (vormals output/noe)
    scan_dkm_candidates()    -> derived/prep/kataster/b_export_parquet/
                               at_dkm_gst_nfl_epsg31287.geoparquet
                               (pipeline/prep/kataster/b_export_parquet.py,
                               W1.P2) statt --dkm-parquet (vormals
                               output/kataster/at_dkm_gst_nfl_epsg31287.geoparquet)
    load_address_points(),
    load_building_points()   -> derived/prep/adressen/{adressen_31287,
                               bev_gebaeude_31287}.parquet
                               (pipeline/prep/adressen.py, W1.P3) statt
                               data/adressen direkt - der --cache-dir-Default
                               war schon vor diesem Paket derived/prep/adressen
                               (Fix aus W1.1, siehe dortiger Bericht), ändert
                               sich hier nicht.

Die dritte Zeile ist der eigentliche Anlass dieses Pakets: der bisherige
Vorgabewert ``output/kataster/at_dkm_gst_nfl_epsg31287.geoparquet`` ist eine
5,3-GB-Datei vom 15. Mai 2026 - ein Überbleibsel des Vorgängerprojekts,
vier Monate älter als der erste Commit dieses Repos. Diese Datei rührt sie
an KEINER Stelle an (weder als Vorgabewert noch lesend noch schreibend);
der neue Default ist ``pipeline.prep.kataster.b_export_parquet.DEFAULT_OUTPUT``
(importiert, nicht neu als Literal geschrieben - Regel 2).

## Fingerabdruck-Konvention (folgt W2.4, nicht W2.3 - Entscheidung bereits
getroffen)

Ein Tag ``PREP_FINGERPRINT`` in jedem der sieben Checkpoints, SHA-256 über
``pipeline.fingerprint.compute()`` der tatsächlich gelesenen Dateien
(``_prep_inputs()`` unten: die drei Widmungs-GPKGs, die drei NÖ-PDF-
GeoJSONs, das Kataster-GeoParquet, die beiden BEV-Parquet-Caches - neun
Dateien insgesamt). Geprüft über den ``extra_ok``/``extra_tags``-Mechanismus
von ``layer_done()`` (``calc/abschichtung_common.py``), genau wie
bei den neun Prep-Stufen - siehe PLAN.md §12 "Nebenbefund mit Folgen für
Welle 2" und der Kommentar dazu in ``04_create_distance_zones.py:446-447``.
Bei Abweichung: neu bauen (die sieben Checkpoints landen in ``missing``,
kein Abbruch) - eine Abweichung heißt nur "das Prep ist seither neu
gelaufen", kein Fehlerzustand.

Die bisherigen Parameter-Tags aus ``02_build_hig_sources.py:_params_tag()``
(``HIG_FILTER_BUFFER_M`` usw.) bleiben unverändert zusätzlich zum neuen
``PREP_FINGERPRINT``-Tag bestehen - beide zusammen entscheiden über
Wiederverwendung.

## Punkt 24: erbt diese Stufe die Kärnten-Extraktions-Cache-Lücke?

Nein. ``widmung_sources._ensure_ktn_gpkg()`` (die Cache-Weiche, die nur
prüft OB die entpackte Datei existiert, nicht ob sich
``data/widmung/kaernten/flawi_ktn_gpkg.zip`` seither geändert hat) wird
ausschließlich von ``calc/widmung_sources.py`` selbst aufgerufen,
und zwar nur über ``pipeline/prep/widmung.py`` (Paket W1.P4, bereits
abgeschlossen). Diese Datei hier importiert ``widmung_sources`` nicht und
ruft auch nichts auf, was es täte - ``zoning_masks()`` (aus
``hig_source_masks.py``) liest ausschließlich die bereits fertigen
``*_combined.gpkg``-Bündel aus ``derived/prep/widmung/``. Der eigene
``PREP_FINGERPRINT``-Tag dieser Stufe erfasst diese drei GPKGs über
Größe/Änderungszeit (siehe ``pipeline/fingerprint.py``); sollte die
Kärnten-Cache-Lücke die Prep-Stufe dazu bringen, ein GPKG mit falschem
(veraltetem) Inhalt, aber gleicher Größe/mtime zu belassen, würde das auch
diese Stufe nicht auffangen - dieselbe Grenze, die ``fingerprint.py`` selbst
im eigenen Docstring einräumt ("Größe und Änderungszeit, nicht der
Dateiinhalt"). Das Problem selbst liegt jedoch strukturell außerhalb dieses
Pakets (W1.P4-Code, nicht W2.1-Code) und wird hier nur gemeldet, nicht
behoben.

## Regel 4 - fachliche Eigenheiten unverändert übernommen

``build_sources()`` unten ist inhaltlich Zeile-für-Zeile
``02_build_hig_sources.py:build_sources()`` - jede Schwelle
(``HIG_FILTER_BUFFER_M``, ``HIG_CHAIN_M``, ``HIG_MIN_ADRESSEN``, ...), jede
Klassifikationsregel (Streusiedlung vs. Einzellage, industriegebietartig
vs. unbewohnt) und jede Sonderbehandlung (NÖ-PDF-Zonen sind bereits
Objekt+750m und gehen ungepuffert ins Ergebnis) bleibt unangetastet.
Einzige Änderung: die vier I/O-Vorgabewerte oben (``--zoning-dir``,
``--noe-dir``, ``--dkm-parquet``, ``--cache-dir`` zeigen jetzt auf
``derived/prep/`` statt auf Roh-/Zwischenstände) sowie die Zusammenlegung von
``--layer-dir``/``--out-dir`` zu einem einzigen ``--out-dir`` (siehe oben,
"Kein externes Quell-Checkpoint-Verzeichnis nötig").

## W5.P2 (08.09.2026, Punkt 34) - eine Ausnahme von Regel 4

Die einzige fachliche Änderung an dieser Datei seit W2.1: DKM-Riesenflächen
über ``HIG_MAX_FOOTPRINT_M2`` (735 ha größte, NÖ-DXF-Polygonisierungs-
artefakte) wurden bisher AUSNAHMSLOS auf eine 5-m-Scheibe um ihren Zentroid
reduziert. Der Nutzer hat entschieden: eine Riesenfläche ohne jede eigene
BEV-Adresse (520 von 806 gemessenen Fällen, siehe Bericht W5.P2) entfällt
jetzt als Kandidat vollständig - keine Scheibe, keine Hüllen-Mitgliedschaft.
Riesenflächen MIT mindestens einer eigenen BEV-Adresse behalten das
bisherige Verhalten unverändert (Scheibe um den Zentroid, kein
``representative_point()``). Umgesetzt in
``calc/hig_detection.py:scan_dkm_candidates()`` über den neuen
``address_xy``-Parameter; ``HIG_MAX_FOOTPRINT_M2`` selbst (10 000 m²) und
``HIG_MIN_ADRESSEN`` (5) bleiben unverändert.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import contract, fingerprint, runtime  # noqa: E402
from pipeline.prep import widmung as prep_widmung  # noqa: E402
from pipeline.prep.kataster.b_export_parquet import DEFAULT_OUTPUT as KATASTER_PARQUET  # noqa: E402

from calc.config import load_config  # noqa: E402
from calc.abschichtung_common import (  # noqa: E402
    HIG_ADDRESS_RADIUS_M,
    HIG_CHAIN_M,
    HIG_FILTER_BUFFER_M,
    HIG_GARDEN_RADIUS_M,
    HIG_INDUSTRIE_WIDMUNG_MIN_SHARE,
    HIG_MAX_FOOTPRINT_M2,
    HIG_MIN_ADRESSEN,
    HIG_WOHNANTEIL_MIN_SHARE,
    ensure_group_layers,
    load_grid,
    timed,
)
from calc.bev_register import (  # noqa: E402
    ADDRESS_CACHE_NAME,
    BUILDING_CACHE_NAME,
    load_address_points,
    load_building_points,
)
from calc.hig_detection import (  # noqa: E402
    HULL_CLASS_BEWOHNT,
    HULL_CLASS_INDUSTRIE,
    HULL_CLASS_UNBEWOHNT,
    aggregate_hulls,
    build_hulls,
    building_signals,
    class_masks,
    dominant_bundesland,
    hull_polygons,
    label_mask,
    sample_labels,
    scan_dkm_candidates,
)
from calc.streusiedlung import chain_hull_params  # noqa: E402
from calc.hig_source_masks import (  # noqa: E402
    NOE_PDF_LAYER_NAMES,
    candidate_filter_mask,
    noe_pdf_mask,
    widmung_seed,
    zoning_masks,
)

# ---------------------------------------------------------------------------
# Wortgleich aus scripts/widmung_v2/02_build_hig_sources.py übernommen
# (Regel 4) - dort Zeilen 76-86. Seit W6.1 aus dem Repo entfernt, letzter
# Stand im Commit f1d00f7 - git show
# f1d00f7:scripts/widmung_v2/02_build_hig_sources.py.
# ---------------------------------------------------------------------------

SOURCE_LAYER_NAMES = [
    "official_settlement_source",
    "ferienhaus_tourismus_source",
    "official_hig_source",
    "noe_pdf_750m_zones",
    "hig_hulls_source",
    "bewohnt_einzellage_source",
    "nonresidential_hulls_source",
]

HULL_GPKG_NAME = "hig_huellen.gpkg"


# ---------------------------------------------------------------------------
# Fingerabdruck der Prep-Eingaben (folgt der W2.4-Konvention, siehe
# Moduldocstring) - der SHA-256 landet als PREP_FINGERPRINT-Tag in jedem
# der sieben Checkpoints.
# ---------------------------------------------------------------------------


def _prep_inputs() -> list[Path]:
    """Alle tatsächlich gelesenen Prep-Ausgaben dieser Stufe."""
    widmung_dir = contract.PREP["widmung"]
    noe_dir = contract.PREP["noe_sekrop"]["b_vectorize"]
    adressen_dir = contract.PREP["adressen"]
    return sorted(
        [
            *(
                widmung_dir / prep_widmung.BUNDLE_FILENAME.format(bucket=bucket)
                for bucket in ("wohn_misch", "haeuser_im_gruenen", "industrie_negativ")
            ),
            *(noe_dir / f"{name}.geojson" for name in NOE_PDF_LAYER_NAMES),
            KATASTER_PARQUET,
            adressen_dir / ADDRESS_CACHE_NAME,
            adressen_dir / BUILDING_CACHE_NAME,
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
# Wortgleich aus 02_build_hig_sources.py:build_sources() übernommen (Regel
# 4) - einzige Änderung: liest zoning_dir/noe_dir/dkm_parquet/address_dir/
# cache_dir jetzt aus den (auf derived/prep/ umgestellten) Vorgabewerten in
# parse_args(), statt hartkodierter output/-/data/-Pfade (dieses output/
# existiert seit W6.1 nicht mehr im Repo); hig_huellen.gpkg
# geht nach ``out_dir`` statt einem separaten ``--out-dir``, siehe
# Moduldocstring. 02_build_hig_sources.py selbst ist seit W6.1 aus dem Repo
# entfernt, letzter Stand im Commit f1d00f7 - git show
# f1d00f7:scripts/widmung_v2/02_build_hig_sources.py.
# ---------------------------------------------------------------------------


def build_sources(cfg: dict, grid: dict, args: argparse.Namespace, out_dir: Path) -> dict[str, np.ndarray]:
    """Stufen A-C; gibt die Checkpoint-Masken zurück und schreibt hig_huellen.gpkg."""
    zoning_dir = Path(args.zoning_dir)
    with timed("Stufe A: Widmungsmasken"):
        zoning = zoning_masks(zoning_dir, grid)
        noe_pdf = noe_pdf_mask(Path(args.noe_dir), grid)

    with timed(f"Stufe A: Kandidaten-Filter (+{args.filter_buffer_m:g} m)"):
        # NÖ-PDF-Zonen sind schon 750-m-Zonen; sie filtern ohne weitere Aufweitung.
        filter_mask = candidate_filter_mask(zoning, noe_pdf, args.filter_buffer_m, grid)
        print(
            f"[info]  Filter: Widmung {int(widmung_seed(zoning).sum()):,} Zellen -> "
            f"{int(filter_mask.sum()):,} Filterzellen (inkl. NÖ-PDF)",
            flush=True,
        )

    bl_filter = set(args.bl) if args.bl else None
    hull_dilate_m, hull_erode_m = chain_hull_params(args.chain_m)
    address_dir = Path(args.address_dir)
    cache_dir = Path(args.cache_dir)
    with timed("Stufe A: BEV-Adressen laden"):
        # Vor dem DKM-Scan geladen (nicht erst in Stufe C wie bisher): der
        # Scan braucht den Adressbestand jetzt selbst, um adresslose
        # Riesen-Footprints (Punkt 34, W5.P2) als Kandidaten zu verwerfen.
        address_xy = load_address_points(address_dir, cache_dir=cache_dir)

    with timed("Stufe B: DKM-Scan"):
        scan = scan_dkm_candidates(
            Path(args.dkm_parquet), grid, filter_mask, args.max_footprint_m2, bl_filter=bl_filter,
            margin_m=hull_dilate_m, address_xy=address_xy,
        )
        n_oversized_kept = scan.n_oversized - scan.n_addressless_dropped
        print(
            f"[info]  Bauflächen {scan.n_buildings_total:,} | gefiltert {scan.n_filtered:,} "
            f"({100 * scan.n_filtered / max(scan.n_buildings_total, 1):.1f}%) | "
            f"Kandidaten {len(scan):,} | >{args.max_footprint_m2:g}m2 gesamt {scan.n_oversized:,} "
            f"(Scheibe {n_oversized_kept:,}, adresslos entfallen {scan.n_addressless_dropped:,}) | "
            f"Garten-Punkte {len(scan.garden_xy):,}",
            flush=True,
        )

    with timed(f"Stufe B: Hüllenbildung ({args.chain_m:g}-m-Verkettung)"):
        hull_mask, labels, n_labels = build_hulls(
            scan.geometries, grid, hull_dilate_m, hull_erode_m,
        )
        print(f"[info]  Hüllen: {n_labels:,} ({int(hull_mask.sum()):,} Zellen)", flush=True)

    with timed("Stufe C: Signale + Klassifikation"):
        bev_buildings = load_building_points(address_dir, cache_dir=cache_dir)
        signals = building_signals(
            scan, address_xy, bev_buildings, args.address_radius_m, args.garden_radius_m,
            industrie_widmung_mask=zoning["industrie_negativ"], grid=grid,
        )
        hull_label = sample_labels(labels, scan.centroids, grid)
        hull_frame = aggregate_hulls(
            hull_label, signals, n_labels,
            args.wohnanteil_min_share, args.industrie_widmung_min_share,
        )
        # Streusiedlungs-Regel (Knie-Analyse): bewohnte Hüllen mit >= min_adressen
        # adressierten Objekten sind GEBIETE (750 m); bewohnte darunter sind
        # Einzellagen und werden als eigene Klasse ausgewiesen (25 m) - sichtbar,
        # nicht stillschweigend verworfen.
        ist_bewohnt = hull_frame["klasse"].eq(HULL_CLASS_BEWOHNT)
        ist_streusiedlung = ist_bewohnt & (hull_frame["n_adressen"] >= args.min_adressen)
        hull_frame["ist_streusiedlung"] = ist_streusiedlung.astype(np.int8)
        counts = hull_frame["klasse"].value_counts().to_dict()
        print(
            f"[info]  Klassen: bewohnt={counts.get(HULL_CLASS_BEWOHNT, 0):,} "
            f"(davon Streusiedlung={int(ist_streusiedlung.sum()):,}, "
            f"Einzellage={int((ist_bewohnt & ~ist_streusiedlung).sum()):,}), "
            f"industriegebietartig={counts.get(HULL_CLASS_INDUSTRIE, 0):,}, "
            f"unbewohnt={counts.get(HULL_CLASS_UNBEWOHNT, 0):,}",
            flush=True,
        )

    streusiedlung_mask = label_mask(
        labels, hull_frame.loc[ist_streusiedlung, "label"].to_numpy(), n_labels)
    einzellage_mask = label_mask(
        labels, hull_frame.loc[ist_bewohnt & ~ist_streusiedlung, "label"].to_numpy(), n_labels)
    masks = class_masks(labels, hull_frame, n_labels)

    if not args.skip_gpkg:
        with timed("Hüllen-GPKG schreiben"):
            hulls = hull_polygons(labels, hull_mask, hull_frame, grid)
            if not hulls.empty:
                hulls["bundesland"] = dominant_bundesland(hull_label, scan.bundesland, hulls["label"].to_numpy())
            out_path = out_dir / HULL_GPKG_NAME
            out_path.parent.mkdir(parents=True, exist_ok=True)
            hulls.to_file(out_path, driver="GPKG", layer="hig_huellen")
            print(f"Wrote {out_path}: {len(hulls):,} Hüllen", flush=True)

    return {
        "official_settlement_source": zoning["official_settlement_source"],
        "ferienhaus_tourismus_source": zoning["ferienhaus_tourismus_source"],
        "official_hig_source": zoning["official_hig_source"],
        "noe_pdf_750m_zones": noe_pdf,
        "hig_hulls_source": streusiedlung_mask,
        "bewohnt_einzellage_source": einzellage_mask,
        "nonresidential_hulls_source": masks[HULL_CLASS_INDUSTRIE] | masks[HULL_CLASS_UNBEWOHNT],
    }


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente der Layer-Stufe ``hig``.

    Die Vorgaben der Eingabeverzeichnisse zeigen auf die Prep-Ausgaben
    (``widmung``, ``noe_sekrop``, ``kataster``, ``adressen``), ``--address-dir`` auf
    den Rohpfad des Adressregisters. Die Schwellwerte haben die ``HIG_*``-
    Konstanten als Vorgabe.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        Die geparsten Argumente.
    """
    p = argparse.ArgumentParser(
        description=(
            "Layer-Stufe W2.1: Widmung und Häuser im Grünen - die sieben "
            "Checkpoints aus scripts/widmung_v2/02_build_hig_sources.py, "
            "Eingaben aus derived/prep/ statt output/kataster/ bzw. output/noe/ "
            "bzw. dem Widmungs-Zwischenstand."
        )
    )
    p.add_argument("--config", default="config.json")
    p.add_argument(
        "--zoning-dir",
        default=str(contract.PREP["widmung"]),
        help="Prep-Ausgabe von pipeline/prep/widmung.py (drei *_combined.gpkg-Bündel).",
    )
    p.add_argument(
        "--noe-dir",
        default=str(contract.PREP["noe_sekrop"]["b_vectorize"]),
        help="Prep-Ausgabe von pipeline/prep/noe_sekrop.py (pdf_750m_*.geojson).",
    )
    p.add_argument(
        "--dkm-parquet",
        default=str(KATASTER_PARQUET),
        help="Prep-Ausgabe von pipeline/prep/kataster/b_export_parquet.py. "
             "NIEMALS output/kataster/at_dkm_gst_nfl_epsg31287.geoparquet (Vorgängerprojekt-Altlast).",
    )
    p.add_argument("--address-dir", default=str(contract.RAW["adressen"]["address_dir"]))
    p.add_argument(
        "--cache-dir",
        default=str(contract.PREP["adressen"]),
        help="Prep-Ausgabe von pipeline/prep/adressen.py (BEV-Parquet-Caches).",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Ziel für die sieben eigenen Checkpoints + hig_huellen.gpkg. "
             "Default: pipeline.contract.DERIVED_LAYERS (derived/layers/).",
    )
    p.add_argument("--bbox", default=None, help="EPSG:31287 bbox minx,miny,maxx,maxy für Smoke-Tests")
    p.add_argument("--bl", action="append", default=None, help="Nur diese Bundesländer scannen (wiederholbar)")
    p.add_argument("--filter-buffer-m", type=float, default=HIG_FILTER_BUFFER_M)
    p.add_argument("--chain-m", type=float, default=HIG_CHAIN_M,
                   help="Verkettungsdistanz des Closings (Knie-Analyse: 200 m)")
    p.add_argument("--min-adressen", type=int, default=HIG_MIN_ADRESSEN,
                   help="Streusiedlungs-Schwelle: adressierte Objekte je bewohnter Hülle")
    p.add_argument("--address-radius-m", type=float, default=HIG_ADDRESS_RADIUS_M)
    p.add_argument("--garden-radius-m", type=float, default=HIG_GARDEN_RADIUS_M)
    p.add_argument("--max-footprint-m2", type=float, default=HIG_MAX_FOOTPRINT_M2)
    p.add_argument("--wohnanteil-min-share", type=float, default=HIG_WOHNANTEIL_MIN_SHARE)
    p.add_argument("--industrie-widmung-min-share", type=float, default=HIG_INDUSTRIE_WIDMUNG_MIN_SHARE)
    p.add_argument("--force-layers", action="store_true")
    p.add_argument("--skip-gpkg", action="store_true", help="Nur Raster-Checkpoints, kein hig_huellen.gpkg")
    return p.parse_args(argv)


def _params_tag(args: argparse.Namespace) -> dict[str, str]:
    """Wortgleich aus ``02_build_hig_sources.py:_params_tag()`` (Regel 4;
    seit W6.1 aus dem Repo entfernt; letzter Stand im Commit ``f1d00f7``,
    abrufbar mit ``git show f1d00f7:scripts/widmung_v2/02_build_hig_sources.py``)."""
    return {
        "HIG_FILTER_BUFFER_M": f"{args.filter_buffer_m:g}",
        "HIG_CHAIN_M": f"{args.chain_m:g}",
        "HIG_MIN_ADRESSEN": str(args.min_adressen),
        # Input-Provenienz: ohne diesen Tag würde ein Lauf gegen das falsche
        # Zoning-Verzeichnis (z. B. v1 statt v2) als "aktuell" durchgehen.
        "HIG_ZONING_DIR": str(args.zoning_dir),
        "HIG_ADDRESS_RADIUS_M": f"{args.address_radius_m:g}",
        "HIG_GARDEN_RADIUS_M": f"{args.garden_radius_m:g}",
        "HIG_MAX_FOOTPRINT_M2": f"{args.max_footprint_m2:g}",
        "HIG_WOHNANTEIL_MIN_SHARE": f"{args.wohnanteil_min_share:g}",
        "HIG_INDUSTRIE_WIDMUNG_MIN_SHARE": f"{args.industrie_widmung_min_share:g}",
    }


def main(argv: list[str] | None = None) -> None:
    """Baut die sieben Checkpoints der Layer-Stufe ``hig``.

    Ruft ``build_sources()`` über ``ensure_group_layers()`` auf. Die Gruppe wird
    übersprungen, wenn alle Checkpoints vorliegen und ihre Tags (Parameter aus
    ``_params_tag()`` und ``PREP_FINGERPRINT``) passen, außer mit
    ``--force-layers``.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.
    """
    args = parse_args(argv)
    with timed("load config/grid"):
        cfg = load_config(args.config)
        grid = load_grid(cfg, args.bbox)

    out_dir = Path(args.out_dir) if args.out_dir else contract.DERIVED_LAYERS
    runtime.ensure_dir(out_dir)

    tags = {**_params_tag(args), "PREP_FINGERPRINT": _fingerprint_tag()}

    def _tags_ok(existing: dict) -> bool:
        return all(existing.get(k) == v for k, v in tags.items())

    with timed("derived/update HiG checkpoint layers"):
        ensure_group_layers(
            out_dir,
            SOURCE_LAYER_NAMES,
            "Häuser-im-Grünen-Quellen v2",
            lambda: build_sources(cfg, grid, args, out_dir),
            grid,
            args.force_layers,
            extra_ok=_tags_ok,
            extra_tags=tags,
        )
    print(f"pipeline.layers.hig: {len(SOURCE_LAYER_NAMES)} Checkpoints in {out_dir}")


if __name__ == "__main__":
    main()
