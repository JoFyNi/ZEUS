"""Die Eingangsstufe: Text so aufbereiten, dass nichts verloren geht, was spaeter gebraucht wird.

Anlass (Audit vom 12.10.2026)
-----------------------------
`vault.fold()` ersetzte alles ausser a-z und 0-9 durch Leerzeichen. Gemessen:

    fold("Привет мир")        -> ""
    fold("日本語のテキスト")    -> ""
    fold("København")         -> "k benhavn"
    similarity("Москва", "Москва") -> 0.0

Jeder Abgleich, der auf `fold` aufsetzt - Stichwortsuche, Notizauflösung,
Dublettenprüfung im Gedaechtnis -, war fuer nicht-lateinische Schrift blind.
Eine Notiz namens "Москва" war per Name nicht auffindbar, zwei identische
russische Saetze galten als verschieden.

Was hier steht - und was bewusst NICHT
--------------------------------------
* **Normalisieren heisst NFC, nicht mehr.** NFC ist kanonische Aequivalenz:
  "Ü" als ein Zeichen und "U" + Trema sind danach dasselbe Zeichen, sonst
  aendert sich nichts. Ohne das trifft ein aus macOS eingefuegtes "Überdenke"
  kein einziges Muster der Triage. Code, Formeln, Zeilenumbrueche bleiben
  unangetastet - die exakte Schreibweise ist oft genau das, worum es geht.
* **Die Tokenisierung des Modells macht das Modell.** Ollama zerlegt den Text
  selbst; einen Tokenizer-Endpunkt, den man vorher fragen koennte, bietet es
  nicht. `textlast()` ist deshalb eine SCHAETZUNG der Last im Kontextfenster,
  kalibriert auf die Faustregel des Projekts (~3,3 Zeichen je Token bei
  deutschem Text, siehe `config.prompt_char_budget`). Fuer lateinische Schrift
  ist sie exakt die Zeichenzahl - am bisherigen Verhalten aendert sich fuer
  deutschen Text also nichts.
* **Stichwortzerlegung ist nicht Verstehen.** `stichwoerter_cjk()` zerlegt
  chinesische/japanische Zeichenketten ohne Leerzeichen in Zeichenpaare, damit
  der Stichwortabgleich ueberhaupt etwas zum Vergleichen hat. Ueber Bedeutung
  sagt das nichts - die steckt im Einbettungsmodell und im Sprachmodell.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List

# ---------------------------------------------------------------- Schriften
# Grobe Bereiche je Schrift. Reicht fuer Lastschaetzung und Zerlegung; eine
# vollstaendige Unicode-Schrifttabelle braeuchte eine Abhaengigkeit.
_BEREICHE = (
    (0x0000, 0x024F, "latein"), (0x1E00, 0x1EFF, "latein"),
    (0x2000, 0x206F, "latein"),            # Satzzeichen wie – “ ” …
    (0x0370, 0x03FF, "griechisch"), (0x1F00, 0x1FFF, "griechisch"),
    (0x0400, 0x052F, "kyrillisch"),
    (0x0590, 0x05FF, "hebraeisch"),
    (0x0600, 0x06FF, "arabisch"), (0x0750, 0x077F, "arabisch"),
    (0x0900, 0x0DFF, "indisch"),
    (0x0E00, 0x0E7F, "thai"),
    (0x1100, 0x11FF, "cjk"), (0x2E80, 0x9FFF, "cjk"), (0xAC00, 0xD7AF, "cjk"),
    (0xF900, 0xFAFF, "cjk"), (0xFF00, 0xFFEF, "cjk"), (0x20000, 0x2FA1F, "cjk"),
    (0x2190, 0x2BFF, "symbol"), (0x1F000, 0x1FAFF, "symbol"),
)

# Geschaetzte Last je Zeichen, relativ zu lateinischem Text. Grundlage sind
# die ueblichen Verhaeltnisse moderner BPE-Tokenizer (Qwen, Llama 3): Deutsch
# ~3,3 Zeichen je Token, Kyrillisch ~2,5, Griechisch ~2, Arabisch ~2,3,
# indische Schriften ~1,5, CJK ~1,4, Emoji und Sonderzeichen oft 2-3 Token
# je Zeichen. Lieber zu hoch geschaetzt als zu niedrig: Zu niedrig heisst,
# Ollama schneidet still den Prompt-Anfang ab (Lehre vom 18.09.).
GEWICHT: Dict[str, float] = {
    "latein": 1.0, "kyrillisch": 1.3, "griechisch": 1.6, "arabisch": 1.4,
    "hebraeisch": 1.4, "thai": 2.0, "indisch": 2.2, "cjk": 2.4,
    "symbol": 2.5, "sonstige": 1.5,
}

# Alles ausserhalb von Latein und allgemeinen Satzzeichen. Fuer den schnellen
# Weg in `textlast`: Deutscher Text ohne solche Zeichen kostet keine Schleife.
_NICHT_LATEIN = re.compile(r"[^\u0000-ɏḀ-ỿ -⁯]")


def schrift(zeichen: str) -> str:
    """Die Schrift eines Zeichens - grob, siehe `_BEREICHE`."""
    punkt = ord(zeichen)
    for anfang, ende, name in _BEREICHE:
        if anfang <= punkt <= ende:
            return name
    return "sonstige"


def schriften(text: str) -> Dict[str, int]:
    """Wie viele Buchstaben welcher Schrift im Text stehen (ohne Satzzeichen)."""
    zaehlung: Dict[str, int] = {}
    for zeichen in text or "":
        if zeichen.isalpha():
            name = schrift(zeichen)
            zaehlung[name] = zaehlung.get(name, 0) + 1
    return zaehlung


# ------------------------------------------------------------- Normalisieren
def normalisieren(text: str) -> str:
    """NFC und ein fuehrendes BOM weg - sonst nichts.

    Das BOM (U+FEFF) kommt mit, wenn man den Inhalt einer Datei einfuegt, die
    ein Windows-Editor gespeichert hat. Es ist unsichtbar und bricht jedes
    Muster, das auf den Zeilenanfang schaut (`^\\s*erledigt\\s+ist\\s+nicht`).
    """
    text = str(text or "")
    if text.startswith("﻿"):
        text = text[1:]
    return unicodedata.normalize("NFC", text)


_CODE_RE = re.compile(r"```|^( {4}|\t)\S|`[^`\n]+`", re.MULTILINE)
_FORMEL_RE = re.compile(
    r"[=<>≤≥≈≠±×÷√∑∏∫∂∞^²³]|\d\s*[+\-*/]\s*\d|[∀-⋿]")


@dataclass
class Eingang:
    """Die aufbereitete Eingabe - das Original bleibt daneben erhalten."""

    original: str
    text: str
    schriften: Dict[str, int] = field(default_factory=dict)
    hat_code: bool = False
    hat_formel: bool = False

    @property
    def normalisiert(self) -> bool:
        """Hat NFC etwas geaendert? Dann kam die Eingabe zerlegt an."""
        return self.text != self.original

    def als_dict(self) -> dict:
        return {"normalisiert": self.normalisiert, "schriften": dict(self.schriften),
                "code": self.hat_code, "formel": self.hat_formel}


def aufbereiten(text: str) -> Eingang:
    """Stufe 1: Darstellung vereinheitlichen und Merkmale ablesen.

    Merkmale, keine Deutung: Ob die Eingabe Code oder eine Formel enthaelt,
    entscheidet hier ein Muster - was sie bedeutet, entscheidet das Modell.
    """
    original = str(text or "")
    sauber = normalisieren(original)
    return Eingang(original=original, text=sauber, schriften=schriften(sauber),
                   hat_code=bool(_CODE_RE.search(sauber)),
                   hat_formel=bool(_FORMEL_RE.search(sauber)))


# ----------------------------------------------------------------- Lastschaetzung
def textlast(text: str) -> int:
    """Geschaetzte Last eines Textes im Kontextfenster, in 'lateinischen Zeichen'.

    Fuer rein lateinischen Text ist das genau `len(text)` - die Budgets des
    Projekts sind in solchen Zeichen gerechnet und bleiben gueltig. Jedes
    Zeichen einer dichter tokenisierten Schrift zaehlt mit seinem Gewicht.
    """
    text = text or ""
    fremd = _NICHT_LATEIN.findall(text)
    if not fremd:
        return len(text)
    zuschlag = sum(GEWICHT.get(schrift(z), GEWICHT["sonstige"]) - 1.0 for z in fremd)
    return int(len(text) + zuschlag + 0.5)


def auf_last_kuerzen(text: str, last: int) -> str:
    """Den laengsten Anfang von `text`, dessen `textlast` hoechstens `last` ist."""
    text = text or ""
    if last <= 0:
        return ""
    if textlast(text) <= last:
        return text
    if not _NICHT_LATEIN.search(text):
        return text[:last]
    summe = 0.0
    for stelle, zeichen in enumerate(text):
        gewicht = 1.0 if not _NICHT_LATEIN.match(zeichen) else \
            GEWICHT.get(schrift(zeichen), GEWICHT["sonstige"])
        if summe + gewicht > last:
            return text[:stelle]
        summe += gewicht
    return text


# ---------------------------------------------------------------- Zerlegung
_CJK_LAUF = re.compile(r"[぀-ヿ㐀-鿿가-힯豈-﫿]+")
_CJK_TRENNER = re.compile(r"([぀-ヿ㐀-鿿가-힯豈-﫿]+)")


def ist_cjk_wort(wort: str) -> bool:
    return bool(wort) and bool(_CJK_LAUF.fullmatch(wort))


def _zeichenpaare(lauf: str) -> List[str]:
    """"東京タワー" -> ["東京", "京タ", "タワ", "ワー"]. Ein einzelnes Zeichen
    bleibt stehen: Im Chinesischen ist es oft schon ein ganzes Wort."""
    if len(lauf) <= 2:
        return [lauf]
    return [lauf[i:i + 2] for i in range(len(lauf) - 1)]


def cjk_zerlegen(wort: str):
    """None, wenn das Wort keine CJK-Zeichen enthaelt.

    Sonst: die CJK-Laeufe als ueberlappende Zeichenpaare, die uebrigen Teile
    ("東京tower" -> "tower") unveraendert. Gemischte Woerter kommen vor, weil
    Japanisch lateinische Fachbegriffe ohne Leerzeichen einbettet.
    """
    if not _CJK_LAUF.search(wort or ""):
        return None
    aus: List[str] = []
    for teil in _CJK_TRENNER.split(wort):
        if not teil:
            continue
        aus.extend(_zeichenpaare(teil) if _CJK_LAUF.fullmatch(teil) else [teil])
    return aus
