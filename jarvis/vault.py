"""Vault-Verständnis: Ordnerbaum, Frontmatter, Tags, Wikilinks, Überschriften.

Das ist der Teil, der aus einem Haufen .md-Dateien eine *Struktur* macht:
welche Ordner existieren, was liegt darin, wie heißen die Notizen wirklich,
welche Tags und Links verbinden sie.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml  # optional, aber empfohlen
except ImportError:  # pragma: no cover
    yaml = None

IGNORED_DIRS = {
    ".obsidian", ".trash", ".git", ".smart-env", ".jarvis", "node_modules",
    "__pycache__", ".stfolder", "_Papierkorb", "_papierkorb",
}
TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
CANVAS_SUFFIX = ".canvas"

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")
INLINE_TAG_RE = re.compile(r"(?:(?<=\s)|\A)#([A-Za-zÄÖÜäöüß0-9][\w/\-äöüÄÖÜß]{1,48})")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
CODEFENCE_RE = re.compile(r"^\s*(```|~~~)")


# --------------------------------------------------------------------- Helpers
# Lateinische Buchstaben, die NFKD NICHT in Grundbuchstabe + Akzent zerlegt.
# Ohne diese Tabelle wurde aus "København" "k benhavn" und aus "Łódź" "odz".
_UMSCHRIFT = str.maketrans({"ł": "l", "đ": "d", "ø": "o", "æ": "ae", "œ": "oe",
                            "þ": "th", "ð": "d", "ı": "i", "ħ": "h"})


def fold(text: str) -> str:
    """Klein, ohne Umlaute/Akzente/Satzzeichen - für robustes Vergleichen.

    Buchstaben JEDER Schrift bleiben stehen. Bis zum 12.10.2026 blieb nur
    a-z0-9 übrig: "Москва" wurde zu "", und zwei identische russische Namen
    hatten die Ähnlichkeit 0 (siehe sprache.py). Für lateinischen Text ist das
    Ergebnis dasselbe wie vorher.
    """
    text = text.lower()
    text = (
        text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    )
    text = text.translate(_UMSCHRIFT)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Wieder zusammensetzen: NFKD zerlegt Hangul-Silben in Einzellaute.
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"[\W_]+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    """0..1 Ähnlichkeit zweier Namen, tolerant gegen Tippfehler ('Depp'/'Deep')."""
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    base = SequenceMatcher(None, fa, fb).ratio()
    # Wortweise Teiltreffer belohnen ("deep learning" in "01 skript deep learning")
    wa, wb = set(fa.split()), set(fb.split())
    if wa and wb:
        overlap = len(wa & wb) / len(wa | wb)
        base = max(base, 0.55 * base + 0.45 * overlap)
        if (wa <= wb or wb <= wa) and _teilmenge_taugt(wa, wb):
            base = max(base, 0.85)
    return base


def _teilmenge_taugt(wa: set, wb: set) -> bool:
    """Ist "der eine Name steckt im anderen" hier ein echtes Argument?

    Für zwei Wörter ist es eins: 'deep learning' in '01 skript deep learning'
    meint dieselbe Sache. Für EIN Wort ist es keins - ein Einwortname steckt in
    fast jedem längeren Namen. Genau daran ist 'ARVIS Architektur Overview' auf
    die bestehende Notiz 'Architektur' gefallen: Teilmenge, pauschal 0,85, über
    jeder Schwelle. Drei Verknüpfungen landeten an der falschen Notiz.

    Zusätzlich muss der kürzere Name mindestens die Hälfte des längeren
    ausmachen; sonst ist er ein Stichwort und keine Bezeichnung.
    """
    klein, gross = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(klein) >= 2 and len(klein) / len(gross) >= 0.5


# Zählwörter, die als Unterscheidungsmerkmal in einem Namen stehen können.
# Sie sind der Grund, warum 'MerkzettelZwei' KEIN Tippfehler von
# 'MerkzettelEins' ist - obwohl beide zu 86 % übereinstimmen.
_ZAEHLWOERTER = {
    "eins", "zwei", "drei", "vier", "fuenf", "fünf", "sechs", "sieben",
    "acht", "neun", "zehn", "elf", "zwoelf", "zwölf",
    "erste", "erster", "erstes", "zweite", "zweiter", "zweites",
    "dritte", "dritter", "drittes", "vierte", "vierter", "viertes",
    "alt", "neu", "alte", "neue", "final", "entwurf",
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "first", "second", "third", "old", "new", "draft", "final",
    "a", "b", "c", "d", "i", "ii", "iii", "iv", "v",
}


def _marke(rest: str) -> bool:
    """Ist dieser Wortrest ein Unterscheidungsmerkmal statt eines Tippfehlers?"""
    return bool(rest) and (rest.isdigit() or rest in _ZAEHLWOERTER)


def _unterscheidet_sich_bewusst(a: str, b: str) -> bool:
    """Sind zwei Namen absichtlich verschieden - oder ist es ein Tippfehler?

    'Depp Learning' / 'Deep Learning' → Tippfehler (langes Wort, ein Dreher).
    'Teil A' / 'Teil B'               → Unterscheidungsmerkmal, nicht angleichen.
    'Agenten 4' / 'Agenten 5'         → dito.
    'Übersicht X' / 'X'               → zusätzliches Wort, eigenständiger Name.
    'MerkzettelZwei' / 'MerkzettelEins' → dito, und das war der Fehlerfall.

    Der letzte Fall ist am 21.09. im Abnahmetest aufgefallen und ist der
    teuerste: Ein Auftrag verlangte nacheinander 'MerkzettelEins' und
    'MerkzettelZwei'. Beide bestehen aus EINEM Wort, unterscheiden sich nur
    in diesem einen, und beide Wörter sind länger als drei Zeichen - die
    bisherigen Ausnahmen griffen also alle nicht. Ergebnis: Die zweite Notiz
    hieß wieder 'MerkzettelEins' und landete als 'MerkzettelEins_2.md' in
    der Vault. Die Datei existierte, der Inhalt stimmte, die Verifikation
    war zufrieden - nur der Name war der falsche. Genau die Sorte stiller
    Fehlschlag, die dieses Projekt am teuersten bezahlt.

    Der Unterschied zu einem Tippfehler liegt nicht in der Ähnlichkeit,
    sondern darin, WO die Abweichung sitzt: Ein Zähler oder Kennzeichen
    hängt HINTEN an einem gemeinsamen Stamm ('merkzettel' + eins/zwei), ein
    Tippfehler sitzt mittendrin ('de' + pp/ep).
    """
    ta, tb = fold(a).split(), fold(b).split()
    if not ta or not tb:
        return True
    if len(ta) != len(tb):
        return True                     # ein Wort mehr = anderer Begriff
    unterschiede = [(x, y) for x, y in zip(ta, tb) if x != y]
    if not unterschiede:
        return False
    for x, y in unterschiede:
        # Kurze oder numerische Abweichungen sind Zähler/Kennzeichen, keine Tippfehler
        if x.isdigit() or y.isdigit() or len(x) <= 3 or len(y) <= 3:
            return True
        # Gemeinsamer Stamm, unterschiedliche Endung: 'Kapitel1'/'Kapitel2',
        # 'MerkzettelEins'/'MerkzettelZwei', 'PlanAlt'/'PlanNeu'.
        gemeinsam = 0
        for zeichen_x, zeichen_y in zip(x, y):
            if zeichen_x != zeichen_y:
                break
            gemeinsam += 1
        if gemeinsam >= 4 and (_marke(x[gemeinsam:]) or _marke(y[gemeinsam:])):
            return True
    return False


def safe_filename(name: str) -> str:
    """Dateiname säubern - aber der führende Unterstrich bleibt.

    In Obsidian ist '_Lernen' oder '_System' eine gängige Konvention, um Ordner
    nach oben zu sortieren. Ein pauschales strip('._') hat daraus stillschweigend
    'Lernen' gemacht: Der Agent legte Notizen in einem NEUEN Ordner ab, während
    der eigentliche daneben unberührt blieb.
    """
    name = name.strip().replace(" ", "_")
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r"\.{2,}", ".", name)
    name = name.lstrip(".")          # versteckte Dateien verhindern
    name = name.rstrip("._ ")        # Punkt/Unterstrich am ENDE ist nur Ballast
    return name[:120] or "notiz"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def strip_frontmatter(raw: str) -> Tuple[dict, str]:
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    block, body = match.group(1), raw[match.end() :]
    data: dict = {}
    if yaml is not None:
        try:
            parsed = yaml.safe_load(block)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            data = {}
    if not data:  # Mini-Parser als Fallback
        current_key = None
        for line in block.splitlines():
            if not line.strip():
                continue
            if line.lstrip().startswith("-") and current_key:
                data.setdefault(current_key, [])
                if isinstance(data[current_key], list):
                    data[current_key].append(line.lstrip()[1:].strip())
            elif ":" in line:
                key, _, value = line.partition(":")
                current_key = key.strip()
                value = value.strip()
                if value.startswith("[") and value.endswith("]"):
                    data[current_key] = [v.strip(" '\"") for v in value[1:-1].split(",") if v.strip()]
                elif value:
                    data[current_key] = value
                else:
                    data[current_key] = []
    return data, body


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,\n]", value) if v.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def strip_code_blocks(body: str) -> str:
    out, inside = [], False
    for line in body.splitlines():
        if CODEFENCE_RE.match(line):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


# Text in `Backticks` - eine einzelne Zeile Code mitten im Absatz.
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Ein Platzhalter aus einem f-String: [[{note.stem}]], [[{ziel}]], [[{v}]].
# Das ist KEIN Link, sondern Quelltext, der zufällig in einer Notiz steht.
PLATZHALTER_RE = re.compile(r"[{}]")


def wikilinks_finden(body: str) -> list:
    """Alle ECHTEN [[Wikilinks]] eines Textes - ohne die aus Quelltext.

    Der Grund steht in einem Protokoll vom 01.09.: `/links reparieren` meldete
    48 offene Links, darunter `[[Notizname]]`, `[[{note.stem}]]`, `[[Links]]`
    und `[[{v}]]`. Keiner davon ist ein Link. Es sind Beispiele und f-String-
    Platzhalter aus den Docstrings des eigenen Quellcodes, den `/codebasis` als
    Notizen in die Vault gelegt hat.

    Die Folge war nicht bloß Rauschen: Ein Bericht mit 48 Fehlalarmen wird nach
    dem zweiten Mal nicht mehr gelesen, und `reparieren=True` hätte versucht,
    diese Platzhalter auf zufällig ähnliche Notizen umzubiegen - also echten
    Quelltext verändert.

    Deshalb: erst Codeblöcke und Inline-Code entfernen, dann suchen, und alles
    mit geschweifter Klammer verwerfen.
    """
    ohne_code = INLINE_CODE_RE.sub(" ", strip_code_blocks(body or ""))
    gefunden = []
    for treffer in WIKILINK_RE.finditer(ohne_code):
        ziel = treffer.group(1).strip()
        if not ziel or PLATZHALTER_RE.search(ziel):
            continue
        gefunden.append(ziel)
    return gefunden


# ----------------------------------------------------------------------- Notiz
@dataclass
class Note:
    path: Path
    rel_path: str          # z. B. "Deep Learning/01_Skript_Deep_Learning.md"
    folder: str            # z. B. "Deep Learning" ("" = Vault-Wurzel)
    stem: str              # Dateiname ohne Endung
    title: str             # H1 oder Frontmatter-title oder stem
    body: str
    frontmatter: dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    mtime: float = 0.0
    size: int = 0
    checksum: str = ""

    @property
    def display(self) -> str:
        return f"{self.folder}/{self.stem}" if self.folder else self.stem

    def summary_line(self) -> str:
        parts = [f"[[{self.stem}]]"]
        if self.tags:
            parts.append("Tags: " + ", ".join("#" + t for t in self.tags[:6]))
        if self.headings:
            parts.append("Abschnitte: " + " | ".join(self.headings[:5]))
        return " - ".join(parts)


def parse_canvas(raw: str) -> str:
    """Obsidian-Canvas: Textknoten einsammeln, damit auch die durchsuchbar sind."""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    lines = []
    for node in data.get("nodes", []):
        if node.get("type") == "text" and node.get("text"):
            lines.append(node["text"])
        elif node.get("type") == "file" and node.get("file"):
            lines.append(f"Verknüpfte Datei: [[{Path(node['file']).stem}]]")
    return "\n\n".join(lines)


def parse_note(path: Path, vault_root: Path) -> Optional[Note]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if path.suffix.lower() == CANVAS_SUFFIX:
        frontmatter, body = {}, parse_canvas(raw)
    else:
        frontmatter, body = strip_frontmatter(raw)

    rel = path.relative_to(vault_root).as_posix()
    folder = str(Path(rel).parent.as_posix())
    folder = "" if folder == "." else folder

    clean = strip_code_blocks(body)
    headings = [m.group(2).strip() for m in (HEADING_RE.match(l) for l in clean.splitlines()) if m]
    h1 = next((m.group(2).strip() for m in (HEADING_RE.match(l) for l in clean.splitlines())
               if m and len(m.group(1)) == 1), "")

    tags = {t.lstrip("#").strip() for t in _as_list(frontmatter.get("tags")) if t}
    tags |= {m.group(1) for m in INLINE_TAG_RE.finditer(clean)}
    tags = {t for t in tags if t and not t.isdigit()}

    links = set(wikilinks_finden(body))
    aliases = _as_list(frontmatter.get("aliases")) + _as_list(frontmatter.get("alias"))

    title = str(frontmatter.get("title") or h1 or path.stem).strip()
    stat = path.stat()

    return Note(
        path=path,
        rel_path=rel,
        folder=folder,
        stem=path.stem,
        title=title,
        body=body,
        frontmatter=frontmatter,
        tags=sorted(tags),
        links=sorted(links),
        aliases=aliases,
        headings=headings,
        mtime=stat.st_mtime,
        size=stat.st_size,
        checksum=sha1(raw),
    )


def scan_vault(vault_root: Path) -> List[Note]:
    vault_root = Path(vault_root).expanduser().resolve()
    if not vault_root.is_dir():
        from .config import ENV_FILE

        quelle = f"laut {ENV_FILE}" if ENV_FILE else "aus dem Standardwert (keine .env gefunden!)"
        raise FileNotFoundError(
            f"Vault-Ordner nicht gefunden: {vault_root} ({quelle}). "
            "Setze OBSIDIAN_VAULT in der .env oder nutze im Chat /vault <Pfad>."
        )

    notes: List[Note] = []
    for path in sorted(vault_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS or part.startswith(".") for part in path.relative_to(vault_root).parts[:-1]):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES | {CANVAS_SUFFIX}:
            continue
        note = parse_note(path, vault_root)
        if note:
            notes.append(note)
    return notes


# ------------------------------------------------------------------- Struktur
@dataclass
class FolderInfo:
    path: str                       # "" = Wurzel
    name: str                       # letzter Pfadteil
    note_count: int = 0
    tags: List[str] = field(default_factory=list)
    titles: List[str] = field(default_factory=list)
    subfolders: List[str] = field(default_factory=list)

    def profile_text(self) -> str:
        """Kurzbeschreibung des Ordners - Basis für semantische Zuordnung."""
        bits = [f"Ordner: {self.path or 'Vault-Wurzel'}"]
        if self.titles:
            bits.append("Notizen: " + ", ".join(self.titles[:12]))
        if self.tags:
            bits.append("Tags: " + ", ".join(self.tags[:12]))
        if self.subfolders:
            bits.append("Unterordner: " + ", ".join(self.subfolders[:8]))
        return " | ".join(bits)


class VaultStructure:
    """Der 'Bauplan' der Vault - wird bei jedem Scan neu aufgebaut (billig)."""

    def __init__(self, vault_root: Path, notes: Sequence[Note]):
        self.vault_root = Path(vault_root).expanduser().resolve()
        self.notes: List[Note] = list(notes)
        self.by_rel: Dict[str, Note] = {n.rel_path: n for n in self.notes}
        self.folders: Dict[str, FolderInfo] = {}
        self.tag_index: Dict[str, List[str]] = {}
        self.name_index: Dict[str, Note] = {}
        self._build()

    def _build(self) -> None:
        # Alle Ordner erfassen - auch leere, denn ein leerer "Deep Learning"-Ordner
        # ist trotzdem der richtige Ablageort.
        all_dirs = {""}
        for path in self.vault_root.rglob("*"):
            if path.is_dir():
                rel = path.relative_to(self.vault_root).as_posix()
                if any(p in IGNORED_DIRS or p.startswith(".") for p in Path(rel).parts):
                    continue
                all_dirs.add(rel)
        for note in self.notes:
            parts = Path(note.folder).parts if note.folder else ()
            for i in range(len(parts)):
                all_dirs.add(Path(*parts[: i + 1]).as_posix())

        for rel in sorted(all_dirs):
            self.folders[rel] = FolderInfo(path=rel, name=Path(rel).name if rel else "Vault-Wurzel")

        for note in self.notes:
            info = self.folders.setdefault(note.folder, FolderInfo(path=note.folder, name=Path(note.folder).name))
            info.note_count += 1
            info.titles.append(note.title)
            for tag in note.tags:
                if tag not in info.tags:
                    info.tags.append(tag)
                self.tag_index.setdefault(tag.lower(), []).append(note.rel_path)
            for key in {note.stem.lower(), note.title.lower(), *(a.lower() for a in note.aliases)}:
                self.name_index.setdefault(key, note)

        for rel, info in self.folders.items():
            for other in self.folders:
                if not other:
                    continue
                parent = Path(other).parent.as_posix()
                parent = "" if parent == "." else parent
                if parent == rel:
                    info.subfolders.append(Path(other).name)

    # ---------------------------------------------------------------- Abfragen
    @property
    def folder_paths(self) -> List[str]:
        return [p for p in sorted(self.folders) if p]

    def note_count(self) -> int:
        return len(self.notes)

    def tree(self, max_notes_per_folder: int = 8) -> str:
        """Baumdarstellung für Terminal und LLM-Prompt."""
        lines = [f"{self.vault_root.name}/"]
        by_folder: Dict[str, List[Note]] = {}
        for note in self.notes:
            by_folder.setdefault(note.folder, []).append(note)

        for rel in sorted(self.folders):
            depth = 0 if not rel else len(Path(rel).parts)
            if rel:
                lines.append("  " * depth + f"📁 {Path(rel).name}/  ({self.folders[rel].note_count} Notizen)")
            for note in sorted(by_folder.get(rel, []), key=lambda n: n.stem)[:max_notes_per_folder]:
                lines.append("  " * (depth + 1) + f"📄 {note.stem}")
            rest = len(by_folder.get(rel, [])) - max_notes_per_folder
            if rest > 0:
                lines.append("  " * (depth + 1) + f"… {rest} weitere")
        return "\n".join(lines)

    def overview(self) -> str:
        """Kompakter Struktur-Text, der in jeden System-Prompt passt."""
        parts = [
            f"Vault '{self.vault_root.name}': {self.note_count()} Notizen in {len(self.folder_paths)} Ordnern."
        ]
        for rel in sorted(self.folder_paths):
            info = self.folders[rel]
            desc = f"- {rel}/ ({info.note_count})"
            if info.titles:
                desc += ": " + ", ".join(sorted(info.titles)[:6])
            parts.append(desc)
        root = self.folders.get("")
        if root and root.note_count:
            parts.append(f"- (Wurzel) ({root.note_count}): " + ", ".join(sorted(root.titles)[:6]))
        top_tags = sorted(self.tag_index.items(), key=lambda kv: -len(kv[1]))[:15]
        if top_tags:
            parts.append("Häufige Tags: " + ", ".join(f"#{t} ({len(v)})" for t, v in top_tags))
        return "\n".join(parts)

    def match_folder(self, text: str, threshold: float = 0.62) -> List[Tuple[str, float]]:
        """Ordner, deren Name zum Text passt - inkl. Tippfehler-Toleranz."""
        scored: List[Tuple[str, float]] = []
        for rel, info in self.folders.items():
            if not rel:
                continue
            score = max(similarity(text, info.name), similarity(text, rel.replace("/", " ")))
            # Tag-Treffer im Ordner zählen mit
            for tag in info.tags:
                score = max(score, 0.9 * similarity(text, tag.replace("_", " ").replace("/", " ")))
            if score >= threshold:
                scored.append((rel, score))
        return sorted(scored, key=lambda kv: -kv[1])

    def match_note(self, text: str, threshold: float = 0.72) -> List[Tuple[Note, float]]:
        scored = []
        for note in self.notes:
            score = max(
                similarity(text, note.stem),
                similarity(text, note.title),
                *(similarity(text, a) for a in note.aliases or [""]),
            )
            if score >= threshold:
                scored.append((note, score))
        return sorted(scored, key=lambda kv: -kv[1])

    def canonical_name(self, text: str, threshold: float = 0.82) -> str:
        """'Depp Learning' -> 'Deep Learning', wenn es so etwas in der Vault gibt.

        Aber NUR bei echten Tippfehlern. 'Teil B' darf nicht zu 'Teil A' werden und
        'Übersicht Deep Learning' nicht zu 'Deep Learning' - sonst tragen beim
        Aufteilen einer Notiz alle Kapitel denselben Namen.
        """
        best, best_score = text, threshold

        def pruefen(kandidat: str, score: float) -> None:
            nonlocal best, best_score
            if score > best_score and not _unterscheidet_sich_bewusst(text, kandidat):
                best, best_score = kandidat, score

        for rel, info in self.folders.items():
            if rel:
                pruefen(info.name, similarity(text, info.name))
        for note in self.notes:
            for candidate in {note.title, note.stem}:
                pruefen(candidate, similarity(text, candidate))
        for tag in self.tag_index:
            lesbar = tag.replace("_", " ")
            pruefen(lesbar.title(), similarity(text, lesbar))
        return best

    def resolve_link(self, name: str) -> Optional[Note]:
        return self.name_index.get(name.strip().lower())


# ------------------------------------------------------ Abschnitte / Chunking
def split_sections(body: str) -> List[Tuple[List[str], str]]:
    """Zerlegt einen Text an Überschriften in (Überschriften-Pfad, Abschnittstext).

    Code-Blöcke werden respektiert, damit '# Kommentar' in Python-Code keine
    Überschrift erzeugt. Wird sowohl fürs Chunking als auch fürs vollständige
    Nachladen eines Kapitels verwendet - so bekommt das Modell ganze Abschnitte
    statt willkürlich abgeschnittener Fragmente.
    """
    sections: List[Tuple[List[str], List[str]]] = []
    stack: List[str] = []
    current: List[str] = []
    inside_code = False

    def flush() -> None:
        if current and any(line.strip() for line in current):
            sections.append((list(stack), list(current)))
        current.clear()

    for line in body.splitlines():
        if CODEFENCE_RE.match(line):
            inside_code = not inside_code
            current.append(line)
            continue
        match = None if inside_code else HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            stack = stack[: level - 1] + [match.group(2).strip()]
            current.append(line)  # Überschrift bleibt Teil ihres Abschnitts
        else:
            current.append(line)
    flush()

    if not sections:
        return [([], body)]
    return [(heads, "\n".join(lines).strip()) for heads, lines in sections]


def extract_section(body: str, breadcrumb: str, max_chars: int = 6000) -> str:
    """Holt einen kompletten Abschnitt inklusive Unterabschnitten."""
    if not breadcrumb:
        return trim_at_boundary(body, max_chars)
    wanted = [p.strip() for p in breadcrumb.split(">") if p.strip()]
    collected: List[str] = []
    for heads, text in split_sections(body):
        if heads[: len(wanted)] == wanted:
            collected.append(text)
    if not collected:  # tolerant: nur die letzte Überschrift vergleichen
        last = wanted[-1].lower()
        for heads, text in split_sections(body):
            if heads and heads[-1].lower() == last:
                collected.append(text)
    return trim_at_boundary("\n\n".join(collected).strip() or body, max_chars)


def trim_at_boundary(text: str, max_chars: int) -> str:
    """Kürzt nie mitten im Wort - schneidet an Zeilen-, sonst an Satzgrenze."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in ("\n\n", "\n", ". ", " "):
        pos = cut.rfind(sep)
        if pos > max_chars * 0.5:
            cut = cut[: pos + (len(sep) if sep != " " else 0)]
            break
    return cut.rstrip() + "\n\n[… gekürzt – vollständigen Text mit notiz_lesen anfordern]"


