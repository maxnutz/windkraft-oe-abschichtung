"""Fingerabdruck der Eingänge einer Stufe (Entscheidung (c), docs/rewrite/PLAN.md §5).

Jede Prep-Stufe (W1.P1-W1.P9) schreibt nach einem erfolgreichen Lauf einen
Fingerabdruck ihrer tatsächlich gelesenen Eingaben. Ein späterer Aufrufer -
ein erneuter Prep-Lauf oder, laut Nebenbefund in PLAN.md §12, auch die
Layer-Stufe (W2.1-W2.3) - prüft mit ``matches()``, ob sich seither eine der
Eingaben geändert hat, und kann dann hart abbrechen statt mit veralteten
Zwischenständen weiterzurechnen.

Dieses Modul liegt bewusst auf Höhe von ``pipeline/contract.py`` und
``pipeline/runtime.py`` - nicht unter ``pipeline/prep/`` - weil laut
PLAN.md §12 ("Nebenbefund mit Folgen für Welle 2") *derselbe* Mechanismus
auch von der Layer-Stufe gebraucht wird, die keine Prep-Stufe ist. Ohne
dieses Vorpaket hätte vermutlich jedes der neun Prep-Pakete seine eigene,
leicht andere Fingerabdruck-Logik erfunden - genau die Inkonsistenz, vor
der der Nebenbefund warnt. Kein Paket aus §7 "besitzt" diese Datei; sie ist
Infrastruktur wie ``pipeline/runtime.py`` (siehe dessen Docstring).

Größe und Änderungszeit (nicht der Dateiinhalt) bilden den Fingerabdruck für
Rohdaten und Zwischenstände - ein Hash über mehrere GB bei jedem Lauf wäre
selbst die Kosten, die die Prep-Stufe laut Entscheidung (c) gerade vermeiden
soll ("läuft nicht bei jedem Kettenlauf mit").

Ausnahme (W6.6, docs/rewrite/FORTSCHRITT.md Punkt 56): für unter Git
versionierte Quelldateien - erkannt an Lage plus Endung, siehe
``_is_versioned_source()`` - bildet stattdessen ein ``sha256`` über den
Dateiinhalt den Fingerabdruck. Grund: seit W6.4 nimmt jedes der zehn
Prep-Einstiegsmodule seine eigene Quelldatei mit in die Eingabemenge auf,
damit eine Codeänderung den Abdruck ungültig macht (Punkt 53) - aber
``git clone`` überträgt keine ``mtime``, jede frisch ausgecheckte Datei
bekommt die Checkout-Zeit, also passte seither kein gespeicherter Abdruck
mehr in einem Klon (0 von 10 Prep-Stufen übersprangen, siehe W6.4-Messung).
Ein paar Dutzend KB Python zu hashen kostet nichts gegenüber den
Gigabytes unter ``data/``, für die es beim Kostenargument oben bleibt.

Das Speicherformat trägt seit dieser Änderung eine Versionsnummer
(``_fingerprint_version``) und pro Eintrag einen Art-Diskriminator
(``kind``): ein vor W6.6 geschriebener Abdruck (flaches
``{pfad: {"size":…, "mtime_ns":…}}`` ohne diese Schlüssel) kann dadurch
nie mehr als Treffer durchgehen, sondern löst zuverlässig eine Neuberechnung
aus - absichtlich, siehe Bericht zu W6.6.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pipeline import contract

FINGERPRINT_FILENAME = ".fingerprint.json"

# W6.6/Punkt 56: Format-Version des Fingerabdrucks. Erhöhen, wann immer sich
# die Bedeutung eines gespeicherten Eintrags ändert (z. B. eine weitere
# Hash-Art dazukommt) - ein alter Abdruck muss dann automatisch als
# Nicht-Treffer gelten, siehe Moduldocstring.
FINGERPRINT_SCHEMA_VERSION = 2

# Diese drei Verzeichnisse enthalten laut Vertrag (pipeline/contract.py)
# ausschließlich unter Git versionierten Code - keine Rohdaten, keine
# Zwischenstände. Eine ".py"-Datei darunter gilt deshalb als "versionierte
# Quelldatei" (Punkt 56) und wird gehasht statt ge-stat't. Absichtlich rein
# pfadbasiert statt über einen eigenen ``code_inputs=``-Parameter an den
# zehn Aufrufstellen: dort steht die Quelldatei (z. B. ``MODULE_PATH``)
# bereits heute mitten in derselben ``inputs``-Liste wie die Datendateien
# (siehe z. B. pipeline/prep/noe_sekrop.py, pipeline/prep/kataster/
# a_noe_polygonize.py) - ein eigener Parameter hätte alle zehn Aufrufstellen
# anfassen müssen, für eine Unterscheidung, die sich am Pfad selbst schon
# eindeutig ablesen lässt.
_VERSIONED_SOURCE_DIRS = ("pipeline", "calc", "tools")


def _is_versioned_source(path: Path) -> bool:
    """True, wenn ``path`` als unter Git versionierte Quelldatei gilt.

    Kriterium ist Lage plus Endung, nicht ein Git-Aufruf zur Laufzeit -
    billig, deterministisch, ohne Prozessstart (Anforderung aus Punkt 56):
    jede ``.py``-Datei unterhalb von ``pipeline/``, ``calc/`` oder
    ``tools/`` innerhalb von ``contract.ROOT``. Liegt ``path`` außerhalb von
    ``contract.ROOT`` oder ist die Endung nicht ``.py``, gilt es als
    Rohdatum/Zwischenstand (Rückfall auf ``_stat()``).
    """
    try:
        rel = path.resolve().relative_to(contract.ROOT)
    except ValueError:
        return False
    return rel.suffix == ".py" and rel.parts[0] in _VERSIONED_SOURCE_DIRS


def _stat(path: Path) -> dict:
    """Fingerabdruck-Eintrag aus Dateigröße und Änderungszeit.

    Für Rohdaten und Zwischenstände (siehe Moduldocstring).

    Parameters
    ----------
    path : Path
        Datei, deren Metadaten gelesen werden.

    Returns
    -------
    dict
        ``{"kind": "stat", "size": ..., "mtime_ns": ...}``.
    """
    st = path.stat()
    return {"kind": "stat", "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _sha256(path: Path) -> dict:
    """Fingerabdruck-Eintrag aus dem ``sha256`` des Dateiinhalts.

    Für unter Git versionierte Quelldateien (siehe ``_is_versioned_source()``).

    Parameters
    ----------
    path : Path
        Datei, deren Inhalt gehasht wird.

    Returns
    -------
    dict
        ``{"kind": "sha256", "sha256": <hexdigest>}``.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"kind": "sha256", "sha256": digest}


