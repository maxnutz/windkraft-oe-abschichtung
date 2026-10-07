"""Dashboard: manifest-getriebener Prüfbericht über die Bänder der
Abschichtung (Paket W4.1, docs/rewrite/PLAN.md §7).

## Was dieses Werkzeug ist - und was nicht

Der eigentliche interaktive Viewer für das Widmung-v2-Ergebnis lebt nicht
in diesem Repo, sondern auf der Konsumentenseite ("Dashboard-Repo",
``scripts/band_manifest.py`` auf Branch ``feat/band-manifest`` dort - siehe
``docs/HANDOFF.md``). Seit Paket W6.7 übernimmt ``pipeline/export/viewer.py``
die Erzeugung von ``out/dashboard/index.html`` (Leaflet/OSM-Kartenviewer);
dieses Modul ist die **Prüfstufe** der Welle 4 (``pipeline/export/``): es
liest ``out/abschichtung.bands.json`` und schreibt einen Bericht (JSON) unter
``out/dashboard/``, der zeigt, was im Manifest steht, gruppiert nach
``rolle`` (WAS ein Band ist - Bedingung, Aggregat, Verfügbarkeit, Unschärfe,
Referenz) und nach ``category`` (die Anzeige-Gruppierung des Manifests selbst,
z. B. Mensch/Natur/Geografie), und prüft das Manifest auf innere Widersprüche
sowie optional gegen den Kopf des zugehörigen GeoTIFF.

## Der eigentliche Auftrag: keine Bandnamen im Code

``docs/rewrite/PLAN.md`` §3 (Produkttabelle) über genau dieses Produkt:
"liest ausschließlich das Manifest, nie eine fest verdrahtete Bandliste.
Genau daran ist der heutige Dashboard-Builder gescheitert." Gemeint ist
``scripts/analysis/build_v2_dashboard_data.py`` (gelöscht in W1.5, siehe
Git-Historie) - dessen ``EXCLUSION_LAYERS``-Dict listet ~20 Bandnamen der
ALTEN 63-Band-Kette wörtlich im Code (``settlement_v2_buffer``,
``ferienhaus_tourismus_buffer``, ``wien_full_exclusion``,
``power_380_400kv``, ...). Keiner dieser Namen existiert im heutigen
38-Band-Schema - genau das Symptom, das §13.6 dieses Projekts meint: eine
Bandliste, die an zwei Stellen lebt (Code UND Manifest), läuft auseinander,
ohne dass es auffällt, bis ein Konsument bricht.

Dieses Modul enthält deshalb **keinen einzigen wörtlichen Bandnamen**.
Wo gruppiert wird, geschieht das über die ``rolle``-Werte aus dem
Manifest-Schema 2.0.0 (``bedingung``, ``aggregat_kategorie``,
``aggregat_gesamt``, ``verfuegbarkeit_roh``, ``verfuegbarkeit_bereinigt``,
``unschaerfe``, ``referenz``) - das sind Schema-Vokabular, keine Bandnamen,
genauso wie ``category`` und die ``_wirkungspfad``-Konvention (siehe
``_impact_path_keys()`` unten) Schema-Vokabular sind. Jeder Bandname, der
in diesem Modul vorkommt, kommt zur Laufzeit aus der geladenen JSON-Datei.
Beleg: eine Suche über dieses Modul nach den Bandnamen aus dem echten
Manifest findet keinen Treffer, und der Härtetest in der Abnahme (fremdes
Manifest, andere Bandzahl, andere Namen, unverändertes Modul) läuft durch.

## Bewusst NICHT aus dem alten Skript übernommen (mit Begründung)

``scripts/analysis/build_v2_dashboard_data.py`` (s.o.) konnte mehr als
dieses Modul. Bewusst nicht übernommen, weil es entweder hartes
Bandnamen-Wissen gebraucht hätte, eine fremde Zuständigkeit berührt, oder
eine externe Quelle brauchte, die hier nicht deklariert ist (Regel 4:
Zahlen unverändert übernehmen oder eben nicht neu erfinden):

- **Fläche je Band in km², gesamt und je Bundesland (63×9-Matrix).** Das
  alte Skript liest dafür jede Zelle des Rasters (voller Bandpass, ~37 s)
  und rastert zusätzlich die Verwaltungsgrenzen aus ``pipeline/prep/admin``
  (fremde Zuständigkeit, "Braucht", nicht "Besitzt" - siehe PLAN.md §7).
  Ohne Bundesland-Aufschlüsselung bräuchte dieses Modul nur den TIF-Kopf
  (Bandzahl/-namen), keinen vollen Pixel-Scan - genau das ist die
  Grenze, an der "liest ausschließlich das Manifest" (§3) endet und ein
  Pixel-Scan beginnt. Wer die Flächenzahlen braucht: die Fläche jedes
  Bandes lässt sich aus ``out/abschichtung.tif`` mit demselben
  Zählverfahren wie ``pipeline/validate.py:measure_bands`` ableiten - das
  Werkzeug existiert bereits (W3.2), muss hier nicht dupliziert werden.
- **Marginale Ausschlussfläche je Kriterium + 3er-Venn Mensch/Natur/
  Geografie.** Wäre über ``rolle`` und ``abgeleitet_von`` prinzipiell
  manifest-getrieben nachbaubar (die Mitglieder jeder Gruppe stehen im
  ``abgeleitet_von`` der ``aggregat_kategorie``-Bänder), bräuchte dafür
  aber ebenfalls einen vollen Pixel-Scan über alle Bedingungsbänder
  gleichzeitig - Analyse, keine Prüfung. Gehört eher zu einem künftigen
  ``scripts/analysis/``-Werkzeug als zur Verify-Stufe.
- **Vergleich mit der Energiewerkstatt/IG-Windkraft-Studie 2023**
  (``STUDY_KM2``, ``STUDY_MW``, feste Zahlen je Bundesland) und die
  Windkraft-Leistungsdichte aus ``config.json``. Externe Referenzwerte
  ohne Bezug zum Manifest - eine andere Zuständigkeit als "prüft das
  Manifest".
- **Turbinendichte (MW/km²) aus der Pipeline-Konfiguration.** Gehört zur
  Analyse-Ebene, nicht zur Struktur-Prüfung des Manifests.

Was dieses Modul stattdessen NEU kann, weil das Manifest es hergibt und das
alte Skript es nicht hatte (Schema 1.0.0 kannte weder ``rolle`` noch
``quelle``/``abgeleitet_von``): Gruppierung nach Pipeline-Rolle, eine
Referenz-Integritätsprüfung (jede ``quelle``, jedes ``abgeleitet_von``, jede
``*_wirkungspfad``-Liste und jeder ``caveats[].affects.bands``-Index muss
auf etwas zeigen, das im Manifest tatsächlich existiert) und optional ein
Kopf-Abgleich gegen das GeoTIFF (Bandzahl und Bandreihenfolge - der Fehler,
den ``docs/HANDOFF.md`` als "die Falle" beschreibt: Bänder positionsbasiert
statt namensbasiert lesen).

## Kopf-Abgleich, nicht Pixel-Scan

``cross_check_raster()`` öffnet das GeoTIFF nur für seine Kopfdaten
(``rasterio.open`` liest Metadaten - Bandzahl, Bandbeschreibungen,
CRS/Transform/Shape - ohne eine einzige Pixelzeile zu dekodieren). Das ist
dieselbe Prüfung, die ``docs/HANDOFF.md`` unter "Wie ein Konsument korrekt
prüft" als Schritt 1 verlangt, hier automatisiert. Ist der Rasterpfad nicht
vorhanden oder ``--skip-raster`` gesetzt, läuft der Bericht trotzdem durch
- der Manifest-Teil ist vollständig eigenständig, das ist der Kern des
Härtetests in der Abnahme.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pipeline import contract

try:
    import rasterio
except ImportError:  # pragma: no cover - rasterio ist eine harte Abhängigkeit
    # dieses Projekts (siehe pyproject.toml); der Fallback erlaubt trotzdem,
    # den reinen Manifest-Pfad ohne Geo-Stack zu testen (siehe Härtetest).
    rasterio = None


# ---------------------------------------------------------------------------
# Manifest laden und auf innere Konsistenz prüfen - keine Bandnamen, nur
# Schema-Struktur (Schlüssel, die es in JEDEM Manifest dieses Formats gibt,
# unabhängig von Bandzahl oder -namen).
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> dict:
    """Liest das Band-Manifest.

    Parameters
    ----------
    path : Path
        Pfad zu ``abschichtung.bands.json``.

    Returns
    -------
    dict
        Das geparste JSON.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate_manifest_shape(manifest: dict) -> list[str]:
    """Formale Konsistenz: passt band_count zu bands[], sind die Indizes
    lückenlos, sind die Namen eindeutig. Reine Struktur, kein Bandwissen."""
    problems: list[str] = []
    bands = manifest.get("bands", [])
    declared = manifest.get("band_count")
    if declared != len(bands):
        problems.append(
            f"band_count ({declared!r}) stimmt nicht mit der Länge von bands[] ({len(bands)}) überein"
        )
    indices = [b.get("index") for b in bands]
    if indices != list(range(1, len(bands) + 1)):
        problems.append("bands[].index ist nicht lückenlos 1..N in Reihenfolge")
    names = [b.get("name") for b in bands]
    if len(set(names)) != len(names):
        problems.append("bands[].name enthält Duplikate")
    if any(not n for n in names):
        problems.append("mindestens ein Band hat keinen (oder leeren) name")
    return problems


