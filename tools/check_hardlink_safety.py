#!/usr/bin/env python3
"""Prüft mechanisch die Hardlink-Invariante dieses Repos.

Invariante: Keine Datei in diesem Repo teilt sich einen Inode mit einer
Datei außerhalb davon. Unabhängig von Größe, Verzeichnis und davon, ob im
Code ein Overwrite-Schutz existiert.

Das ergibt zwei Regeln:

  Regel A: Keine Datei unter output/ darf einen Link-Count > 1 haben.
           Ausgaben werden nie geteilt, ohne Ausnahme — alles unter output/
           kann von der Kette geschrieben werden, und "aktuell schreibt sie
           nichts dorthin" ist keine verlässliche Garantie für die Zukunft.

  Regel B: Seit W1.3 (Hardlinks aufgelöst, docs/rewrite/nachweise/w13/) muss
           JEDE Datei unter data/ Link-Count 1 haben — nicht mehr nur eine
           deklarierte Teilmenge bekannter Schreibziele. Vor W1.3 war data/
           überwiegend echtes, per Hardlink aus dem Vorgängerprojekt
           übernommenes Quellmaterial; das war eine bewusste Warnung nur für
           die bekannten Schreibziele, weil der Rest absichtlich geteilt
           blieb. Nach W1.3 gibt es diesen Rest nicht mehr — data/ besteht
           komplett aus echten Kopien, und aus der Warnung wird eine
           Invariante: *jede* Datei mit Link-Count > 1 unter data/ ist ein
           Verstoß, ob deklariertes Schreibziel oder nicht.

Warum zwei Regeln statt einer Klassifikation "Eingaben hardlinken, Ausgaben
kopieren": genau diese Klassifikation hat vorher versagt. Die Adressregister-
Caches und die output/noe-/output/kataster-Dateien wurden korrekt als
"Artefakte, die (auch) gelesen werden" eingeordnet — und trotzdem schreibt
die Kette in sie hinein. Eine Regel, die bei einer korrekten Einordnung
trotzdem falsch liegt, ist die falsche Regel. Der Link-Count ist ein
Tatsache, keine Einordnung; er lässt sich nicht falsch einschätzen.

Exit-Code 0, wenn keine Verstöße gefunden wurden; ungleich 0 sonst.
Keine externen Abhängigkeiten, kein Zugriff auf das Alt-Repo, läuft in
Sekunden (nur stat(), kein Lesen von Dateiinhalten).
"""
from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Violation:
    rule: str  # "A" oder "B"
    path: Path
    inode: int
    link_count: int


def _stat_if_real_file(path: Path) -> os.stat_result | None:
    """lstat() auf `path`, oder None wenn es kein reguläres File ist.

    lstat() statt stat(): Symlinks selbst sollen nicht als "Hardlink" zählen
    (ihr Link-Count betrifft den Symlink-Inode, nicht das Ziel).
    """
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return st


def check_rule_a(output_dir: Path) -> tuple[list[Violation], int]:
    """Regel A: kein File unter `output_dir` darf Link-Count > 1 haben.

    Gibt (Verstöße, Anzahl geprüfter Dateien) zurück. Ein nicht existierender
    `output_dir` ist kein Fehler (0 geprüft, 0 Verstöße) — z. B. vor dem
    ersten Kettenlauf.
    """
    violations: list[Violation] = []
    checked = 0
    if not output_dir.exists():
        return violations, checked
    for dirpath, _dirnames, filenames in os.walk(output_dir):
        for name in filenames:
            path = Path(dirpath) / name
            st = _stat_if_real_file(path)
            if st is None:
                continue
            checked += 1
            if st.st_nlink > 1:
                violations.append(Violation("A", path, st.st_ino, st.st_nlink))
    return violations, checked


def check_rule_b(data_dir: Path) -> tuple[list[Violation], int]:
    """Regel B: JEDE Datei unter `data_dir` muss Link-Count 1 haben.

    Seit W1.3 gibt es keine bewusst geteilten Ausnahmen mehr — data/ besteht
    komplett aus echten Kopien. Ein nicht existierendes `data_dir` ist kein
    Fehler (0 geprüft, 0 Verstöße). Gibt (Verstöße, Anzahl geprüfter
    Dateien) zurück.
    """
    violations: list[Violation] = []
    checked = 0
    if not data_dir.exists():
        return violations, checked
    for dirpath, _dirnames, filenames in os.walk(data_dir):
        for name in filenames:
            path = Path(dirpath) / name
            st = _stat_if_real_file(path)
            if st is None:
                continue
            checked += 1
            if st.st_nlink > 1:
                violations.append(Violation("B", path, st.st_ino, st.st_nlink))
    return violations, checked


@dataclass(frozen=True)
class CheckResult:
    violations: list[Violation]
    checked_a: int
    checked_b: int

    @property
    def checked_total(self) -> int:
        """Zahl aller geprüften Dateien.

        Returns
        -------
        int
            ``checked_a + checked_b``.
        """
        return self.checked_a + self.checked_b

    @property
    def ok(self) -> bool:
        """Ob keine Verstöße gefunden wurden.

        Returns
        -------
        bool
            ``True``, wenn ``violations`` leer ist.
        """
        return not self.violations


def run_check(repo_root: Path) -> CheckResult:
    """Prüft Regel A unter ``output/`` und Regel B unter ``data/``.

    Parameters
    ----------
    repo_root : Path
        Wurzel des Repos.

    Returns
    -------
    CheckResult
        Verstöße beider Regeln und die Zahl der je Regel geprüften Dateien.
    """
    violations_a, checked_a = check_rule_a(repo_root / "output")
    violations_b, checked_b = check_rule_b(repo_root / "data")
    return CheckResult(violations_a + violations_b, checked_a, checked_b)


def format_report(result: CheckResult) -> str:
    """Formatiert das Prüfergebnis als Textbericht.

    Parameters
    ----------
    result : CheckResult
        Ergebnis aus ``run_check()``.

    Returns
    -------
    str
        Eine ``VERSTOSS``-Zeile je Verstoß mit Pfad, Inode und Link-Count, dazu
        eine abschließende ``OK``- oder ``FEHLGESCHLAGEN``-Zeile.
    """
    lines: list[str] = []
    for v in result.violations:
        regel = "Regel A (output/)" if v.rule == "A" else "Regel B (data/)"
        lines.append(
            f"VERSTOSS [{regel}]: {v.path}  "
            f"(Inode {v.inode}, Link-Count {v.link_count}, erwartet 1)"
        )
    if result.ok:
        lines.append(
            f"OK: {result.checked_total} Dateien geprüft "
            f"({result.checked_a} unter output/, {result.checked_b} unter data/) "
            f"— keine Hardlink-Verstöße gefunden."
        )
    else:
        lines.append(
            f"FEHLGESCHLAGEN: {len(result.violations)} von {result.checked_total} "
            f"geprüften Dateien verstoßen gegen die Hardlink-Invariante."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Führt die Hardlink-Prüfung aus und gibt den Bericht aus.

    Parameters
    ----------
    argv : list of str, optional
        Wird nicht ausgewertet.

    Returns
    -------
    int
        0 ohne Verstoß, sonst 1.
    """
    result = run_check(REPO_ROOT)
    print(format_report(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
