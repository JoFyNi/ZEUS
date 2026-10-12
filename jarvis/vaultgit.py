"""Git in der Vault: jede Aenderung von Zeus bekommt einen Commit.

Die Sicherungen unter `.jarvis/backups` kennen den letzten Stand. Sie
beantworten nicht die Frage, die bei unbeaufsichtigtem Betrieb als erste
kommt: **was hat Zeus letzten Dienstag eigentlich geaendert?** Genau dafuer
gibt es Git, und die Vault ist ein Ordner voller Textdateien - der Fall, fuer
den Git gebaut wurde.

Drei Festlegungen:

1. **Ein Git-Fehler darf einen Lauf nie kippen.** Ist kein Git installiert,
   ist die Vault kein Repository oder haengt ein Index-Lock, dann wird das
   protokolliert und weitergearbeitet. Die Alternative waere, dass ein
   fehlendes Programm das Schreiben von Notizen verhindert - absurd.
2. **Committet wird nach der Transaktion, nicht nach jeder Datei.** Ein
   Stapel von zehn Notizen ist eine Aenderung, kein Dutzend.
3. **Nichts wird je zurueckgesetzt.** Dieses Modul schreibt Commits und sonst
   nichts. Kein `reset`, kein `checkout`, kein `clean` - Wiederherstellung ist
   Handarbeit mit Blick auf den Diff, nicht etwas, das nebenbei passiert.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

# Was NICHT in die Historie gehoert. `.obsidian/workspace*` aendert sich bei
# jedem Fensterwechsel, `.jarvis/` ist Zeus' eigener Maschinenraum samt
# Sicherungen, `_Papierkorb/` ist per Definition das, was weg soll.
GITIGNORE = """# Von Zeus angelegt - siehe jarvis/vaultgit.py
.obsidian/workspace*
.obsidian/cache
.jarvis/
_Papierkorb/
.trash/
*.tmp
"""

# Laenger darf kein Git-Aufruf dauern. Ein haengendes Git blockiert sonst den
# Agenten - und der Commit ist Beiwerk, nicht der Zweck des Laufs.
FRIST = 30


class Vaultgit:
    """Fuehrt die Vault-Historie. Schweigsam im Normalfall, nie laut."""

    def __init__(self, wurzel: Path, melden: Optional[Callable[[str], None]] = None):
        self.wurzel = Path(wurzel)
        self.melden = melden or (lambda text: None)
        self._verfuegbar: Optional[bool] = None

    # ------------------------------------------------------------- Zustand
    def git_vorhanden(self) -> bool:
        """Gibt es ueberhaupt ein git auf diesem Rechner?"""
        if self._verfuegbar is None:
            self._verfuegbar = self._lauf(["--version"], in_vault=False).get("ok", False)
        return bool(self._verfuegbar)

    def ist_repo(self) -> bool:
        if not (self.wurzel / ".git").exists():
            return False
        return self._lauf(["rev-parse", "--is-inside-work-tree"]).get("ok", False)

    # ---------------------------------------------------------- Einrichten
    def einrichten(self) -> tuple:
        """Legt das Repository an. Gibt (geglueckt, Meldung) zurueck."""
        if not self.git_vorhanden():
            return False, ("Git ist auf diesem Rechner nicht installiert. "
                           "Ohne Git bleibt es bei den Sicherungen unter .jarvis/backups.")
        if self.ist_repo():
            return True, "Die Vault ist bereits ein Git-Repository."
        if not self.wurzel.exists():
            return False, f"Die Vault gibt es nicht: {self.wurzel}"

        ergebnis = self._lauf(["init"])
        if not ergebnis.get("ok"):
            return False, f"git init fehlgeschlagen: {ergebnis.get('fehler', '')[:200]}"

        pfad = self.wurzel / ".gitignore"
        if not pfad.exists():
            try:
                pfad.write_text(GITIGNORE, encoding="utf-8")
            except OSError as exc:
                return False, f".gitignore nicht schreibbar: {exc}"

        # Ein Erstcommit, damit es einen Stand gibt, gegen den spaeter
        # verglichen wird. Ohne ihn zeigt der erste Diff die ganze Vault.
        self._lauf(["add", "-A"])
        self._lauf(["commit", "-m", "Erster Stand der Vault", "--allow-empty"])
        return True, (f"Die Vault ist jetzt ein Git-Repository. Jede Aenderung von "
                      f"Zeus bekommt ab sofort einen Commit.")

    # ------------------------------------------------------------ Sichern
    def sichern(self, beschreibung: str, aktion: str = "") -> bool:
        """Committet den aktuellen Stand. Wirft NIE.

        Rueckgabe sagt nur, ob es geklappt hat - der Aufrufer darf sie
        ignorieren, und genau das tut die Transaktion auch.
        """
        try:
            if not self.ist_repo():
                return False
            if not self._lauf(["add", "-A"]).get("ok"):
                return False
            # Nichts geaendert? Dann auch kein Commit - eine Historie voller
            # leerer Eintraege ist keine Historie.
            if self._lauf(["diff", "--cached", "--quiet"]).get("ok"):
                return False
            text = " ".join(str(beschreibung or "Aenderung").split())[:150]
            marke = f"zeus{'/' + aktion if aktion else ''}: {text}"
            ergebnis = self._lauf(["commit", "-m", marke])
            if not ergebnis.get("ok"):
                self.melden(f"Vault-Commit fehlgeschlagen: "
                            f"{ergebnis.get('fehler', '')[:150]}")
                return False
            return True
        except Exception as exc:                       # bewusst alles
            # Ein Commit ist Beiwerk. Er darf den Lauf nicht mitreissen.
            self.melden(f"Vault-Commit uebersprungen: {type(exc).__name__}: {exc}")
            return False

    def letzte(self, anzahl: int = 5) -> list:
        """Die juengsten Commits - fuer /verlauf und den Morgenbericht."""
        ergebnis = self._lauf(["log", f"-{max(1, int(anzahl))}",
                               "--pretty=format:%h\t%ad\t%s", "--date=format:%d.%m. %H:%M"])
        if not ergebnis.get("ok"):
            return []
        zeilen = []
        for zeile in (ergebnis.get("ausgabe") or "").splitlines():
            teile = zeile.split("\t", 2)
            if len(teile) == 3:
                zeilen.append({"kennung": teile[0], "zeit": teile[1], "text": teile[2]})
        return zeilen

    # --------------------------------------------------------------- intern
    def _lauf(self, argumente, in_vault: bool = True) -> dict:
        befehl = ["git"]
        if in_vault:
            befehl += ["-C", str(self.wurzel)]
        befehl += [str(a) for a in argumente]
        try:
            fertig = subprocess.run(befehl, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=FRIST)
            return {"ok": fertig.returncode == 0,
                    "ausgabe": fertig.stdout or "",
                    "fehler": fertig.stderr or ""}
        except FileNotFoundError:
            return {"ok": False, "fehler": "git nicht gefunden"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "fehler": f"git antwortete nicht innerhalb von {FRIST}s"}
        except Exception as exc:
            return {"ok": False, "fehler": f"{type(exc).__name__}: {exc}"}
