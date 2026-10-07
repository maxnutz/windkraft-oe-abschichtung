#!/usr/bin/env python3
"""Statischer Wächter: nichts im Code schreibt nach ``data/``.

``data/`` ist der Rohdatenbaum (siehe docs/rohdaten.md). Er wird
ausschließlich gelesen — die Kette selbst darf dort nie hineinschreiben.
Das ist die Sorte Fehler, die W1.1 in ``bev_register.py`` fand: eine
Cache-Weiche, die beim ersten Aufruf still in ``data/`` landete, weil ihr
Vorgabewert kein eigenes Build-Verzeichnis kannte.

Dieser Wächter prüft das **statisch**, per AST — nicht durch Beobachten
eines echten Laufs. Er sucht nach bekannten Schreibaufrufen (``to_parquet``,
``open(..., "w")``, ``mkdir``, ``shutil.copy`` usw.) und verfolgt deren
Pfad-Argument rückwärts durch einfache Zuweisungen innerhalb derselben
Funktion, um festzustellen, ob es aus ``data/`` stammt — entweder über
einen zusammengesetzten Pfad (``ROOT / "data" / "x"``) oder über den
Vertrag (``contract.RAW[...]``, ``contract.DATA``, ``contract.LEGACY_*``).

Exit-Code 0, wenn kein Fund; ungleich 0 sonst. Keine externen
Abhängigkeiten, kein Dateizugriff außer dem Einlesen der ``.py``-Dateien
selbst, läuft in Sekunden.

---------------------------------------------------------------------------
BEKANNTE LÜCKEN — was dieser Wächter NICHT findet
---------------------------------------------------------------------------

1. **Keine funktionsübergreifende Datenflussverfolgung.** Die
   Taint-Verfolgung bleibt innerhalb einer einzelnen Funktion. Wird ein
   Rohpfad als Parameter hereingereicht (``def f(cache_dir): cache_dir.mkdir()``)
   und der Aufrufer übergibt einen ``data/``-Pfad, sieht dieser Wächter das
   nicht — er kennt den Aufrufer nicht. Genau dieses Muster hat W1.1 in
   ``bev_register.py`` gefunden; dort wirkte es aber über einen
   *Vorgabewert* (siehe Punkt 3), nicht über eine Aufrufkette, und wird
   deshalb doch erkannt.

2. **Keine Auflösung dynamischer Pfade.** ``Path(some_string_from_config())``
   oder ein Pfad, der aus ``config.json`` zur Laufzeit gelesen wird, ist für
   eine statische AST-Analyse unsichtbar. Betrifft insbesondere
   ``cfg["paths"][...]``-Zugriffe — die sind hier bewusst NICHT geprüft,
   weil ``config.json`` beliebige Werte enthalten kann und eine Prüfung
   ohne echtes Einlesen der Konfiguration nur raten würde.

3. **Vorgabewerte werden nur bei direkter Literal-Zusammensetzung erkannt.**
   Ein Parameter-Vorgabewert wie ``cache_dir: Path = DATA / "cache"`` wird
   erkannt (siehe unten). Ein Vorgabewert, der erst zur Laufzeit über eine
   Funktion oder einen Konfigurationszugriff aufgelöst wird
   (``cache_dir=get_default_cache()``), wird NICHT erkannt.

4. **Keine String-Konkatenation außerhalb von ``/`` und ``os.path.join``.**
   f-Strings, ``str.format()`` oder ``"data/" + x`` werden nicht als
   Pfadaufbau erkannt (nur der Sonderfall eines Literals, das komplett mit
   ``data/`` beginnt oder ``/data/`` enthält).

5. **``.parent`` und ähnliche Pfad-Transformationen übervorsichtig.**
   Taint wird durch ``.parent``, ``.resolve()``, ``.joinpath()`` usw.
   hindurch weitergereicht, auch wenn ``.parent`` eines ``data/``-Pfads
   im Extremfall aus dem Baum heraus in ein Elternverzeichnis führen
   könnte. Das ist eine bewusste Überschätzung (mehr Fehlalarme statt
   übersehener Funde) — kommt in diesem Repo aber nicht vor, weil niemand
   ``DATA.parent`` bildet.

6. **Nur ``calc/``, ``scripts/``, ``pipeline/`` und ``tools/`` (außer
   sich selbst und ``check_hardlink_safety.py``).** ``tests/`` ist bewusst
   ausgenommen — Tests arbeiten laut Konvention in ``tempfile``-Verzeichnissen
   (siehe ``tests/test_check_hardlink_safety.py``), nicht in ``data/``, aber
   dieser Wächter erzwingt das nicht. ``docs/`` (Analyse- und
   Migrationsskripte, z. B. ``docs/rewrite/nachweise/``) ist ebenfalls
   ausgenommen — die sind Einmalwerkzeuge zur Beweisführung, nicht Teil der
   laufenden Kette.

7. **Nur eine feste Liste bekannter Schreib-APIs.** Siehe ``WRITE_APIS_*``
   unten. Eine neue, hier nicht gelistete Schreibmethode (z. B. eine
   fremde Bibliotheksfunktion mit ungewöhnlichem Namen) wird nicht erkannt.

Ein Wächter, der ehrlich zeigt, was er nicht prüft, ist besser als einer,
der Vollständigkeit vortäuscht (siehe docs/rewrite/PLAN.md §7, W1.4).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCAN_DIRS = ("calc", "scripts", "pipeline", "tools")

# Der Wächter scannt sich selbst nicht (enthält absichtlich Beispielnamen
# wie "to_parquet" im Docstring/Quellcode) und lässt check_hardlink_safety.py
# aus, weil der ohnehin nur liest (stat()), nie schreibt.
SELF_EXCLUDE = {"check_raw_only.py", "check_hardlink_safety.py"}

# Attribut-Methoden, deren PFAD-ARGUMENT das erste positionelle Argument
# (oder ein passendes Keyword) ist - der Aufruf-"Empfänger" (z. B. ein
# DataFrame) selbst ist nicht der Pfad.
WRITE_APIS_ARG0 = {
    "to_parquet": ("path",),
    "to_csv": ("path_or_buf",),
    "to_file": ("filename",),
    "to_pickle": ("path",),
    "to_excel": ("excel_writer",),
    "to_json": ("path_or_buf",),
}

# Attribut-Methoden, deren PFAD der Empfänger selbst ist (z. B.
# `target.write_bytes(...)`, `cache_dir.mkdir()`).
WRITE_APIS_SELF = {"write_text", "write_bytes", "mkdir"}

# Freie Funktionen mit fester Positions-/Keyword-Zuordnung für den
# Pfad ("dst" = Ziel bei Kopier-/Verschiebeoperationen).
FREE_FUNCS_ARG0 = {
    "makedirs": ("name",),
    "ensure_dir": ("path",),
    "ensure_parent": ("file_path",),
}
FREE_FUNCS_DST = {
    "rename": (1, "dst"),
    "replace": (1, "dst"),
    "move": (1, "dst"),
    "copy": (1, "dst"),
    "copy2": (1, "dst"),
    "copyfile": (1, "dst"),
}

WRITE_MODE_CHARS = set("wax")  # write/append/exclusive-create


@dataclass(frozen=True)
class Finding:
    path: Path
    lineno: int
    call_desc: str
    reason: str


def _contract_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Findet Namen, die auf das Vertragsmodul bzw. dessen Rohpfad-Symbole
    gebunden sind - egal ob per ``from pipeline import contract`` (Modul)
    oder ``from pipeline.contract import RAW`` (Direktimport)."""
    module_aliases: set[str] = set()
    direct_names: set[str] = set()
    interesting = {"RAW", "DATA", "LEGACY_TOT", "LEGACY_ENTFAELLT"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "pipeline" and any(a.name == "contract" for a in node.names):
                for alias in node.names:
                    if alias.name == "contract":
                        module_aliases.add(alias.asname or alias.name)
            elif node.module == "pipeline.contract":
                for alias in node.names:
                    if alias.name in interesting:
                        direct_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pipeline.contract":
                    module_aliases.add(alias.asname or "contract")
    return module_aliases, direct_names


def _runtime_bindings(tree: ast.Module) -> set[str]:
    """Namen, die auf ``pipeline.runtime.ensure_dir``/``ensure_parent``
    gebunden sind (egal welcher lokale Alias)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pipeline.runtime":
            for alias in node.names:
                if alias.name in ("ensure_dir", "ensure_parent"):
                    names.add(alias.asname or alias.name)
    return names


def _is_data_literal_str(value: str) -> bool:
    """Ob ein String-Literal einen Pfad unter ``data/`` bezeichnet.

    Parameters
    ----------
    value : str
        Das Literal.

    Returns
    -------
    bool
        ``True`` für ``"data"``, für Literale, die mit ``data/`` beginnen, und für
        Literale, die ``/data/`` enthalten.
    """
    return value == "data" or value.startswith("data/") or "/data/" in value


class _TaintChecker:
    """Prüft Ausdrücke auf Herkunft aus ``data/`` - entweder über den
    Vertrag (``contract.RAW``/``contract.DATA``/``contract.LEGACY_*``) oder
    über einen zusammengesetzten Pfad mit dem Segment ``"data"``."""

    def __init__(self, contract_modules: set[str], contract_names: set[str]):
        """Legt die Namen fest, die als Herkunft aus ``data/`` gelten.

        Parameters
        ----------
        contract_modules : set of str
            Lokale Namen des Vertragsmoduls (aus ``_contract_bindings()``).
        contract_names : set of str
            Direkt importierte Rohpfad-Symbole (aus ``_contract_bindings()``).
        """
        self.contract_modules = contract_modules
        self.contract_names = contract_names

    def is_tainted(self, node: ast.AST, local_tainted: set[str]) -> bool:
        """Ob ein Ausdruck einen Pfad aus ``data/`` ergibt.

        Als belastet gelten Vertragssymbole (``RAW``, ``DATA``, ``LEGACY_TOT``,
        ``LEGACY_ENTFAELLT``), lokal belastete Namen und ``data/``-Literale. Taint
        wird durch Attribute, Subskripte, ``/``, ``or``/``and``, bedingte Ausdrücke
        sowie ``joinpath``, ``resolve``, ``absolute``, ``with_suffix``,
        ``with_name``, ``os.path.join`` und ``Path(...)`` weitergereicht.

        Parameters
        ----------
        node : ast.AST
            Der zu prüfende Ausdruck.
        local_tainted : set of str
            In der aktuellen Funktion bereits als belastet erkannte Namen.

        Returns
        -------
        bool
            ``True``, wenn der Ausdruck als aus ``data/`` stammend erkannt wird.
        """
        if isinstance(node, ast.Name):
            return node.id in self.contract_names or node.id in local_tainted
        if isinstance(node, ast.Attribute):
            if (
                node.attr in ("RAW", "DATA", "LEGACY_TOT", "LEGACY_ENTFAELLT")
                and isinstance(node.value, ast.Name)
                and node.value.id in self.contract_modules
            ):
                return True
            # Pfadtransformationen reichen Taint durch (bewusst
            # übervorsichtig - siehe Docstring, Lücke 5).
            if node.attr in ("parent", "parents", "stem", "name"):
                return self.is_tainted(node.value, local_tainted)
            return self.is_tainted(node.value, local_tainted)
        if isinstance(node, ast.Subscript):
            return self.is_tainted(node.value, local_tainted)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _is_data_literal_str(node.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.is_tainted(node.left, local_tainted) or self.is_tainted(
                node.right, local_tainted
            )
        if isinstance(node, ast.BoolOp):  # `x or y`, `x and y`
            return any(self.is_tainted(v, local_tainted) for v in node.values)
        if isinstance(node, ast.IfExp):  # `a if cond else b`
            return self.is_tainted(node.body, local_tainted) or self.is_tainted(
                node.orelse, local_tainted
            )
        if isinstance(node, ast.Call):
            func = node.func
            funcname = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None
            )
            if funcname in ("joinpath", "resolve", "absolute", "with_suffix", "with_name"):
                if isinstance(func, ast.Attribute) and self.is_tainted(func.value, local_tainted):
                    return True
            if funcname == "join":  # os.path.join(...)
                return any(self.is_tainted(a, local_tainted) for a in node.args)
            if funcname == "Path":
                return any(self.is_tainted(a, local_tainted) for a in node.args)
            return False
        return False


def _first_matching_arg(call: ast.Call, keywords: tuple[str, ...], index: int = 0):
    """Liefert ein Aufrufargument nach Position oder Schlüsselwort.

    Parameters
    ----------
    call : ast.Call
        Der Aufruf.
    keywords : tuple of str
        Schlüsselwortnamen, unter denen das Argument stehen kann.
    index : int, optional
        Position des Arguments, Vorgabe 0.

    Returns
    -------
    ast.expr or None
        Das Positionsargument an ``index``, falls vorhanden, sonst den Wert des
        ersten passenden Schlüsselworts, sonst ``None``.
    """
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg in keywords:
            return kw.value
    return None


def _mode_is_write(call: ast.Call, mode_index: int = 1) -> bool:
    """Ob das Modus-Argument eines Aufrufs schreibend ist.

    Parameters
    ----------
    call : ast.Call
        Der Aufruf.
    mode_index : int, optional
        Position des Modus-Arguments, Vorgabe 1.

    Returns
    -------
    bool
        ``True``, wenn der Modus ein String-Literal mit einem Zeichen aus
        ``WRITE_MODE_CHARS`` ist. ``False`` ohne Modus oder bei dynamischem
        Modus.
    """
    mode_node = _first_matching_arg(call, ("mode",), mode_index)
    if mode_node is None:
        return False  # kein Modus angegeben -> Default ist meist Lesen ("r")
    if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
        return any(c in WRITE_MODE_CHARS for c in mode_node.value)
    return False  # dynamischer Modus - siehe Lücke 4/2, wir raten nicht


class _FunctionScanner(ast.NodeVisitor):
    """Läuft den Körper einer Funktion sequenziell ab, verfolgt einfache
    Zuweisungen (nur ``Name = ...``, kein Tuple-Unpacking) und meldet jeden
    Schreibaufruf, dessen Pfad-Argument als aus ``data/`` stammend erkannt
    wird."""

    def __init__(
        self,
        checker: _TaintChecker,
        runtime_names: set[str],
        findings: list[Finding],
        file_path: Path,
    ):
        """Bereitet den Scan einer Funktion vor.

        Parameters
        ----------
        checker : _TaintChecker
            Prüft Ausdrücke auf Herkunft aus ``data/``.
        runtime_names : set of str
            Lokale Namen für ``pipeline.runtime.ensure_dir``/``ensure_parent``.
        findings : list of Finding
            Liste, an die Funde angehängt werden.
        file_path : Path
            Die gescannte Datei, für die Funde.
        """
        self.checker = checker
        self.runtime_names = runtime_names
        self.findings = findings
        self.file_path = file_path
        self.tainted: set[str] = set()

    def _init_params(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Markiert Parameter mit belastetem Vorgabewert als belastet.

        Parameters
        ----------
        fn : ast.FunctionDef or ast.AsyncFunctionDef
            Die gescannte Funktion; berücksichtigt positionelle und
            Keyword-only-Parameter.
        """
        args = fn.args
        positional = args.posonlyargs + args.args
        defaults = args.defaults
        # defaults richten sich an das ENDE von `positional`
        offset = len(positional) - len(defaults)
        for arg, default in zip(positional[offset:], defaults):
            if default is not None and self.checker.is_tainted(default, self.tainted):
                self.tainted.add(arg.arg)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is not None and self.checker.is_tainted(default, self.tainted):
                self.tainted.add(arg.arg)

    def run(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Scannt eine Funktion: Parameter, dann Körper.

        Parameters
        ----------
        fn : ast.FunctionDef or ast.AsyncFunctionDef
            Die zu scannende Funktion.
        """
        self._init_params(fn)
        self._walk_body(fn.body)

    def _walk_body(self, body: list[ast.stmt]) -> None:
        """Besucht eine Anweisungsliste der Reihe nach.

        Parameters
        ----------
        body : list of ast.stmt
            Die Anweisungen.
        """
        for stmt in body:
            self._visit_stmt(stmt)

    def _visit_stmt(self, stmt: ast.stmt) -> None:
        """Verarbeitet eine Anweisung.

        Zuweisungen an einen einfachen Namen setzen oder entfernen dessen Taint
        je nach Wert. In allen Ausdrücken werden Aufrufe geprüft; Blöcke von
        ``if``, ``for``, ``while``, ``with`` und ``try`` werden rekursiv besucht.
        Verschachtelte Funktionen werden übersprungen.

        Parameters
        ----------
        stmt : ast.stmt
            Die Anweisung.
        """
        # Zuweisungen: Taint des Ziels aus dem Wert ableiten.
        if isinstance(stmt, ast.Assign):
            self._check_expr_for_calls(stmt.value)
            tainted_value = self.checker.is_tainted(stmt.value, self.tainted)
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if tainted_value:
                        self.tainted.add(target.id)
                    else:
                        self.tainted.discard(target.id)
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            self._check_expr_for_calls(stmt.value)
            if isinstance(stmt.target, ast.Name):
                if self.checker.is_tainted(stmt.value, self.tainted):
                    self.tainted.add(stmt.target.id)
                else:
                    self.tainted.discard(stmt.target.id)
        elif isinstance(stmt, ast.AugAssign):
            self._check_expr_for_calls(stmt.value)
        elif isinstance(stmt, (ast.Expr, ast.Return)):
            if stmt.value is not None:
                self._check_expr_for_calls(stmt.value)
        elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            for sub in ("test", "iter"):
                if hasattr(stmt, sub):
                    self._check_expr_for_calls(getattr(stmt, sub))
            self._walk_body(stmt.body)
            self._walk_body(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                self._check_expr_for_calls(item.context_expr)
            self._walk_body(stmt.body)
        elif isinstance(stmt, ast.Try):
            self._walk_body(stmt.body)
            for handler in stmt.handlers:
                self._walk_body(handler.body)
            self._walk_body(stmt.orelse)
            self._walk_body(stmt.finalbody)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Verschachtelte Funktion: eigener Taint-Scope, hier nicht
            # weiterverfolgt (wird von der modulweiten Top-Level-Suche
            # separat als eigene Funktion erfasst).
            pass
        else:
            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, ast.expr):
                    self._check_expr_for_calls(child)

    def _check_expr_for_calls(self, expr: ast.expr | None) -> None:
        """Prüft jeden Aufruf innerhalb eines Ausdrucks.

        Parameters
        ----------
        expr : ast.expr or None
            Der Ausdruck; ``None`` wird ignoriert.
        """
        if expr is None:
            return
        for node in ast.walk(expr):
            if isinstance(node, ast.Call):
                self._check_call(node)

    def _check_call(self, call: ast.Call) -> None:
        """Prüft einen Aufruf auf einen Schreibzugriff nach ``data/``.

        Erkennt die Schreib-APIs aus ``WRITE_APIS_ARG0``, ``WRITE_APIS_SELF``,
        ``FREE_FUNCS_ARG0`` und ``FREE_FUNCS_DST``, ``open()`` mit schreibendem
        Modus (als freie Funktion, als ``Path.open`` und als ``modul.open``) sowie
        Aufrufe von ``ensure_dir``/``ensure_parent`` über lokale Aliase.

        Parameters
        ----------
        call : ast.Call
            Der Aufruf.
        """
        func = call.func
        if isinstance(func, ast.Attribute):
            method = func.attr
            if method in WRITE_APIS_ARG0:
                path_node = _first_matching_arg(call, WRITE_APIS_ARG0[method], 0)
                self._report_if_tainted(call, path_node, f".{method}(...)")
            elif method in WRITE_APIS_SELF:
                self._report_if_tainted(call, func.value, f".{method}(...)")
            elif method == "open":
                # ".open(...)" ist syntaktisch zweideutig zwischen zwei
                # Mustern - beide kommen in diesem Repo vor, beide werden
                # geprüft:
                #   (1) Path.open(mode=...)      - Empfänger IST der Pfad,
                #       das Modus-Argument steht an Position 0.
                #   (2) modul.open(path, mode)   - z. B. rasterio.open() -
                #       der Pfad ist das erste Positionsargument, der Modus
                #       das zweite.
                if _mode_is_write(call, mode_index=0):
                    self._report_if_tainted(call, func.value, ".open(mode=...)  [Muster 1: Path.open]")
                if len(call.args) >= 1 and _mode_is_write(call, mode_index=1):
                    self._report_if_tainted(
                        call, call.args[0], "<modul>.open(path, 'w', ...)  [Muster 2: modul.open]"
                    )
            elif method in FREE_FUNCS_DST:
                idx, kw = FREE_FUNCS_DST[method]
                path_node = _first_matching_arg(call, (kw,), idx)
                self._report_if_tainted(call, path_node, f"shutil/os.{method}(...) [Ziel]")
        elif isinstance(func, ast.Name):
            if func.id == "open":
                if _mode_is_write(call, mode_index=1):
                    path_node = _first_matching_arg(call, ("file",), 0)
                    self._report_if_tainted(call, path_node, "open(..., mode=...)")
            elif func.id in FREE_FUNCS_ARG0:
                path_node = _first_matching_arg(call, FREE_FUNCS_ARG0[func.id], 0)
                self._report_if_tainted(call, path_node, f"{func.id}(...)")
            elif func.id in self.runtime_names:
                path_node = _first_matching_arg(call, ("path", "file_path"), 0)
                self._report_if_tainted(call, path_node, f"{func.id}(...)")

    def _report_if_tainted(self, call: ast.Call, path_node, desc: str) -> None:
        """Hängt einen Fund an, wenn das Pfad-Argument belastet ist.

        Parameters
        ----------
        call : ast.Call
            Der Aufruf, dessen Zeilennummer gemeldet wird.
        path_node : ast.expr or None
            Das Pfad-Argument; ``None`` wird ignoriert.
        desc : str
            Beschreibung des Aufrufs für den Fund.
        """
        if path_node is None:
            return
        if self.checker.is_tainted(path_node, self.tainted):
            try:
                src = ast.unparse(path_node)
            except Exception:
                src = "<Ausdruck>"
            self.findings.append(
                Finding(
                    path=self.file_path,
                    lineno=call.lineno,
                    call_desc=desc,
                    reason=f"Pfad-Argument '{src}' stammt aus data/",
                )
            )


def _scan_file(file_path: Path, findings: list[Finding]) -> None:
    """Scannt eine Python-Datei und hängt Funde an.

    Jede Funktion bekommt einen eigenen Taint-Scope, die Modulebene einen
    weiteren. Nicht lesbare oder nicht parsebare Dateien werden übersprungen.

    Parameters
    ----------
    file_path : Path
        Die Datei.
    findings : list of Finding
        Liste, an die Funde angehängt werden.
    """
    try:
        src = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    try:
        tree = ast.parse(src, filename=str(file_path))
    except SyntaxError:
        return

    contract_modules, contract_names = _contract_bindings(tree)
    runtime_names = _runtime_bindings(tree)
    checker = _TaintChecker(contract_modules, contract_names)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scanner = _FunctionScanner(checker, runtime_names, findings, file_path)
            scanner.run(node)

    # Modul-Ebene (außerhalb jeder Funktion): eigener, leerer Taint-Scope.
    module_level_body = [
        stmt
        for stmt in tree.body
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    scanner = _FunctionScanner(checker, runtime_names, findings, file_path)
    scanner._walk_body(module_level_body)

    # Methoden in Klassenkörpern (z. B. `class X: def f(self, ...): ...`)
    # werden bereits über den obigen ast.walk() erfasst, da ast.walk() auch
    # in ClassDef-Bodies absteigt und dort FunctionDef-Knoten findet.


@dataclass(frozen=True)
class CheckResult:
    findings: list[Finding]
    files_scanned: int

    @property
    def ok(self) -> bool:
        """Ob kein Fund vorliegt.

        Returns
        -------
        bool
            ``True``, wenn ``findings`` leer ist.
        """
        return not self.findings


def run_check(repo_root: Path) -> CheckResult:
    """Scannt alle Python-Dateien unter ``SCAN_DIRS``.

    Übersprungen werden ``__pycache__`` und die beiden Wächter selbst unter
    ``tools/`` (``SELF_EXCLUDE``).

    Parameters
    ----------
    repo_root : Path
        Wurzel des Repos.

    Returns
    -------
    CheckResult
        Funde und Zahl der gescannten Dateien.
    """
    findings: list[Finding] = []
    files_scanned = 0
    for dirname in SCAN_DIRS:
        base = repo_root / dirname
        if not base.exists():
            continue
        for py_path in sorted(base.rglob("*.py")):
            if "__pycache__" in py_path.parts:
                continue
            if py_path.name in SELF_EXCLUDE and py_path.parent == repo_root / "tools":
                continue
            files_scanned += 1
            _scan_file(py_path, findings)
    return CheckResult(findings, files_scanned)


def format_report(result: CheckResult, repo_root: Path) -> str:
    """Formatiert das Prüfergebnis als Textbericht.

    Parameters
    ----------
    result : CheckResult
        Ergebnis aus ``run_check()``.
    repo_root : Path
        Wurzel, relativ zu der die Fundpfade ausgegeben werden.

    Returns
    -------
    str
        Eine ``VERSTOSS``-Zeile je Fund, nach Pfad und Zeile sortiert, dazu
        eine abschließende ``OK``- oder ``FEHLGESCHLAGEN``-Zeile.
    """
    lines: list[str] = []
    for f in sorted(result.findings, key=lambda x: (str(x.path), x.lineno)):
        try:
            rel = f.path.relative_to(repo_root)
        except ValueError:
            rel = f.path
        lines.append(f"VERSTOSS: {rel}:{f.lineno}  {f.call_desc}  -  {f.reason}")
    if result.ok:
        lines.append(
            f"OK: {result.files_scanned} Python-Dateien geprüft "
            f"({', '.join(SCAN_DIRS)}) — kein Schreibzugriff nach data/ gefunden."
        )
    else:
        lines.append(
            f"FEHLGESCHLAGEN: {len(result.findings)} möglicher Schreibzugriff/e "
            f"nach data/ gefunden."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Führt den statischen Wächter aus und gibt den Bericht aus.

    Parameters
    ----------
    argv : list of str, optional
        Wird nicht ausgewertet.

    Returns
    -------
    int
        0 ohne Fund, sonst 1.
    """
    result = run_check(REPO_ROOT)
    print(format_report(result, REPO_ROOT))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