def _impact_path_keys(manifest: dict) -> list[str]:
    """Alle Top-Level-Schlüssel, die der ``..._wirkungspfad``-Konvention aus
    dem Manifest-Schema folgen (siehe calc/band_manifest.py,
    Schema-Historie - Schema 2.0.0 führte den ersten dieser Schlüssel ein,
    2.1.0 einen zweiten; der heutige Manifest-Erzeuger trägt dort inzwischen
    zwei solcher Schlüssel ein). Über das Namens-SUFFIX gefunden, nicht über
    eine feste Anzahl oder einen konkreten Bandnamen - ein fremdes Manifest
    mit einem anders benannten Wirkungspfad-Schlüssel (z. B.
    ``irgendwas_wirkungspfad``) wird genauso gefunden, unabhängig davon, wie
    das Band heißt, um das es geht, und unabhängig davon, wie viele solcher
    Schlüssel es gibt."""
    return [k for k, v in manifest.items() if k.endswith("_wirkungspfad") and isinstance(v, list)]


def _validate_references(manifest: dict) -> list[str]:
    """Zeigt jede Querverweisung im Manifest auf etwas, das dort tatsächlich
    existiert? Prüft nur Struktur (Existenz einer Referenz), nie den Inhalt
    eines konkreten Bandnamens."""
    problems: list[str] = []
    bands = manifest.get("bands", [])
    names = {b.get("name") for b in bands}
    indices = {b.get("index") for b in bands}
    source_keys = set(manifest.get("sources", {}).keys())

    for b in bands:
        band_label = f"Band {b.get('index')!r} ({b.get('name')!r})"
        for src_key in b.get("quelle") or []:
            if src_key not in source_keys:
                problems.append(f"{band_label}: quelle-Schlüssel {src_key!r} fehlt in sources")
        for dep in b.get("abgeleitet_von") or []:
            if dep not in names:
                problems.append(f"{band_label}: abgeleitet_von-Eintrag {dep!r} ist kein bekannter Bandname")

    for key in _impact_path_keys(manifest):
        for n in manifest[key]:
            if n not in names:
                problems.append(f"{key}: Eintrag {n!r} ist kein bekannter Bandname")

    for c in manifest.get("caveats", []) or []:
        affects = c.get("affects", {}) if isinstance(c.get("affects"), dict) else {}
        for idx in affects.get("bands", []) or []:
            if idx not in indices:
                problems.append(f"Caveat {c.get('id')!r}: betrifft unbekannten Band-Index {idx!r}")

    return problems


