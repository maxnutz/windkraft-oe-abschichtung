"""Validierung: vergleicht ein frisch finalisiertes TIF bandweise gegen die
Vergleichsbasis ``run1`` und bewertet jede Abweichung nach der Ampel aus
``docs/rewrite/PLAN.md`` §6 (Paket W3.2).

## Was dieses Werkzeug tut - und was nicht

Es **bewertet, es entscheidet nicht** (§6, Ablauf je Paket, Punkt 3: "Rot
hält an. Dann Rückfrage, keine eigenmächtige Fortsetzung"). Es schreibt
``docs/rewrite/abweichungen.tsv`` vollständig (alle Spalten außer
``ursache``, die trägt ein Mensch ein), meldet Rot als Rot und beendet den
Lauf mit Exit-Code 1, sobald mindestens ein Band Rot ist, es sei denn, die
Abweichung ist bereits **angenommen** (siehe Abschnitt "Der Nutzer hat
entschieden" unten). Es "repariert" nichts, es stuft nichts herab, und es
überschreibt nie ``run1`` selbst.

## Der Nutzer hat entschieden (Welle 4, Nachtrag zu W3.2; zweiter Wechsel W5.P2)

Am 08.09.2026 hat der Nutzer die Bodensee-Korrektur angenommen (Punkt 33,
PLAN.md §6, Abschnitt "Die Referenz hat sich am 08.09.2026 geändert").
Damit ist ``run1`` (``sha256 dc58b011…9e3df1``) nicht mehr das Ziel; Ziel
ist jetzt Bitgleichheit mit der **aktuellen Referenz**
(:data:`AKTUELLE_REFERENZ_SHA256`). ``run1`` bleibt bestehen und bleibt die
bandweise Vergleichsbasis für die Diagnose (§6-Randbedingung: es wird
nichts dupliziert, kein zweites TIF vorgehalten), verliert aber seine
Rolle als eingebaute Pass/Fail-Schranke:

1. **Schneller Weg (Regelfall).** Stimmt der ``sha256`` des frisch
   finalisierten TIFs mit :data:`AKTUELLE_REFERENZ_SHA256` überein, ist die
   Kette bitgleich zum angenommenen Stand - keine bandweise Prüfung nötig,
   Exit 0. Das ist zugleich der Determinismusnachweis, den Welle 5 braucht:
   ein voller Lauf aus Rohdaten, der denselben Hash reproduziert.
2. **Diagnoseweg (bei Abweichung vom aktuellen Stand).** Stimmt der Hash
   nicht überein, läuft die bisherige bandweise Prüfung gegen ``run1``.
   Dabei gilt: eine Abweichung, die in ``docs/rewrite/abweichungen.tsv``
   bereits für dasselbe Paket und denselben Band mit einer ``ursache``
   geführt wird, die das Wort "angenommen" enthält, wird als
   :data:`AMPEL_AKZEPTIERT` eingestuft statt als Rot erzwungen zu werden -
   der Lauf scheitert daran nicht mehr. Eine **neue**, bislang nicht im
   Register geführte Abweichung ist davon nicht betroffen und bleibt Rot
   (siehe :func:`write_register`).

Der frühere Satz an dieser Stelle - "alle 38 Bänder müssen bitgleich zu
``run1`` sein" - stammte aus der Zeit vor der Nutzerentscheidung und ist
seit dem 08.09.2026 falsch: neun der mittlerweile 44 Bänder (26, 29-36) weichen
**bewusst und angenommen** von ``run1`` ab. Die Bitgleichheits-Anforderung
gilt unverändert, nur eben gegen die aktuelle Referenz, nicht mehr gegen
``run1`` (siehe Abschnitt "Referenzbänder 37/38" weiter unten, der davon
unberührt bleibt: 37/38 kennen nach wie vor keinen Grün-/Gelb-Korridor).

## Vergleichsbasis

``osm_wka_distance_zones_widmung_v2_run1.tif`` - das letzte Ergebnis DIESES
Repos aus der alten Kette, nicht die aus dem Vorgängerprojekt kopierte
Referenz-TIF (§6, Begründung dort: zwischen ``run1`` und der Referenz
bestehen bereits dokumentierte Alt-Abweichungen, die sich sonst mit den
neuen vermischen würden).

**Seit W6.1** liegt ``run1`` nicht mehr im Repo (``output/`` wurde nach
``~/Documents/master_windkraft/archiv/`` herausbewegt, siehe dessen
README.md) und ist deshalb kein RAW-/PREP-/LAYERS-/PRODUCTS-Pfad in
``pipeline.contract`` mehr, sondern ``contract.RUN1_TIF`` - optional, aus
der Umgebungsvariable ``ABSCHICHTUNG_RUN1`` aufgelöst, Vorgabe ``None``
(dasselbe Muster wie ``ABSCHICHTUNG_ALTREPO`` in
``tests/test_distance_engine_equivalence.py``). Ist ``run1`` nicht
auflösbar, überspringt dieses Werkzeug den bandweisen Vergleich mit einer
sichtbaren Meldung und führt nur noch die Prüfsummenprüfung gegen die
aktuelle Referenz (:data:`AKTUELLE_REFERENZ_SHA256`) aus - wer ``run1`` aus
dem Archiv zurückholt und ``ABSCHICHTUNG_RUN1`` setzt, bekommt wieder
dasselbe Register wie zuvor.

## Drei Kennzahlen je Band (§6)

1. Abweichende Pixel, absolut.
2. Anteil an den **gesetzten Pixeln des Bandes**.
3. Größte zusammenhängende Abweichungsfläche in Hektar (4er-Nachbarschaft,
   wie ``calc.abschichtung_common.min_area_filter`` - Aug-2026-
   Entscheidung: nur Kantenkontakt verbindet).

### Die Nenner-Frage (Punkt aus dem Auftrag, hier entschieden)

§6 sagt wörtlich "Anteil an den **gesetzten Pixeln des Bandes**" - nicht
"an der Gesamtzellzahl". W2.4 hat seinerzeit (PLAN.md §13.9,
FORTSCHRITT.md) gegen die Gesamtzellzahl des Rasters (336.038.001 Zellen)
gerechnet und kam für ``geography_water_bodies`` auf 0,16 %. Dieses
Werkzeug folgt dem Wortlaut von §6: Nenner ist die Zahl der Zellen
!= 0 im **Referenzband** (``run1`` - die im Vergleich ausgezeichnete,
unveränderliche Seite), nicht die Gesamtzellzahl des Rasters. Für
``geography_water_bodies`` ergibt das einen höheren Prozentsatz als
0,16 % (der Nenner ist kleiner als 336.038.001 - ein See ist immer nur
ein Bruchteil der Landesfläche). Beide Zahlen werden gemessen und im Bericht
zu diesem Paket genannt, aber nur die §6-Variante (gesetzte Pixel) landet
in ``anteil_prozent``.

## Gruppen und Ampel

Die Ampeltabelle unterscheidet zwei Gruppen nach ``band_role()`` aus
``calc.band_manifest``:

- ``bedingung`` (Bänder 1-26 sowie 39-41, Quellen und Puffer): Anteil
  **und** größte Fläche müssen beide unter dem jeweiligen Schwellwert
  liegen.
- ``aggregat_kategorie`` / ``aggregat_gesamt`` / ``verfuegbarkeit_roh`` /
  ``verfuegbarkeit_bereinigt`` / ``unschaerfe`` (Bänder 27-36 sowie 42-44,
  Aggregate und Verfügbarkeit): ein einziges Flächenbudget in km², absolut
  (nicht Prozent) - "die Fläche zählt, nicht die Pixelzahl" (§6). Die
  Staatsfläche 83.921 km² aus der Ampeltabelle ist dabei nur Kontext für
  die Größenordnung, kein Nenner.

Die Zuordnung läuft über ``band_role()`` (``calc.band_manifest``), nicht
über einen hartkodierten Indexbereich - Bänder 39-44 (Schema 2.2.0, W7.1)
fallen deshalb ohne Sonderfall in dieselben zwei Gruppen wie 1-26/27-36
(``band_role()`` liefert für 39-41 ``bedingung``, für 42-44
``aggregat_kategorie`` - siehe dortiges Modul).

### Referenzbänder 37/38 - eine zweite Lücke in der Ampeltabelle, hier entschieden

Die Ampeltabelle in §6 hat nur zwei Spalten (1-26, 27-36; seit Schema
2.2.0 zusätzlich 39-41 bzw. 42-44, siehe oben). Für die Referenzbänder
37/38 gibt es keine dritte Spalte - ohne Sonderfall (die frühere
Steiermark-SAPRO-Ausnahme für Band 37 wurde von W1.7 ersatzlos gestrichen,
weil die zugrunde liegende inhaltliche Änderung sich als nicht existent
erwiesen hat). Entscheidung dieses Werkzeugs: jede Abweichung auf einem
Referenzband ist automatisch **Rot**, unabhängig von Pixelzahl oder
Fläche - es gibt für diese beiden Bänder keinen Grün-/Gelb-Korridor, weil
die Ampeltabelle keinen definiert. Das gilt unverändert auch nach der
Nutzerentscheidung vom 08.09.2026 (Abschnitt "Der Nutzer hat entschieden"
oben): 37/38 gehören nicht zu den neun angenommenen Bändern (26, 29-36),
für sie bleibt Bitgleichheit mit der aktuellen Referenz ohne Ausnahme
verlangt - der frühere, hier gestrichene Satz "alle 38 Bänder müssen
bitgleich zu ``run1`` sein" bezog sich auf ``run1`` als *Ziel*; das ist
seit der Nutzerentscheidung nicht mehr richtig (siehe oben), die
Referenzband-Regel selbst schon.

## §13.9-Wächter: der zehnte Fall ist ein Fehler

PLAN.md §13.9 (Regel 8) erlaubt Abweichungen **ausschließlich** in Bändern,
die transitiv aus einer bereits geprüften und angenommenen Ursache gespeist
werden - welche das je Ursache sind, berechnet
``calc.band_manifest`` VOR der Messung aus ``abgeleitet_von`` und
legt es im Manifest als eigenen Top-Level-Schlüssel ab, einen je Ursache
(``geography_water_bodies_wirkungspfad`` für die Bodensee-Korrektur, Punkt
33; ``dkm_geoparquet_wirkungspfad`` für den Wegfall adressloser
DKM-Großflächen, Punkt 34, seit W5.P5) - siehe dortiges Modul, Regel 8:
"vorher genannt, dann gemessen".

Dieses Werkzeug liest **jeden** Schlüssel, der im Manifest der
``..._wirkungspfad``-Konvention folgt (Namenssuffix, keine feste Liste von
Schlüsselnamen - dieselbe Erkennung wie
``pipeline/export/dashboard.py:_impact_path_keys()``), bildet die
Vereinigung ihrer Bandlisten und behandelt jede Abweichung auf einem Band
AUSSERHALB dieser Vereinigung als Fehler: erzwungenes Rot, unabhängig von
der berechneten Ampel-Farbe, mit eigenem Ursachenvermerk im Register. Jede
Zeile wird damit gegen den Wirkungspfad IHRER EIGENEN Ursache geprüft, nicht
pauschal gegen einen einzigen - ein Band, das in keinem der bekannten Pfade
auftaucht, bleibt unerwartet, ganz gleich, wie viele Ursachen das Manifest
inzwischen kennt. Ein künftiger dritter Wirkungspfad braucht an dieser
Stelle keine Anpassung, solange sein Manifest-Schlüssel derselben
Namenskonvention folgt.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import contract  # noqa: E402
from pipeline.prep import admin as prep_admin  # noqa: E402
from calc.band_manifest import (  # noqa: E402
    ROLE_BEDINGUNG,
    ROLE_REFERENZ,
    band_role,
    manifest_path_for,
)

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

# Optional (siehe Moduldocstring, "Vergleichsbasis") - None, solange
# ABSCHICHTUNG_RUN1 nicht gesetzt ist.
REFERENCE_TIF = contract.RUN1_TIF

# Die aktuelle Referenz (PLAN.md §6, "Die Referenz hat sich am 08.09.2026
# geändert"): der sha256 des TIFs, das der Nutzer am 08.09.2026 mit der
# Bodensee-Korrektur (Punkt 33) als Ziel angenommen hat -
# ``pipeline.contract.PRODUCTS["abschichtung_tif"]``, sofern es bitgleich
# ist. Es wird dafür bewusst KEIN zweites TIF vorgehalten (Plattenplatz,
# Randbedingung dieses Auftrags) - der Nachweis ist ein Hash-Vergleich,
# kein Datei-Duplikat. Derselbe Wert steht auch in
# ``tests/test_referenz_tif.py`` (dort ``REFERENZ_SHA256``) - zwei Stellen,
# bewusst nicht in ein gemeinsames Modul gezogen, weil Produktionscode
# nicht von einem Testmodul abhängen soll; wer den einen Wert ändert, muss
# den anderen mitziehen.
#
# Dritter Wechsel (09.09.2026, W7.1, Neuzuschnitt "Layer-Struktur v4"):
# Hüllen-Zuschnitt gegen official_wind_zoning (Band 38, Geometriefehler
# behoben) plus sechs angehängte Bänder 39-44 (Schema 2.2.0). Der vorherige
# Wert ``fb57c41dca0642225a8115e3ed95297ede47b56d56c00fdddf8caa445e232c30``
# bleibt in ``docs/rewrite/FORTSCHRITT.md``/``PLAN.md`` als historischer
# Zeuge dokumentiert, ist aber - wie zuvor ``run1`` - kein Ziel mehr.
#
# Vierter Wechsel (09.09.2026, W7.5, Personenseilbahnen enger gefasst):
# PEOPLE_CARRYING_AERIALWAY_TYPES (calc/abschichtung_common.py) auf gondola/
# cable_car/chair_lift/mixed_lift eingeengt - Nutzerentscheidung 09.09.2026,
# angenommen. Betrifft Bänder 10/11/12/13/17/27/30/31/32/33-36 (siehe
# cableway_typ_wirkungspfad im Manifest, calc/band_manifest.py) - Kopfzahl
# des Bandvergleichs: +9,8763 km² bei available_cleaned_min_10ha (Band 32).
# Der vorherige Wert
# ``a905c0563696c8af4a2fd3b1407521c81ccdc3c0b373d14ce8a6429196ab5c9f``
# bleibt ebenso als historischer Zeuge dokumentiert.
#
# Fünfter Wechsel (09.09.2026, W7.6, Kategorie-/Familien-/Stufentexte, drei
# neue Geografie-Schwellenwerte in ``parameters``): die Datei-Prüfsumme
# ändert sich, die Pixel nicht. pipeline/finalize.py schreibt fünf neue
# Schlüssel in ``tags`` (SLOPE_MAX_DEG, ELEVATION_MAX_M, PD_MIN_W_M2,
# PD_MIN_REFERENCE_HEIGHT_M, PD_MIN_AT_150M_W_M2) - dieselben Werte landen
# als GDAL-Datei-Tags im TIF (siehe ``compose_exclusion_geotiff()``s
# ``update_tags``), was die Datei-Bytes ändert, obwohl kein Band abweicht.
# Nachgewiesen durch zwei unabhängige Läufe: (1) dieselben
# ``derived/layers/``-Checkpoints mit dem UNVERÄNDERTEN
# ``pipeline/finalize.py`` (git stash) reproduzieren exakt den vorherigen
# Wert (Determinismus bestätigt); (2) ein bandweiser Vergleich (rasterio,
# alle 44 Bänder einzeln gelesen) zwischen diesem Lauf und dem W7.6-Lauf
# ergibt ``diff=0`` für jedes Band, ``a.descriptions == b.descriptions``,
# und der einzige Unterschied in den Datei-Tags sind exakt die fünf neu
# hinzugekommenen Schlüssel (siehe ``tests/test_referenz_tif.py``, dortiger
# Kommentar "Fünfter Wechsel", derselbe Nachweis). Der vorherige Wert
# ``99d522239dc499b833cab3080c810b9568ac3078a66b81a470e37228b55a96ac``
# bleibt ebenso als historischer Zeuge dokumentiert.
AKTUELLE_REFERENZ_SHA256 = "c1809c4cf9cb243d420efc949ff5543130ef3c5a75db9e948d2ea53c94416f37"

REGISTER_PATH = contract.ROOT / "docs" / "rewrite" / "abweichungen.tsv"

REGISTER_COLUMNS = [
    "paket",
    "band_nr",
    "band_name",
    "pixel_abs",
    "anteil_prozent",
    "groesste_flaeche_ha",
    "schwerpunkt_bundesland",
    "ampel",
    "ursache",
]

URSACHE_PLATZHALTER = "TODO: von Hand eintragen"
URSACHE_UNERWARTET = (
    "FEHLER: in keinem *_wirkungspfad des Manifests - verstoesst "
    "gegen PLAN.md Paragraph 13.9, vor Fortsetzung klaeren"
)
# W7.4: Default-ursache fuer ein Band ohne Gegenstueck in run1 - kein TODO
# (URSACHE_PLATZHALTER waere hier irrefuehrend, es ist nichts offen zu
# klaeren), sondern eine Aussage, die den fehlenden Vergleich benennt.
URSACHE_OHNE_GEGENSTUECK = (
    "kein Gegenstueck in run1 (neues Band seit Schema 2.2.0/W7.1)"
)

# Konvention (siehe Moduldocstring, "Der Nutzer hat entschieden"): eine
# ursache, die dieses Wort enthaelt, markiert eine vom Nutzer bereits
# geprüfte und angenommene Abweichung - z. B. "vom Nutzer am 08.09.2026
# angenommen (Punkt 33)", wie es die neun Zeilen aus W3.1 in
# docs/rewrite/abweichungen.tsv bereits tragen. Gross-/Kleinschreibung
# spielt keine Rolle.
URSACHE_AKZEPTIERT_MARKER = "angenommen"

# ---------------------------------------------------------------------------
# Ampel-Schwellen, PLAN.md §6
# ---------------------------------------------------------------------------

# Bänder 1-26 sowie 39-41 (bedingung, per band_role()): BEIDE Bedingungen
# müssen erfüllt sein.
GRUEN_ANTEIL_PROZENT = 0.01
GRUEN_FLAECHE_HA = 1.0
GELB_ANTEIL_PROZENT = 0.1
GELB_FLAECHE_HA = 25.0

# Bänder 27-36 sowie 42-44 (Aggregate/Verfügbarkeit, per band_role()):
# absolutes Flächenbudget in km².
GRUEN_FLAECHE_KM2 = 1.0
GELB_FLAECHE_KM2 = 10.0

AMPEL_BITGLEICH = "bitgleich"
AMPEL_GRUEN = "gruen"
AMPEL_GELB = "gelb"
AMPEL_ROT = "rot"
# Eigener Status, bewusst nicht AMPEL_GRUEN: die automatische Ampel misst
# nur die Groessenordnung, nicht den Entscheidungsstatus. Ein Band, das
# ohne Annahme rot waere (z. B. 27,58 % Anteil bei geography_water_bodies),
# bleibt auch nach der Annahme eine grosse Abweichung - nur eine, die der
# Nutzer bereits geprueft und akzeptiert hat. Beides in "gruen" zu
# verschmelzen wuerde die Groessenordnung verschleiern; siehe write_register().
AMPEL_AKZEPTIERT = "akzeptiert"
# W7.4: ein Band des neuen TIFs, das in run1 (namentlich, siehe
# measure_bands()) keine Entsprechung hat - z.B. die sechs seit Schema
# 2.2.0/W7.1 angehaengten Baender 39-44. Kein Diff, also weder "bitgleich"
# noch gruen/gelb/rot; eine eigene, ausdrueckliche Aussage ("kein
# Gegenstueck"), damit diese Baender im Register sichtbar bleiben statt als
# gruen durchzugehen oder ganz zu fehlen.
AMPEL_OHNE_GEGENSTUECK = "ohne_gegenstueck"


@dataclass
class BandResult:
    band_nr: int
    band_name: str
    role: str
    pixel_abs: int
    gesetzte_pixel_referenz: int
    anteil_prozent: float  # Nenner: gesetzte Pixel im Referenzband (§6-Wortlaut)
    anteil_prozent_kontrolle: float  # Nenner: Gesamtzellzahl (W2.4-Konvention) - nur zur Kontrolle
    groesste_flaeche_ha: int | float
    flaeche_km2: float
    schwerpunkt_bundesland: str
    ampel: str = AMPEL_BITGLEICH
    unerwartet: bool = False
    ohne_gegenstueck: bool = False  # W7.4: kein Gegenstueck in run1 (siehe AMPEL_OHNE_GEGENSTUECK)


# ---------------------------------------------------------------------------
# Bundesland-Zuordnung
# ---------------------------------------------------------------------------

def _build_bundesland_code_raster(grid: dict) -> tuple[np.ndarray, dict[int, str]]:
    """Rasterisiert die 9 Bundesland-Polygone einmal auf das Zielgitter.

    Liest ``derived/prep/admin/bundesland_masken.gpkg`` (dieselbe Datei, die
    ``pipeline.layers.geo._build_valid_area_mask()`` für die Staatsgebiets-
    maske liest) - nur lesend, wie im Auftrag verlangt. Code 0 = außerhalb
    aller 9 Bundesländer (Rasterfenster reicht über die Staatsgrenze
    hinaus, siehe geography_water_bodies/Bodensee).
    """
    path = contract.PREP["admin"] / prep_admin.BUNDESLAND_MASKEN_FILENAME
    gdf = gpd.read_file(path)
    codes = {i + 1: name for i, name in enumerate(gdf["BL"].tolist())}
    arr = rasterize(
        ((geom, code) for code, geom in zip(codes.keys(), gdf.geometry, strict=True)),
        out_shape=grid["shape"],
        transform=grid["transform"],
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    return arr, codes


def _schwerpunkt_bundesland(diff_mask: np.ndarray, bl_raster: np.ndarray, codes: dict[int, str]) -> str:
    """Bundesland mit den meisten abweichenden Zellen - der Schwerpunkt,
    nicht bloß irgendein betroffenes Land."""
    hit_codes = bl_raster[diff_mask]
    if hit_codes.size == 0:
        return ""
    counts = np.bincount(hit_codes, minlength=max(codes) + 1)
    counts[0] = 0  # außerhalb aller Bundesländer zählt nicht als Schwerpunkt
    if not counts.any():
        return "(ausserhalb aller Bundeslaender)"
    return codes[int(counts.argmax())]


# ---------------------------------------------------------------------------
# Flächenanalyse
# ---------------------------------------------------------------------------

def _largest_connected_component_px(diff_mask: np.ndarray) -> int:
    """Größte zusammenhängende Abweichungsfläche in Pixeln, 4er-Nachbarschaft
    - dieselbe Konvention wie ``abschichtung_common.min_area_filter()``."""
    if not diff_mask.any():
        return 0
    structure = ndimage.generate_binary_structure(2, 1)
    labels, n_labels = ndimage.label(diff_mask, structure=structure)
    if n_labels == 0:
        return 0
    counts = np.bincount(labels.ravel())
    counts[0] = 0
    return int(counts.max())


# ---------------------------------------------------------------------------
# Messung
# ---------------------------------------------------------------------------

def measure_bands(new_tif: Path, reference_tif: Path) -> list[BandResult]:
    """Vergleicht ``new_tif`` bandweise gegen ``reference_tif`` (``run1``).

    **Seit W7.4 vergleichbare Bandzahl ist keine Voraussetzung mehr.** Seit
    Schema 2.2.0 (W7.1) traegt das TIF mehr Baender als ``run1`` - das ist
    der Regelfall, sobald ein Paket neue Baender anhaengt, kein Fehler. Statt
    bei abweichender Bandzahl/-liste hart abzubrechen (frueheres Verhalten,
    ``ValueError`` "Bandzahl weicht ab" bzw. "Bandnamen/-reihenfolge weichen
    ab"), vergleicht diese Funktion nur die **ueberlappenden** Baender und
    weist die uebrigen Baender des neuen TIFs ausdruecklich als
    :data:`AMPEL_OHNE_GEGENSTUECK` aus (``BandResult.ohne_gegenstueck =
    True``) - eine eigene Aussage, kein "bitgleich" (0 Diff) und kein
    Fehler, siehe :func:`classify` und :func:`write_register`.

    **Zuordnung ueber den Bandnamen, nicht ueber den Index** (Auftrag W7.4),
    sofern beide Seiten vollstaendige, eindeutige Namen hergeben - beide
    TIFs tragen ihre Bandnamen als GDAL-``Description`` je Band (``run1``
    ebenso wie das frisch finalisierte TIF; siehe Bericht zu W7.4: eine
    ``.bands.json``-Sidecar existiert fuer ``run1`` zwar auch, ist aber nicht
    Datenquelle dieser Funktion, weil die im TIF selbst eingebetteten
    Descriptions bereits vollstaendig und - geprueft - deckungsgleich mit der
    Sidecar sind). Nur wenn Namen auf einer Seite fehlen oder doppelt sind,
    faellt die Funktion sichtbar (Meldung auf stderr) auf einen reinen
    Indexabgleich der ersten ``min(new_count, ref_count)`` Baender zurueck -
    niemals stillschweigend.
    """
    with rasterio.open(new_tif) as new_src, rasterio.open(reference_tif) as ref_src:
        if new_src.shape != ref_src.shape or new_src.transform != ref_src.transform:
            raise ValueError(
                f"Gitter weicht ab: {new_tif} shape={new_src.shape} transform={new_src.transform} vs. "
                f"{reference_tif} shape={ref_src.shape} transform={ref_src.transform}."
            )

        grid = {"shape": new_src.shape, "transform": new_src.transform, "crs": new_src.crs}
        cell_area_m2 = abs(float(grid["transform"].a) * float(grid["transform"].e))
        total_cells = int(grid["shape"][0]) * int(grid["shape"][1])
        band_names = list(new_src.descriptions)
        ref_names = list(ref_src.descriptions)

        # Namensabgleich nur, wenn beide Seiten vollstaendige UND eindeutige
        # Namen hergeben - eine leere oder doppelte Beschreibung macht einen
        # Namensabgleich bedeutungslos (welchem Band entspraeche ""?).
        namen_nutzbar = (
            all(band_names)
            and all(ref_names)
            and len(set(ref_names)) == len(ref_names)
        )
        if namen_nutzbar:
            ref_index_by_name = {name: idx for idx, name in enumerate(ref_names, start=1)}
            print(
                f"measure_bands(): Zuordnung ueber den Bandnamen ({len(band_names)} Baender "
                f"in {new_tif.name}, {len(ref_names)} in {reference_tif.name})."
            )
        else:
            ref_index_by_name = None
            min_count = min(len(band_names), len(ref_names))
            print(
                "measure_bands(): Namensabgleich nicht moeglich (leere oder doppelte "
                f"Bandbeschreibungen auf mindestens einer Seite) - falle zurueck auf die "
                f"ersten {min_count} Indizes.",
                file=sys.stderr,
            )

        bl_raster: np.ndarray | None = None
        bl_codes: dict[int, str] | None = None

        results: list[BandResult] = []
        for i, raw_name in enumerate(band_names, start=1):
            # Im Indexabgleich-Fallback kann raw_name None sein (GDAL-
            # Description nicht gesetzt) - ab hier durchgaengig als "" statt
            # None fuehren (BandResult.band_name, band_role(), Register).
            name = raw_name or ""
            role = band_role(name)

            if ref_index_by_name is not None:
                ref_idx = ref_index_by_name.get(name)
            else:
                ref_idx = i if i <= min_count else None

            if ref_idx is None:
                # Kein Gegenstueck in run1 (Auftrag W7.4) - ausdruecklich
                # ausweisen statt als "bitgleich" zu verschlucken oder
                # abzubrechen. Kennzahlen sind hier rein informativ (eigener
                # Bestand des neuen Bandes, kein Diff moeglich) - bewusst
                # OHNE Bundesland-Zuordnung: die teure Rasterisierung der
                # Bundeslandmaske (_build_bundesland_code_raster()) lohnt
                # sich fuer einen "Schwerpunkt der Abweichung" - hier gibt es
                # keine Abweichung, nur ein neues Band, also keinen
                # sinnvollen Schwerpunkt.
                a = new_src.read(i)
                mask = a != 0
                gesetzt = int(mask.sum())
                anteil_kontrolle = gesetzt / total_cells * 100.0 if total_cells else 0.0
                groesste_px = _largest_connected_component_px(mask) if gesetzt else 0
                results.append(
                    BandResult(
                        band_nr=i,
                        band_name=name,
                        role=role,
                        pixel_abs=gesetzt,
                        gesetzte_pixel_referenz=0,
                        anteil_prozent=anteil_kontrolle,
                        anteil_prozent_kontrolle=anteil_kontrolle,
                        groesste_flaeche_ha=groesste_px * cell_area_m2 / 10_000.0,
                        flaeche_km2=gesetzt * cell_area_m2 / 1_000_000.0,
                        schwerpunkt_bundesland="",
                        ampel=AMPEL_OHNE_GEGENSTUECK,
                        ohne_gegenstueck=True,
                    )
                )
                del a, mask
                continue

            a = new_src.read(i)
            b = ref_src.read(ref_idx)
            diff_mask = a != b
            pixel_abs = int(diff_mask.sum())

            if pixel_abs == 0:
                results.append(
                    BandResult(
                        band_nr=i,
                        band_name=name,
                        role=role,
                        pixel_abs=0,
                        gesetzte_pixel_referenz=int((b != 0).sum()),
                        anteil_prozent=0.0,
                        anteil_prozent_kontrolle=0.0,
                        groesste_flaeche_ha=0,
                        flaeche_km2=0.0,
                        schwerpunkt_bundesland="",
                        ampel=AMPEL_BITGLEICH,
                    )
                )
                del a, b, diff_mask
                continue

            if bl_raster is None:
                bl_raster, bl_codes = _build_bundesland_code_raster(grid)

            gesetzte_pixel_referenz = int((b != 0).sum())
            anteil_prozent = (
                pixel_abs / gesetzte_pixel_referenz * 100.0 if gesetzte_pixel_referenz else float("inf")
            )
            anteil_prozent_kontrolle = pixel_abs / total_cells * 100.0
            groesste_px = _largest_connected_component_px(diff_mask)
            groesste_flaeche_ha = groesste_px * cell_area_m2 / 10_000.0
            flaeche_km2 = pixel_abs * cell_area_m2 / 1_000_000.0
            schwerpunkt = _schwerpunkt_bundesland(diff_mask, bl_raster, bl_codes)

            results.append(
                BandResult(
                    band_nr=i,
                    band_name=name,
                    role=role,
                    pixel_abs=pixel_abs,
                    gesetzte_pixel_referenz=gesetzte_pixel_referenz,
                    anteil_prozent=anteil_prozent,
                    anteil_prozent_kontrolle=anteil_prozent_kontrolle,
                    groesste_flaeche_ha=groesste_flaeche_ha,
                    flaeche_km2=flaeche_km2,
                    schwerpunkt_bundesland=schwerpunkt,
                )
            )
            del a, b, diff_mask

    return results


# ---------------------------------------------------------------------------
# Ampel-Einstufung
# ---------------------------------------------------------------------------

def _erlaubte_baender_aus_manifest(manifest: dict) -> set[str]:
    """Vereinigung aller ``..._wirkungspfad``-Listen im Manifest - eine je
    bereits geprueften/angenommenen Ursache (Moduldocstring, "§13.9-
    Waechter"). Schluessel werden ueber das Namenssuffix gefunden, nicht
    ueber eine feste Liste ("geography_water_bodies_wirkungspfad",
    "dkm_geoparquet_wirkungspfad", ...) - dieselbe Konvention wie
    ``pipeline/export/dashboard.py:_impact_path_keys()``, hier lokal
    dupliziert statt importiert: beide Module lesen dasselbe Manifest-
    Schema-Vokabular, aber unabhaengig voneinander (Verify- vs.
    Validierungsstufe, siehe PLAN.md §7), keine neue Kopplung zwischen den
    beiden fuer zwei Zeilen Code."""
    erlaubt: set[str] = set()
    for key, value in manifest.items():
        if key.endswith("_wirkungspfad") and isinstance(value, list):
            erlaubt.update(value)
    return erlaubt


def classify(result: BandResult, erlaubte_baender: set[str]) -> tuple[str, bool]:
    """Reine Funktion der Kennzahlen - kein Dateizugriff, gut testbar.

    Gibt (ampel, unerwartet) zurueck. ``unerwartet`` True heisst: dieses
    Band weicht ab, ist aber in KEINER der vom Manifest vorab genannten
    ``..._wirkungspfad``-Listen (PLAN.md §13.9) - ``erlaubte_baender`` ist
    hier bereits die Vereinigung aller Ursachen (siehe main()), die Funktion
    selbst kennt keine einzelne Ursache. Erzwingt Rot unabhaengig von der
    sonst berechneten Farbe.
    """
    if result.ohne_gegenstueck:
        # W7.4: kein Diff moeglich (kein Gegenstueck in run1) - eigener
        # Status, umgeht die Ampeltabelle UND den §13.9-Waechter (der
        # pruefte nur, ob eine ABWEICHUNG erwartet war; hier gibt es gar
        # keine Abweichung, nur ein neues Band).
        return AMPEL_OHNE_GEGENSTUECK, False

    if result.pixel_abs == 0:
        return AMPEL_BITGLEICH, False

    unerwartet = result.band_name not in erlaubte_baender

    if result.role == ROLE_BEDINGUNG:
        if result.anteil_prozent <= GRUEN_ANTEIL_PROZENT and result.groesste_flaeche_ha <= GRUEN_FLAECHE_HA:
            ampel = AMPEL_GRUEN
        elif result.anteil_prozent <= GELB_ANTEIL_PROZENT and result.groesste_flaeche_ha <= GELB_FLAECHE_HA:
            ampel = AMPEL_GELB
        else:
            ampel = AMPEL_ROT
    elif result.role == ROLE_REFERENZ:
        # Referenzbaender kennen laut Ampeltabelle keinen Gruen-/Gelb-
        # Korridor; §6 verlangt Bitgleichheit ohne Ausnahme. Siehe
        # Moduldocstring, Abschnitt "Referenzbaender 37/38".
        ampel = AMPEL_ROT
    else:
        if result.flaeche_km2 <= GRUEN_FLAECHE_KM2:
            ampel = AMPEL_GRUEN
        elif result.flaeche_km2 <= GELB_FLAECHE_KM2:
            ampel = AMPEL_GELB
        else:
            ampel = AMPEL_ROT

    if unerwartet:
        ampel = AMPEL_ROT

    return ampel, unerwartet


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def _load_existing_register(path: Path) -> dict[tuple[str, str], list[str]]:
    """Liest das bestehende Abweichungsregister.

    Parameters
    ----------
    path : Path
        Pfad der TSV-Datei (Vorgabe ``docs/rewrite/abweichungen.tsv``).

    Returns
    -------
    dict of (str, str) to list of str
        Zeilen, geschlüsselt nach ``(paket, band_name)``. Leer, wenn die Datei
        nicht existiert. Zeilen mit falscher Spaltenzahl werden übergangen.

    Raises
    ------
    ValueError
        Wenn der Kopf der Datei nicht ``REGISTER_COLUMNS`` entspricht.
    """
    if not path.exists():
        return {}
    rows: dict[tuple[str, str], list[str]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        if header != REGISTER_COLUMNS:
            raise ValueError(
                f"{path} hat einen unerwarteten Kopf {header!r} - erwartet {REGISTER_COLUMNS!r}. "
                "Von Hand pruefen statt automatisch ueberschreiben."
            )
        for row in reader:
            if len(row) != len(REGISTER_COLUMNS):
                continue
            rows[(row[0], row[2])] = row
    return rows


def _ist_akzeptiert(ursache: str) -> bool:
    """True, wenn eine ``ursache``-Zeile aus dem Register den Nutzer bereits
    als geprueft/angenommen ausweist (siehe URSACHE_AKZEPTIERT_MARKER)."""
    return URSACHE_AKZEPTIERT_MARKER in ursache.lower()


def write_register(path: Path, paket: str, results: list[BandResult], erlaubte_baender: set[str]) -> list[BandResult]:
    """Schreibt/aktualisiert das Register. Zeilen anderer Pakete bleiben
    unveraendert stehen; Zeilen desselben Pakets werden aus der aktuellen
    Messung neu gebaut - ein von Hand eingetragener ``ursache``-Text bleibt
    dabei erhalten, solange er nicht der Platzhalter ist.

    **Angenommene Abweichungen (PLAN.md §6, "Die Referenz hat sich am
    08.09.2026 geändert"):** trägt die bestehende Zeile fuer (``paket``,
    Band) bereits eine ``ursache``, die laut :func:`_ist_akzeptiert` als vom
    Nutzer angenommen gilt, wird die automatisch berechnete Ampel nicht als
    Rot ins Register geschrieben, sondern als :data:`AMPEL_AKZEPTIERT` -
    ausser das Band ist gleichzeitig "unerwartet" (§13.9-Waechter,
    ausserhalb des erlaubten Wirkungspfads): dieser Fall bleibt immer Rot,
    eine alte ``ursache`` darf ihn nicht stillschweigend entschaerfen. Eine
    Abweichung, die bisher gar keine Zeile im Register hat, ist per
    Definition nicht angenommen und wird ganz normal (moeglicherweise Rot)
    eingestuft.

    Gibt die Liste der fuer dieses Paket geschriebenen BandResult-Objekte
    zurueck (mit gesetzter ``ampel``/``unerwartet``), fuer den Bericht auf
    der Kommandozeile.
    """
    existing = _load_existing_register(path)
    other_paket_rows = {key: row for key, row in existing.items() if key[0] != paket}

    # W7.4: Baender ohne Gegenstueck in run1 muessen im Register erscheinen,
    # auch wenn sie (informativ) 0 gesetzte Zellen haetten - deshalb nicht
    # ueber den pixel_abs-Filter ausgeschlossen wie bitgleiche Baender.
    deviating = [r for r in results if r.pixel_abs > 0 or r.ohne_gegenstueck]
    classified: list[BandResult] = []
    new_rows: dict[tuple[str, str], list[str]] = {}
    for r in deviating:
        ampel, unerwartet = classify(r, erlaubte_baender)

        key = (paket, r.band_name)
        prior = existing.get(key)
        akzeptiert = prior is not None and _ist_akzeptiert(prior[-1])

        if akzeptiert and not unerwartet and ampel == AMPEL_ROT:
            ampel = AMPEL_AKZEPTIERT

        r.ampel = ampel
        r.unerwartet = unerwartet
        classified.append(r)

        if unerwartet:
            ursache = URSACHE_UNERWARTET
        elif prior is not None and prior[-1] and prior[-1] not in (URSACHE_PLATZHALTER, URSACHE_UNERWARTET):
            ursache = prior[-1]
        elif r.ohne_gegenstueck:
            ursache = URSACHE_OHNE_GEGENSTUECK
        else:
            ursache = URSACHE_PLATZHALTER

        new_rows[key] = [
            paket,
            str(r.band_nr),
            r.band_name,
            str(r.pixel_abs),
            f"{r.anteil_prozent:.4f}",
            f"{r.groesste_flaeche_ha:.4f}",
            r.schwerpunkt_bundesland,
            r.ampel,
            ursache,
        ]

    combined = {**other_paket_rows, **new_rows}
    ordered = sorted(combined.values(), key=lambda row: (row[0], int(row[1])))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(REGISTER_COLUMNS)
        writer.writerows(ordered)

    return classified


# ---------------------------------------------------------------------------
# Hash-Kurzweg gegen die aktuelle Referenz (siehe Moduldocstring)
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    """``sha256`` einer Datei, blockweise gelesen.

    Parameters
    ----------
    path : Path
        Zu hashende Datei.

    Returns
    -------
    str
        Hexdigest.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente der Validierung.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        ``paket`` (Pflicht), ``new_tif``, ``reference_tif`` und ``register``.
    """
    p = argparse.ArgumentParser(
        description=(
            "Validierung W3.2: vergleicht ein finalisiertes TIF bandweise gegen "
            "run1 und schreibt docs/rewrite/abweichungen.tsv nach der Ampel aus "
            "PLAN.md Paragraph 6. Bewertet, entscheidet nicht - Rot haelt an."
        )
    )
    p.add_argument(
        "--paket",
        required=True,
        help="Paket, dem diese Messung zugeordnet wird (z.B. W3.1 oder W2.4) - siehe PLAN.md Paragraph 6.",
    )
    p.add_argument("--new-tif", default=None, help="Default: pipeline.contract.PRODUCTS['abschichtung_tif'].")
    p.add_argument(
        "--reference-tif", default=None,
        help="Default: pipeline.contract.RUN1_TIF (aus ABSCHICHTUNG_RUN1). Ohne gesetzte "
             "Variable None - der bandweise Vergleich wird dann uebersprungen.",
    )
    p.add_argument("--register", default=None, help="Default: docs/rewrite/abweichungen.tsv.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Vergleicht ein finalisiertes TIF gegen die Referenz und schreibt das Register.

    Beim Default-``new_tif`` endet der Lauf mit 0, wenn dessen ``sha256``
    gleich ``AKTUELLE_REFERENZ_SHA256`` ist; dann findet kein bandweiser
    Vergleich statt. Andernfalls werden alle Bänder gegen run1 gemessen,
    nach der Ampel bewertet und ins Register geschrieben. Bewertet, entscheidet
    nicht.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.

    Returns
    -------
    int
        0 bei bitgleichem TIF oder ohne rotes Band; 1, wenn run1 nicht
        auflösbar ist oder mindestens ein Band rot bewertet wird.

    Raises
    ------
    FileNotFoundError
        Wenn das neue TIF, die angegebene Referenz oder das Manifest fehlt.
    """
    args = parse_args(argv)
    new_tif = Path(args.new_tif) if args.new_tif else contract.PRODUCTS["abschichtung_tif"]
    reference_tif = Path(args.reference_tif) if args.reference_tif else REFERENCE_TIF
    register_path = Path(args.register) if args.register else REGISTER_PATH

    if not new_tif.exists():
        raise FileNotFoundError(f"{new_tif} fehlt - 'make finalize' zuerst laufen lassen.")

    # Schneller Weg (Moduldocstring, "Der Nutzer hat entschieden"): stimmt
    # der frisch finalisierte Stand bitgenau mit der aktuellen, vom Nutzer
    # angenommenen Referenz ueberein, ist die bandweise Pruefung gegen run1
    # nicht noetig - und liefert ohnehin nur dieselben, bereits im Register
    # dokumentierten neun Zeilen. Nur beim Default-new-tif sinnvoll: wer
    # --new-tif explizit auf ein anderes Artefakt zeigt, will vermutlich
    # genau die bandweise Diagnose.
    if args.new_tif is None:
        new_sha256 = _sha256(new_tif)
        if new_sha256 == AKTUELLE_REFERENZ_SHA256:
            print(
                f"{new_tif.name} ist bitgleich mit der aktuellen Referenz "
                f"({AKTUELLE_REFERENZ_SHA256[:8]}…{AKTUELLE_REFERENZ_SHA256[-6:]}, "
                "PLAN.md Paragraph 6, vom Nutzer am 08.09.2026 angenommen) - "
                "keine bandweise Pruefung noetig."
            )
            return 0

    # Diagnoseweg braucht run1 (Moduldocstring, "Vergleichsbasis"): seit W6.1
    # optional (ABSCHICHTUNG_RUN1) - fehlt run1, wird dieser Teil sichtbar
    # uebersprungen statt hart abzubrechen ('run1 nicht da' war frueher ein
    # FileNotFoundError). Die Pruefsummenpruefung oben ist davon unberuehrt
    # und lief bereits (und hat nicht gepasst, sonst waere der Lauf schon
    # oben mit Exit 0 beendet).
    if reference_tif is None:
        print(
            "run1 nicht aufloesbar (ABSCHICHTUNG_RUN1 nicht gesetzt und "
            "--reference-tif nicht angegeben) - der bandweise Vergleich gegen "
            "run1 wird uebersprungen. Ohne run1 kann eine Abweichung von der "
            f"aktuellen Referenz ({AKTUELLE_REFERENZ_SHA256[:8]}…"
            f"{AKTUELLE_REFERENZ_SHA256[-6:]}) hier nicht bandweise diagnostiziert "
            "werden. run1 aus dem Archiv "
            "(~/Documents/master_windkraft/archiv/run1.tif) zurueckholen und "
            "ABSCHICHTUNG_RUN1 setzen, um dasselbe Register wieder zu erzeugen.",
            file=sys.stderr,
        )
        return 1
    if not reference_tif.exists():
        raise FileNotFoundError(f"{reference_tif} fehlt - die run1-Vergleichsbasis ist nicht da.")

    manifest_path = manifest_path_for(new_tif)
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{manifest_path} fehlt - pipeline.finalize schreibt es zusammen mit dem TIF."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    erlaubte_baender = _erlaubte_baender_aus_manifest(manifest)

    results = measure_bands(new_tif, reference_tif)
    ohne_gegenstueck = [r for r in results if r.ohne_gegenstueck]
    bitgleich = [r for r in results if r.pixel_abs == 0 and not r.ohne_gegenstueck]
    classified = write_register(register_path, args.paket, results, erlaubte_baender)

    print(f"Vergleich {new_tif.name} gegen {reference_tif.name}: {len(results)} Baender.")
    print(f"  bitgleich: {len(bitgleich)}")
    print(f"  ohne Gegenstueck in {reference_tif.name}: {len(ohne_gegenstueck)}")
    print(f"  abweichend (inkl. ohne Gegenstueck): {len(classified)}")
    if classified:
        # groesste_ha (groesste zusammenhaengende Flaeche, Register-Spalte
        # groesste_flaeche_ha) und gesamt_km2 (Summe aller abweichenden
        # Zellen) sind unterschiedliche Groessen und faellen fuer Baender
        # 27-36 unterschiedliche Ampel-Eingaben - beide getrennt ausweisen,
        # sonst liest sich die falsche Zahl wie die entscheidende.
        print(
            f"\n{'nr':>3} {'band':<40} {'pixel_abs':>10} {'anteil_%':>10} "
            f"{'kontroll_%':>10} {'groesste_ha':>12} {'gesamt_km2':>11} {'ampel':<8} {'bundesland'}"
        )
        for r in sorted(classified, key=lambda x: x.band_nr):
            flag = " *UNERWARTET*" if r.unerwartet else ""
            print(
                f"{r.band_nr:>3} {r.band_name:<40} {r.pixel_abs:>10} "
                f"{r.anteil_prozent:>10.4f} {r.anteil_prozent_kontrolle:>10.4f} "
                f"{r.groesste_flaeche_ha:>12.4f} {r.flaeche_km2:>11.4f} {r.ampel:<8} {r.schwerpunkt_bundesland}{flag}"
            )
    print(f"\nRegister geschrieben: {register_path}")

    rot = [r for r in classified if r.ampel == AMPEL_ROT]
    if rot:
        namen = ", ".join(f"{r.band_nr} {r.band_name}" for r in rot)
        print(
            f"\nROT: {len(rot)} Band(e) ueber dem Gelb-Budget oder ausserhalb "
            f"des erlaubten Wirkungspfads ({namen}). PLAN.md Paragraph 6: "
            "Rot haelt an - Rueckfrage beim Nutzer, keine eigenmaechtige Fortsetzung.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