def _fingerprint_entry(path: Path) -> dict:
    """Wählt je Eingabe die Art des Fingerabdrucks.

    Parameters
    ----------
    path : Path
        Eine Eingabedatei der Stufe.

    Returns
    -------
    dict
        ``_sha256(path)`` für eine versionierte Quelldatei, sonst
        ``_stat(path)``.
    """
    if _is_versioned_source(path):
        return _sha256(path)
    return _stat(path)


def compute(inputs: list[Path]) -> dict:
    """Baut den Fingerabdruck-Datensatz für die gegebenen Eingaben.

    Schlüssel ist der Pfad als String, **relativ zu ``contract.ROOT``**, wenn
    die Eingabe innerhalb des Repos liegt - nicht der volle absolute Pfad
    (W6.4, docs/rewrite/FORTSCHRITT.md Punkt 53): der volle Pfad hängt am
    Klonort, ``contract.ROOT`` selbst leitet sich aus ``__file__`` ab, also
    hätte jeder Klon andere Schlüssel und nie einen Treffer - auch wenn die
    Dateien (z. B. über einen read-only-Symlink wie ``derived/prep/`` in
    ``make worktree``) buchstäblich dieselben sind. Für alles außerhalb von
    ``contract.ROOT`` (kann laut Vertrag nicht vorkommen, aber ``compute()``
    prüft das nicht) bleibt der Rückfall der aufgelöste absolute Pfad -
    weiterhin voll, nicht nur der Dateiname: zwei gleichnamige Dateien aus
    verschiedenen Verzeichnissen (z. B. je ein Bundesland-ZIP) dürfen sich
    nicht überschreiben.

    Je Eintrag entscheidet ``_fingerprint_entry()`` (Punkt 56), ob ``sha256``
    (versionierte Quelldatei) oder ``_stat()`` (alles andere) gebildet wird.
    Das Ergebnis trägt zusätzlich ``_fingerprint_version`` als Format-Tag
    (siehe Moduldocstring) - ein vor W6.6 gespeicherter, flacher Abdruck
    gleicht diesem Rückgabewert dadurch nie mehr.
    """
    entries = {}
    for p in inputs:
        try:
            key = str(p.relative_to(contract.ROOT))
        except ValueError:
            key = str(p.resolve())
        entries[key] = _fingerprint_entry(p)
    return {"_fingerprint_version": FINGERPRINT_SCHEMA_VERSION, "entries": entries}


def write(prep_dir: Path, inputs: list[Path]) -> Path:
    """Schreibt den Fingerabdruck der gegebenen Eingaben nach
    ``<prep_dir>/.fingerprint.json`` und gibt den geschriebenen Pfad zurück.

    Legt ``prep_dir`` an, falls es noch nicht existiert (wie
    ``pipeline.runtime.ensure_dir`` - kein eigener mkdir-Aufruf nötig).
    """
    prep_dir.mkdir(parents=True, exist_ok=True)
    fp_path = prep_dir / FINGERPRINT_FILENAME
    fp_path.write_text(
        json.dumps(compute(inputs), indent=2, sort_keys=True), encoding="utf-8"
    )
    return fp_path


def matches(prep_dir: Path, inputs: list[Path]) -> bool:
    """True, wenn der unter ``prep_dir`` gespeicherte Fingerabdruck exakt zu
    den aktuellen ``inputs`` passt.

    False, wenn die Fingerabdruckdatei fehlt, nicht lesbar ist, sich der
    Inhalt (sha256) einer versionierten Quelldatei oder Größe/Änderungszeit
    einer sonstigen Eingabe geändert haben, sich nur die Menge der Eingaben
    selbst geändert hat (eine hinzugekommene oder weggefallene Datei zählt
    als Änderung), oder der gespeicherte Abdruck aus einer älteren
    Formatversion stammt (``_fingerprint_version``, Punkt 56) - Letzteres
    trifft auf jeden vor W6.6 geschriebenen Abdruck zu.
    """
    fp_path = prep_dir / FINGERPRINT_FILENAME
    if not fp_path.exists():
        return False
    try:
        stored = json.loads(fp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return stored == compute(inputs)