# ---------------------------------------------------------------------------
# Gruppierung - ausschließlich über Schema-Vokabular (rolle, category), nie
# über einen konkreten Bandnamen.
# ---------------------------------------------------------------------------

UNKNOWN_ROLE = "(ohne rolle)"
UNKNOWN_CATEGORY = "(ohne category)"


def _band_summary(b: dict) -> dict:
    """Kurzfassung eines Manifest-Bandeintrags für den Bericht.

    Parameters
    ----------
    b : dict
        Ein Eintrag aus ``manifest["bands"]``.

    Returns
    -------
    dict
        ``index``, ``name``, ``label_de``, ``rolle``, ``category``, ``puffer_m``,
        ``puffer_hinweis`` sowie die Anzahl der Einträge in ``quelle`` und
        ``abgeleitet_von``. Fehlende Rolle oder Kategorie wird durch
        ``UNKNOWN_ROLE`` bzw. ``UNKNOWN_CATEGORY`` ersetzt.
    """
    return {
        "index": b.get("index"),
        "name": b.get("name"),
        "label_de": b.get("label_de"),
        "rolle": b.get("rolle") or UNKNOWN_ROLE,
        "category": b.get("category") or UNKNOWN_CATEGORY,
        "puffer_m": b.get("puffer_m"),
        "puffer_hinweis": b.get("puffer_hinweis"),
        "quelle_count": len(b.get("quelle") or []),
        "abgeleitet_von_count": len(b.get("abgeleitet_von") or []),
    }


