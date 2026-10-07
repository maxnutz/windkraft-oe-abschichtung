"""Layer-Dokumentation: ``out/LAYER.md`` aus dem Bänder-Manifest erzeugt
(Paket W7.6, Nutzerentscheidung 09.09.2026 nachmittags - Registerpunkt 66).

## Warum dieses Modul jetzt existiert, und vorher nicht

In W7.1 war ``pipeline/export/layer_doc.py`` geplant (docs/rewrite/PLAN.md
§7, Bahn 3), wurde aber bewusst nicht gebaut: "layer_doc.py liegt nicht auf
dem kritischen Pfad: verzögert es, wird LAYER.md aus der Vorlage
übernommen und der Generator als Registerpunkt notiert." Bis dahin kopierte
``make/export/layer_md.mk`` die von Hand gepflegte Datei ``docs/layer.md``
unverändert nach ``out/LAYER.md`` (Ersatz).

Der Ersatz ist inzwischen selbst zum Problem geworden: ``docs/layer.md``
trug am 09.09.2026 noch den Stand vor Schema 2.2.0/2.2.1 (Klammerzusätze,
Meterangaben und Σ in ``label_de``, 34 von 40 dokumentierten Bandnamen
weichen vom echten Manifest ab) - ein Kopierziel schreibt einen frischen
Zeitstempel auf veralteten Inhalt, ohne dass das auffällt. Dieses Modul
löst das, indem es **nichts mehr von Hand weiß**: jeder Bandname, jedes
Label, jede Beschreibung, jede Kategorie/Familie/Stufe und jeder
Parameter- oder Caveat-Wert kommt zur Laufzeit aus
``out/abschichtung.bands.json`` - genau die Regel, an der sich
``pipeline/export/dashboard.py`` (W4.1) und ``pipeline/export/viewer.py``
(W6.7) schon bewährt haben (siehe deren Moduldocstrings: "kein einziger
wörtlicher Bandname"). Ändert sich ein Label, eine Bandzahl oder eine
Kategorie, zieht der nächste Lauf es automatisch nach - ohne Handarbeit,
ohne stillen Drift.

## Was hier NICHT steht

Reine Erklärprosa, die keinem Manifestfeld entspricht (z. B. eine
Einleitung, warum es diese Kette überhaupt gibt), gehört nicht hierher,
sondern nach ``docs/LAYER-MANIFEST.md`` (der Vertrag, WIE das JSON zu lesen
ist) oder in den festen Kopf unten (``_INTRO``) - beides bewusst kurz
gehalten, damit der eigentliche Inhalt aus dem Manifest bleibt, nicht aus
Prosa.

## Struktur der erzeugten Datei

1. Fester Kopf (``_INTRO``) mit den einzigen Literalen dieses Moduls -
   Übersicht/Zweck, kein Bandwissen.
2. Stufen-Vokabular-Tabelle aus ``stufen`` (Reihenfolge: ``stufe_order``).
3. Parameter-Tabelle aus ``parameters`` (Schlüssel/Wert, sortiert).
4. Caveats-Tabelle aus ``caveats`` (id, severity, betroffene Bandzahl, Text).
5. Je Kategorie (Reihenfolge: ``category_order``) eine Überschrift mit deren
   ``description_de`` (aus ``kategorien``), darunter je Familie dieser
   Kategorie (Reihenfolge: erstes Auftreten in ``familien``) eine
   Unterüberschrift mit deren ``description_de``, darunter je Band dieser
   Familie (Reihenfolge: ``index``) ein Abschnitt mit Feldtabelle. Kategorien
   ohne Bänder (heute "Siedlungsabstand-Varianten", "Sonstige") bekommen
   trotzdem ihre Überschrift plus Hinweis "noch keine Bänder" - sie stehen
   im Manifest (``kategorien``), auch wenn (noch) kein Band sie referenziert.

## Determinismus

Zwei Läufe über dasselbe Manifest erzeugen bytegleiche Ausgaben: keine
Zeitstempel, keine Zufallsreihenfolge (Dict-Iteration folgt
``category_order``/``familien``/``bands[]``, alles bereits im Manifest
geordnet; nur ``parameters`` wird explizit sortiert, weil JSON dafür keine
Ordnungsgarantie gibt).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline import contract

_INTRO = """# Layer-Dokumentation Abschichtung Widmung v2

Dieses Dokument beschreibt jedes Band des Bänder-Manifests
`abschichtung.bands.json`, ein Abschnitt pro Band, gruppiert nach Kategorie
und Familie. Es wird von `pipeline/export/layer_doc.py` **erzeugt**, nicht
von Hand gepflegt - jede Änderung an Bandtexten, -zahl oder -struktur
kommt aus `calc/band_manifest.py` bzw. `pipeline/finalize.py` und erscheint
hier automatisch beim nächsten `make export`. Von Hand editierte Abschnitte
werden beim nächsten Lauf überschrieben.