def chunk_note(note: Note, chunk_size: int, chunk_overlap: int) -> List[dict]:
    """Zerlegt eine Notiz in Abschnitte und hängt jedem den Struktur-Kontext an.

    Genau das macht den Unterschied: Ein Chunk weiß danach selbst, aus welchem
    Ordner, welcher Notiz und welchem Abschnitt er stammt - im Text *und* in
    den Metadaten. Damit findet die Vektorsuche auch strukturbezogene Fragen.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunks: List[dict] = []
    for headings, text in split_sections(note.body):
        if not text.strip():
            continue
        breadcrumb = " > ".join(headings)
        for piece in splitter.split_text(text):
            header = (
                f"[Vault] Ordner: {note.folder or 'Wurzel'} | Notiz: {note.title} "
                f"({note.stem}) | Abschnitt: {breadcrumb or 'Anfang'}"
            )
            if note.tags:
                header += " | Tags: " + ", ".join("#" + t for t in note.tags[:8])
            chunks.append(
                {
                    "text": f"{header}\n\n{piece.strip()}",
                    "metadata": {
                        "source": str(note.path),
                        "rel_path": note.rel_path,
                        "folder": note.folder,
                        "filename": note.path.name,
                        "stem": note.stem,
                        "title": note.title,
                        "headings": breadcrumb,
                        "tags": ", ".join(note.tags),
                        "links": ", ".join(note.links),
                    },
                }
            )
    return chunks


def folder_summary_chunks(structure: VaultStructure) -> List[dict]:
    """Zusätzliche 'Meta-Chunks': ein Eintrag pro Ordner + eine Gesamtübersicht.

    Dadurch kann der Agent Fragen wie 'Was liegt in Deep Learning?' direkt
    über die Vektorsuche beantworten, statt zu raten.
    """
    chunks: List[dict] = []
    for rel, info in structure.folders.items():
        if not rel and not info.note_count:
            continue
        text = (
            f"[Vault-Struktur] {info.profile_text()}\n"
            f"Anzahl Notizen: {info.note_count}."
        )
        chunks.append(
            {
                "text": text,
                "metadata": {
                    "source": f"vault-struktur::{rel or 'wurzel'}",
                    "rel_path": f"__struktur__/{rel or 'wurzel'}",
                    "folder": rel,
                    "filename": "Vault-Struktur",
                    "stem": "Vault-Struktur",
                    "title": f"Struktur: {rel or 'Wurzel'}",
                    "headings": "",
                    "tags": "",
                    "links": "",
                    "kind": "structure",
                },
            }
        )
    chunks.append(
        {
            "text": "[Vault-Struktur] Gesamtübersicht\n" + structure.overview(),
            "metadata": {
                "source": "vault-struktur::gesamt",
                "rel_path": "__struktur__/gesamt",
                "folder": "",
                "filename": "Vault-Struktur",
                "stem": "Vault-Struktur",
                "title": "Struktur: Gesamtübersicht",
                "headings": "",
                "tags": "",
                "links": "",
                "kind": "structure",
            },
        }
    )
    return chunks