def group_by_role(bands: list[dict]) -> dict[str, list[dict]]:
    """Gruppiert Bänder nach ihrer Rolle.

    Parameters
    ----------
    bands : list of dict
        Bandeinträge aus dem Manifest.

    Returns
    -------
    dict of str to list of dict
        Je Rolle (fehlend: ``UNKNOWN_ROLE``) die Kurzfassungen aus
        ``_band_summary()``, in Manifest-Reihenfolge.
    """
    groups: dict[str, list[dict]] = {}
    for b in bands:
        groups.setdefault(b.get("rolle") or UNKNOWN_ROLE, []).append(_band_summary(b))
    return groups


def group_by_category(bands: list[dict], category_order: list[str]) -> dict[str, list[dict]]:
    """Gruppiert Bänder nach ihrer Kategorie.

    Parameters
    ----------
    bands : list of dict
        Bandeinträge aus dem Manifest.
    category_order : list of str
        Kategorien, die in dieser Reihenfolge vorab angelegt werden, auch wenn
        sie leer bleiben.

    Returns
    -------
    dict of str to list of dict
        Je Kategorie (fehlend: ``UNKNOWN_CATEGORY``) die Kurzfassungen aus
        ``_band_summary()``. Kategorien außerhalb von ``category_order`` werden
        hinten angehängt.
    """
    groups: dict[str, list[dict]] = {cat: [] for cat in category_order}
    for b in bands:
        cat = b.get("category") or UNKNOWN_CATEGORY
        groups.setdefault(cat, []).append(_band_summary(b))
    return groups


# ---------------------------------------------------------------------------
# Optionaler Kopf-Abgleich gegen das GeoTIFF - Metadaten, keine Pixel.
# ---------------------------------------------------------------------------