Was ein Feld im Manifest bedeutet (`label_de` vs. `description_de`,
`stufe`, `familie`, `caveats.affects`, ...) steht nicht hier, sondern im
Vertrag `docs/LAYER-MANIFEST.md` - dieses Dokument zeigt die WERTE, jenes
erklärt die FORM.
"""


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


def _fmt_puffer(band: dict) -> str:
    """Formatiert den Puffer eines Bandes.

    Parameters
    ----------
    band : dict
        Bandeintrag aus dem Manifest.

    Returns
    -------
    str
        ``puffer_m`` in Metern und ``puffer_hinweis``, durch Leerzeichen
        getrennt; ``"keiner"``, wenn beides fehlt.
    """
    m = band.get("puffer_m")
    hinweis = band.get("puffer_hinweis")
    if m is None and not hinweis:
        return "keiner"
    parts = []
    if m is not None:
        parts.append(f"{m:g} m")
    if hinweis:
        parts.append(hinweis)
    return " ".join(parts) if parts else "keiner"


def _fmt_quellen(band: dict, sources: dict) -> str:
    """Formatiert die Quellen eines Bandes.

    Parameters
    ----------
    band : dict
        Bandeintrag aus dem Manifest.
    sources : dict
        ``sources`` aus dem Manifest.

    Returns
    -------
    str
        Je Quellschlüssel ``<key>: <pfad> (Stand <stand>)``, durch ``; `` getrennt;
        ``"–"``, wenn das Band keine Quelle nennt.
    """
    keys = band.get("quelle") or []
    if not keys:
        return "–"
    parts = []
    for k in keys:
        s = sources.get(k, {})
        pfad = s.get("pfad", "?")
        stand = s.get("stand")
        stand_txt = f" (Stand {stand})" if stand else ""
        parts.append(f"{k}: {pfad}{stand_txt}")
    return "; ".join(parts)


def _fmt_abgeleitet(band: dict) -> str:
    """Formatiert die Bänder, aus denen ein Band abgeleitet ist.

    Parameters
    ----------
    band : dict
        Bandeintrag aus dem Manifest.

    Returns
    -------
    str
        ``abgeleitet_von`` kommagetrennt, sonst ``"–"``.
    """
    dep = band.get("abgeleitet_von") or []
    return ", ".join(dep) if dep else "–"


def _fmt_caveats(band: dict, caveats: list[dict]) -> str:
    """Listet die Caveats, die ein Band betreffen.

    Parameters
    ----------
    band : dict
        Bandeintrag aus dem Manifest.
    caveats : list of dict
        ``caveats`` aus dem Manifest.

    Returns
    -------
    str
        Die ``id`` jedes Caveats, dessen ``affects.bands`` den Index des Bandes
        enthält, kommagetrennt; sonst ``"keine"``.
    """
    idx = band.get("index")
    ids = [c.get("id") for c in caveats if idx in (c.get("affects") or {}).get("bands", [])]
    return ", ".join(ids) if ids else "keine"


def _ja_nein(value: bool) -> str:
    """Übersetzt einen Wahrheitswert in ``"ja"`` oder ``"nein"``.

    Parameters
    ----------
    value : bool
        Der Wert.

    Returns
    -------
    str
        ``"ja"`` oder ``"nein"``.
    """
    return "ja" if value else "nein"


def _band_section(band: dict, sources: dict, caveats: list[dict]) -> list[str]:
    """Baut den Markdown-Abschnitt eines Bandes.

    Parameters
    ----------
    band : dict
        Bandeintrag aus dem Manifest.
    sources : dict
        ``sources`` aus dem Manifest.
    caveats : list of dict
        ``caveats`` aus dem Manifest.

    Returns
    -------
    list of str
        Überschrift mit dem Bandnamen und eine Feld/Wert-Tabelle; Zeilenumbrüche
        und ``|`` in den Werten sind maskiert.
    """
    lines = [f"#### {band['name']}", "", "| Feld | Wert |", "|---|---|"]
    rows = [
        ("Index", str(band.get("index"))),
        ("Stufe", band.get("stufe", "")),
        ("Label", band.get("label_de", "")),
        ("Beschreibung", band.get("description_de", "")),
        ("Wert-Typ", band.get("value_type", "")),
        ("Auf Österreich zugeschnitten", _ja_nein(bool(band.get("clipped_to_austria")))),
        ("Puffer", _fmt_puffer(band)),
        ("Quellen", _fmt_quellen(band, sources)),
        ("Abgeleitet von", _fmt_abgeleitet(band)),
        ("Rolle", band.get("rolle", "")),
        ("Caveats", _fmt_caveats(band, caveats)),
        ("Dashboard-Layer", _ja_nein(bool(band.get("dashboard_layer")))),
        ("Sichtbar beim Laden", _ja_nein(bool(band.get("default_visible")))),
    ]
    for feld, wert in rows:
        wert = str(wert).replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {feld} | {wert} |")
    lines.append("")
    return lines


def build_layer_md(manifest: dict) -> str:
    """Erzeugt den Text von ``LAYER.md`` aus dem Manifest.

    Enthält die Einleitung, das Stufen-Vokabular, die Parameter, die Caveats
    und je Kategorie (in ``category_order``) die Bänder, gruppiert nach
    Familie und nach Index sortiert. Kein Bandname steht im Code.

    Parameters
    ----------
    manifest : dict
        Das Band-Manifest.

    Returns
    -------
    str
        Markdown, mit genau einem abschließenden Zeilenumbruch.
    """
    stufen = manifest.get("stufen", [])
    stufe_order = manifest.get("stufe_order", [b["key"] for b in stufen])
    stufen_by_key = {s["key"]: s for s in stufen}
    kategorien = manifest.get("kategorien", [])
    kategorien_by_cat = {k["category"]: k for k in kategorien}
    category_order = manifest.get("category_order", [])
    familien = manifest.get("familien", [])
    caveats = manifest.get("caveats", []) or []
    sources = manifest.get("sources", {}) or {}
    bands = manifest.get("bands", [])
    parameters = manifest.get("parameters", {}) or {}

    out: list[str] = [_INTRO]

    out.append("## Stufen-Vokabular\n")
    out.append("| Stufe | Label | Bedeutung |")
    out.append("|---|---|---|")
    for key in stufe_order:
        s = stufen_by_key.get(key, {"label_de": key, "description_de": ""})
        out.append(f"| {key} | {s.get('label_de', '')} | {s.get('description_de', '')} |")
    out.append("")

    out.append("## Parameter\n")
    out.append("| Schlüssel | Wert |")
    out.append("|---|---|")
    for key in sorted(parameters):
        wert = str(parameters[key]).replace("\n", " ").replace("|", "\\|")
        out.append(f"| {key} | {wert} |")
    out.append("")

    out.append("## Caveats\n")
    if not caveats:
        out.append("Keine.\n")
    else:
        out.append("| id | severity | betroffene Bänder | Text |")
        out.append("|---|---|---|---|")
        for c in caveats:
            n_bands = len((c.get("affects") or {}).get("bands", []))
            text = str(c.get("text_de", "")).replace("\n", " ").replace("|", "\\|")
            out.append(f"| {c.get('id')} | {c.get('severity')} | {n_bands} | {text} |")
        out.append("")

    for category in category_order:
        kat = kategorien_by_cat.get(category, {"label_de": category, "description_de": ""})
        out.append(f"## {kat.get('label_de', category)}\n")
        if kat.get("description_de"):
            out.append(kat["description_de"] + "\n")

        cat_bands = [b for b in bands if b.get("category") == category]
        # Familien dieser Kategorie in erstem Auftreten der familien-Liste
        cat_familien = [f for f in familien if f.get("category") == category]
        if not cat_bands:
            out.append("Noch keine Bänder in dieser Kategorie.\n")
            continue

        for fam in cat_familien:
            fam_bands = [b for b in cat_bands if b.get("familie") == fam["key"]]
            if not fam_bands:
                continue
            out.append(f"### {fam.get('label_de', fam['key'])}\n")
            if fam.get("description_de"):
                out.append(fam["description_de"] + "\n")
            for b in sorted(fam_bands, key=lambda x: x.get("index", 0)):
                out.extend(_band_section(b, sources, caveats))

    return "\n".join(out).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente des ``LAYER.md``-Exports.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        ``manifest`` und ``out`` (Vorgaben aus ``contract.PRODUCTS``).
    """
    p = argparse.ArgumentParser(
        description="Erzeugt out/LAYER.md aus out/abschichtung.bands.json - kein Bandname im Code."
    )
    p.add_argument("--manifest", default=None, help="Default: pipeline.contract.PRODUCTS['abschichtung_bands_json'].")
    p.add_argument("--out", default=None, help="Default: pipeline.contract.PRODUCTS['layer_md'].")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Schreibt ``out/LAYER.md`` aus dem Band-Manifest.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.

    Returns
    -------
    int
        0 bei Erfolg; 1, wenn das Manifest fehlt.
    """
    args = parse_args(argv)
    manifest_path = Path(args.manifest) if args.manifest else contract.PRODUCTS["abschichtung_bands_json"]
    out_path = Path(args.out) if args.out else contract.PRODUCTS["layer_md"]

    if not manifest_path.exists():
        print(f"[fehlt] {manifest_path} - 'make finalize' zuerst laufen lassen.")
        return 1

    manifest = load_manifest(manifest_path)
    text = build_layer_md(manifest)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"Geschrieben: {out_path} ({len(manifest.get('bands', []))} Bänder, schema {manifest.get('schema_version')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