def cross_check_raster(manifest: dict, raster_path: Path) -> dict:
    """Gleicht Bandzahl und Bandnamen des Rasters gegen das Manifest ab.

    Parameters
    ----------
    manifest : dict
        Das Band-Manifest.
    raster_path : Path
        Das zugehörige GeoTIFF.

    Returns
    -------
    dict
        ``{"checked": False, "reason": ...}``, wenn ``rasterio`` fehlt oder das
        Raster nicht existiert. Sonst ``checked``, ``raster_path``,
        ``raster_band_count``, ``manifest_band_count``, ``band_count_match``,
        ``names_match`` und ``mismatches`` (je abweichendem Index ein Eintrag,
        dazu einer bei ungleicher Länge).
    """
    if rasterio is None:
        return {"checked": False, "reason": "rasterio nicht installiert"}
    if not raster_path.exists():
        return {"checked": False, "reason": f"{raster_path} nicht gefunden"}

    with rasterio.open(raster_path) as src:
        raster_count = src.count
        raster_names = [d for d in src.descriptions]

    manifest_names = [b.get("name") for b in manifest.get("bands", [])]
    band_count_match = raster_count == manifest.get("band_count")

    mismatches = []
    for i, (rn, mn) in enumerate(zip(raster_names, manifest_names), start=1):
        if rn != mn:
            mismatches.append({"index": i, "raster_name": rn, "manifest_name": mn})
    if len(raster_names) != len(manifest_names):
        mismatches.append(
            {
                "index": None,
                "raster_name": f"<{len(raster_names)} Bänder>",
                "manifest_name": f"<{len(manifest_names)} Bänder>",
            }
        )

    return {
        "checked": True,
        "raster_path": str(raster_path),
        "raster_band_count": raster_count,
        "manifest_band_count": manifest.get("band_count"),
        "band_count_match": band_count_match,
        "names_match": not mismatches,
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Bericht zusammensetzen
# ---------------------------------------------------------------------------

def build_report(manifest: dict, raster_path: Path | None) -> dict:
    """Baut den Prüfbericht des Dashboards aus dem Manifest.

    Parameters
    ----------
    manifest : dict
        Das Band-Manifest.
    raster_path : Path or None
        Raster für ``cross_check_raster()``; ``None`` überspringt den Abgleich.

    Returns
    -------
    dict
        Erzeugungszeit, Kopfdaten des Manifests, Ergebnis der Manifestprüfung
        (``validation``), Rasterabgleich (``raster_check``), Gruppierungen nach
        Rolle und Kategorie, ``sources``, ``caveats``, Wirkungspfade und die
        Kurzfassung jedes Bandes.
    """
    bands = manifest.get("bands", [])
    problems = _validate_manifest_shape(manifest) + _validate_references(manifest)
    category_order = manifest.get("category_order") or sorted(
        {b.get("category") or UNKNOWN_CATEGORY for b in bands}
    )

    role_groups = group_by_role(bands)
    category_groups = group_by_category(bands, category_order)

    if raster_path is not None:
        raster_check = cross_check_raster(manifest, raster_path)
    else:
        raster_check = {"checked": False, "reason": "--skip-raster gesetzt bzw. kein Rasterpfad übergeben"}

    return {
        "dashboard_generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_manifest": {
            "schema_version": manifest.get("schema_version"),
            "generated_at": manifest.get("generated_at"),
            "pipeline": manifest.get("pipeline"),
            "band_schema": manifest.get("band_schema"),
            "raster_file": manifest.get("raster_file"),
            "band_count": manifest.get("band_count"),
        },
        "raster_meta": manifest.get("raster", {}),
        "validation": {"ok": not problems, "problems": problems},
        "raster_check": raster_check,
        "role_counts": {rolle: len(items) for rolle, items in role_groups.items()},
        "roles": role_groups,
        "category_order": category_order,
        "categories": category_groups,
        "sources": manifest.get("sources", {}),
        "caveats": manifest.get("caveats", []),
        "impact_paths": {k: manifest[k] for k in _impact_path_keys(manifest)},
        "bands": [_band_summary(b) for b in bands],
    }


# ---------------------------------------------------------------------------
# Ausgabe: JSON (maschinenlesbar)
# ---------------------------------------------------------------------------

def write_json_report(out_dir: Path, report: dict) -> Path:
    """Schreibt den Bericht als ``report.json``.

    Parameters
    ----------
    out_dir : Path
        Zielverzeichnis.
    report : dict
        Bericht aus ``build_report()``.

    Returns
    -------
    Path
        Pfad der geschriebenen Datei.
    """
    out_path = out_dir / "report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path




# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente des Dashboard-Exports.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        ``manifest``, ``raster``, ``out`` (Vorgaben aus ``contract.PRODUCTS``)
        und ``skip_raster``.
    """
    p = argparse.ArgumentParser(
        description=(
            "W4.1: liest das Band-Manifest (nie eine fest verdrahtete Bandliste) und "
            "schreibt einen Pruefbericht (JSON + HTML) unter out/dashboard/. "
            "Siehe docs/rewrite/PLAN.md Paragraph 7 (W4.1) und Paragraph 3."
        )
    )
    p.add_argument("--manifest", default=None, help="Default: pipeline.contract.PRODUCTS['abschichtung_bands_json'].")
    p.add_argument("--raster", default=None, help="Default: pipeline.contract.PRODUCTS['abschichtung_tif'].")
    p.add_argument("--out", default=None, help="Default: pipeline.contract.PRODUCTS['dashboard_dir'].")
    p.add_argument(
        "--skip-raster",
        action="store_true",
        help="Raster-Kopf-Abgleich auslassen, auch wenn --raster bzw. der Default existiert.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Liest das Manifest, baut den Prüfbericht und schreibt ``report.json``.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.

    Returns
    -------
    int
        0 bei Erfolg; 1, wenn die Manifestprüfung Probleme findet oder Raster
        und Manifest im Abgleich nicht übereinstimmen.

    Raises
    ------
    FileNotFoundError
        Wenn das Manifest fehlt.
    """
    args = parse_args(argv)
    manifest_path = Path(args.manifest) if args.manifest else contract.PRODUCTS["abschichtung_bands_json"]
    raster_path = Path(args.raster) if args.raster else contract.PRODUCTS["abschichtung_tif"]
    out_dir = Path(args.out) if args.out else contract.PRODUCTS["dashboard_dir"]

    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} fehlt - 'make finalize' zuerst laufen lassen.")

    manifest = load_manifest(manifest_path)
    report = build_report(manifest, raster_path=None if args.skip_raster else raster_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json_report(out_dir, report)

    print(f"Manifest gelesen: {manifest_path} ({report['source_manifest']['band_count']} Bänder)")
    print("Bänder je rolle:")
    for rolle, count in report["role_counts"].items():
        print(f"  {rolle:<26} {count:>3}")
    rc = report["raster_check"]
    if rc.get("checked"):
        status = "OK" if rc["band_count_match"] and rc["names_match"] else "ABWEICHUNG"
        print(f"Raster-Abgleich: {status} ({rc['raster_path']})")
    else:
        print(f"Raster-Abgleich: übersprungen ({rc.get('reason')})")
    print(f"Geschrieben: {json_path}")

    problems = report["validation"]["problems"]
    if problems:
        print(f"\n{len(problems)} Problem(e) im Manifest - siehe report.json['validation'].", file=sys.stderr)
        return 1
    if rc.get("checked") and not (rc["band_count_match"] and rc["names_match"]):
        print("\nRaster und Manifest stimmen nicht überein - siehe report.json['raster_check'].", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
