# Grundregeln für Zeus

Nicht als Leitbild, sondern als Arbeitsdokument. Jede Regel nennt den Vorfall,
der sie erzwungen hat, und **wo sie erzwungen wird**. Eine Regel ohne
Erzwingungsort ist ein Wunsch.

---

## 0. Die wichtigste Einsicht zuerst: Wo eine Regel lebt, entscheidet, ob sie gilt

Es gibt vier Orte, an denen du eine Regel hinschreiben kannst. Sie sind **nicht
gleichwertig** – das ist der Kernpunkt.

| Ebene | Ort | Bindungskraft | Prüfbar? |
|---|---|---|---|
| 1 | **Python-Code** (Guard, Prüfung, Rückgabewert) | absolut | ja, per Test |
| 2 | **Schema / Konfiguration** (`TOOL_SCHEMAS`, `.env`) | hoch, aber umgehbar | teilweise |
| 3 | **Systemprompt** | Bitte an das Modell | nein |
| 4 | **Notiz / README** | Gedächtnis für Menschen | nein |

**Belege aus dem eigenen Betrieb, warum das keine Theorie ist:**

- Im Entwicklungs-Dokument stand *„Der Prompt wird hart auf ~65 % des
  Kontextfensters begrenzt."* Gemessen wurden **15 547 Zeichen bei einem Budget
  von 14 000**. Die Regel stand auf Ebene 4, erzwungen wurde sie nirgends. Der
  Verlauf fiel deshalb still aus dem Fenster – monatelang unbemerkt.
- Der Systemprompt sagte *„Antworte auf Basis der Notizen"* und nannte die Vault
  *„dein Gedächtnis"*. Der Gesprächsverlauf kam darin nicht vor. Ergebnis: Zeus
  durchsuchte per `notizen_suchen` die **Vault** nach dem, was zwei Sätze vorher
  gesagt worden war. Er hat die Regel korrekt befolgt – die Regel war falsch.
- Die Synonymtabelle für Feldnamen lag nur im Planer (Ebene 1, aber am falschen
  Ort). Ein direkter Werkzeugaufruf mit `title=` statt `titel=` verlor das Feld
  stillschweigend.

**Merksatz:** Schreib die Regel auf Ebene 4, damit du sie in einem Jahr noch
verstehst. Erzwinge sie auf Ebene 1, damit sie gilt. Wenn beides nicht geht,
schreib in die Notiz dazu: *„nicht erzwungen"*.

---

## 1. Regeln über Wahrheit und Nachweis

### R1 – Kein Erfolg ohne Beleg
Zeus meldet einen Vorgang nur dann als erledigt, wenn ein **Rückgabewert aus dem
Code** das belegt – nie, weil das Modell es behauptet.

*Warum:* „Die Notiz '_Lernen/gleichstrom' wurde erfolgreich verschoben." – war
sie nicht.
*Erzwungen in:* `agent._tatsachen_anhaengen()`, `aufgaben.abarbeiten()`,
`evaluation` (Erfolgsbedingungen sind Daten, kein Modellurteil).
*Geprüft durch:* `tests/test_protokoll.py`, `tests/test_planlauf.py`.

### R2 – Eine Fehlermeldung muss handlungsfähig machen
Sie nennt: was fehlte, was erwartet wurde, und **welches Werkzeug stattdessen**
gemeint war.

*Warum:* `Falsche Parameter für 'notiz_erstellen': missing 2 required positional
arguments` – daraus kann ein Modell nichts ableiten, also probierte es dasselbe
erneut. Ebenso: `notizen_verknuepfen(nach="")` fünfmal identisch, weil das
Werkzeug zum Entfernen toter Links fehlte und die Meldung es nicht nannte.
*Erzwungen in:* `tools.ausfuehren()` (Pflichtfeldprüfung vor dem Aufruf),
`_WERKZEUG_HINWEISE` in `agent.py`.
*Geprüft durch:* `tests/test_parameter.py`.

### R3 – Was nicht messbar ist, gilt als unbewiesen
Eine Verbesserung ohne Messung ist eine Vermutung. Der eigene Stufenplan sagte
„Stufe 2 – Evaluation. Nicht überspringen" – und genau sie fehlte über Monate.

*Erzwungen in:* `evaluation.py` (A/B gegen eine **Kopie** der Vault).
*Achtung:* Unter zehn Prüfaufgaben ist die Quote Rauschen. Der Bericht sagt das
selbst dazu.

---

## 2. Regeln über Verluste

### R4 – Nichts geht unwiederbringlich verloren
Backup vor jeder Änderung, Journal, `/rueckgaengig`, Papierkorb statt Löschen,
Rollback ganzer Stapel, Pfade hart auf die Vault begrenzt.

*Diese Regel hat sich heute selbst bewährt:* Eine Bearbeitung landete auf der
falschen Notiz (`Agenten_7` statt `Agenten`, weil beide `title: "Agenten"`
tragen). Der Schaden war in 30 Sekunden zurückgenommen – **weil das Netz da
war**, nicht weil der Fehler nicht passiert wäre.
*Erzwungen in:* `tools._write()`, `_backup()`, `_journal()`, `transaktion()`.

### R5 – „Ungefähr" ist erlaubt beim Lesen, verboten beim Zerstören
Tippfehlertoleranz ist bei einer Suche richtig und beim Ganz-Ersetzen
zerstörerisch. Die Asymmetrie ist Absicht.

| Vorgang | Toleranz |
|---|---|
| Lesen, Suchen, Ergänzen, Abschnitt ersetzen | großzügig |
| `notiz_ueberschreiben`, Löschen, Zusammenführen | **exakter Name** |

*Warum:* Ein Prüfbericht überschrieb eine fremde Notiz, weil die Existenzprüfung
über `resolve_note` lief und notfalls semantisch weitersuchte.
*Erzwungen in:* `notiz_exakt()`, `UNSICHERE_STUFEN`, `_namen_pruefen()`.

### R6 – Zeus ändert seinen eigenen Code nicht
Er darf ihn lesen, verstehen und Verbesserungen mit Datei und Zeilennummer
vorschlagen. Schreiben nie.

*Warum:* Ein Agent, der sich umschreibt, während er läuft, ist eine Fehlerquelle
ohne Rückweg.
*Erzwungen in:* `CODE_ROOTS` nur lesend; kein Werkzeug schreibt dorthin.
*Diese Regel würde ich als letzte lockern – eher nie.*

---

## 3. Regeln über die Grenzen des Modells

### R7 – Die Schrittfolge gehört in Python, nicht in den Prompt
Ein Modell hält eine mehrstufige Abfolge über mehrere Werkzeugrunden nicht
zuverlässig durch. Dreimal unabhängig bestätigt: Schreibaufträge (→ Planer),
Code-Prüfung (→ Ablauf im Code), Fertigkeiten (→ deklarativer Ablaufplan).

*Konsequenz für Neues:* Wenn du dir wünschst „erst X, dann Y, dann Z" – schreib
es als Funktion, nicht als Prompt-Anweisung.

### R8 – Der Prompt ist ein Budget, kein Wunschzettel
Systemprompt, Verlauf und Frage teilen sich **ein** Kontextfenster. Wer nicht
teilt, verliert das, was zuletzt angehängt wurde – und zwar lautlos.

*Aufteilung:* Systemprompt höchstens 55 %, aber nur so viel, wie der Verlauf
wirklich braucht. Bei Knappheit weicht **zuerst der Notizkontext** – der wird
jede Runde neu geholt, der Verlauf nicht.
*Erzwungen in:* `agent._nachrichten_bauen()`.
*Geprüft durch:* `tests/test_gedaechtnis.py`.

### R9 – Gedächtnisverlust wird gemeldet, nicht verschwiegen
Fällt eine Runde aus dem Fenster, steht das in der Antwort. Ist das
Episodengedächtnis unlesbar oder nicht schreibbar, steht das ebenfalls in der
Antwort – und im Prompt die Anweisung, keine Erinnerung zu behaupten.

*Warum:* Sonst ist Vergesslichkeit von einem Bug nicht zu unterscheiden – genau
das hat dich zwei Sitzungen gekostet.
*Erzwungen in:* `_auf_budget_kuerzen()` (Kontext knapp),
`kontextwahl._gedaechtnisluecke_melden()` (Gedächtnis gestört).
*Geprüft durch:* `tests/test_gedaechtnis.py`, `tests/test_kognition.py` (I).

### R10 – Kurzzeit- und Langzeitgedächtnis sind verschiedene Dinge
Der **Gesprächsverlauf** beantwortet „was haben wir eben besprochen".
Die **Vault** beantwortet „was weiß ich über X".
Zeus darf die beiden nicht verwechseln – in keine Richtung.

*Erzwungen in:* Abschnitt `GESPRÄCHSVERLAUF` im Systemprompt (Ebene 3!) **plus**
Übergabe des Verlaufs an den Planer (Ebene 1).
*Seit 12.10.2026 dazwischen:* das **Episodengedächtnis** (`episoden.py`) – was
vor dem Verlauf gesagt wurde, wörtlich und mit Herkunft. Es beantwortet „was
hatten wir am Anfang festgelegt", nicht „was weiß ich über X". Eine
Modellantwort wird dort nie zur Tatsache; als Vorgabe oder Aussage zählt nur,
was der Nutzer gesagt hat.
*Geprüft durch:* `tests/test_kognition.py` (A, C, H).

---

## 4. Regeln über Autonomie

### R11 – Autonomie skaliert mit Umkehrbarkeit
Je schwerer eine Handlung rückgängig zu machen ist, desto mehr Beleg braucht sie.

| Handlung | Autonomie |
|---|---|
| Lesen, Suchen, Berichten | frei |
| Anlegen, Ergänzen, Verlinken | frei, protokolliert |
| Verschieben, Umbenennen, Abschnitt ersetzen | frei, Backup + Journal |
| Überschreiben, Löschen, Zusammenführen | nur mit exaktem Namen |
| Eigenen Code ändern, Vault-Grenze verlassen | **nie** |

### R12 – Bei Mehrdeutigkeit: anhalten, nicht raten
Trifft ein Muster mehrere Kandidaten, ist das ein Fehlschlag mit Kandidatenliste
– kein Zufallsgriff.

*Erzwungen in:* `listenpunkt_erledigt()` (Abbruch bei >1 Treffer),
`_namen_pruefen()`, `notizen_zusammenfuehren` (mindestens zwei echte Quellen).

### R13 – Ein Fehler beendet nie die Sitzung
Schutzschicht um jeden Befehl und jede Antwort; Fehler werden Meldung + offener
Punkt, die Sitzung läuft weiter.

---

## 5. So schreibst du eine neue Grundregel

Nimm diese Vorlage. Die Felder sind nicht Bürokratie – jedes einzelne verhindert
eine Sorte von Fehlregel.

```markdown
### R<n> – <Regel in einem Satz, im Imperativ>
<Zwei bis drei Sätze: was genau gilt, und wo die Grenze verläuft.>

*Warum:* <Der konkrete Vorfall. Datum, Datei, Auswirkung. Keine Abstraktion.>
*Erzwungen in:* <Funktion/Datei – oder ausdrücklich "nicht erzwungen (Ebene 3)">
*Geprüft durch:* <Testdatei – oder "kein Test" als offene Schuld>
*Lockern wenn:* <Welche Beobachtung diese Regel überflüssig machen würde>
```

### Worauf du beim Formulieren achten musst

**1. Jede Regel braucht einen Vorfall.** Eine Regel ohne konkreten Schaden
dahinter ist geraten. Sie kostet Kontext, bindet niemanden und wird beim ersten
Widerspruch gebrochen. Deine besten Regeln (R1, R5) stammen alle aus echten
Datenverlusten.

**2. Nenne die Gegenrichtung.** Gute Regeln sagen, was sie **nicht** meinen. R5
ist nur brauchbar, weil dabeisteht, dass Ergänzen tolerant bleibt. Ohne das wird
aus „exakt" ein Dogma, das den Alltag lähmt.

**3. Schreib die Lockerungsbedingung mit.** *„Lockern wenn: das Modell in 50
Läufen keinen erfundenen Namen mehr produziert."* Ohne dieses Feld sammelt sich
Strenge an, die niemand mehr zu entfernen wagt, weil keiner weiß, warum sie da
ist.

**4. Halte die Zahl klein.** Jede Regel im Systemprompt kostet Kontext, der dem
Verlauf fehlt (R8). Gemessen: Dein Systemprompt belegte 90 % des Budgets. **Zehn
bis fünfzehn Regeln sind das Maximum** – der Rest gehört als Prüfung in den Code,
wo er nichts kostet.

**5. Prüfe auf Widersprüche.** „Antworte auf Basis der Notizen" (alt) gegen
„Nutze den Gesprächsverlauf" (neu) – ohne Rangfolge befolgt das Modell mal die
eine, mal die andere. Steht heute explizit drin: *erst Verlauf, dann Vault.*

**6. Formuliere positiv, wo es geht.** „Nimm den Namen buchstabengetreu aus der
Vault" wirkt besser als „Erfinde keine Namen". Verbote brauchen ein genanntes
Alternativverhalten, sonst entsteht die Lücke, in der improvisiert wird – siehe
`notizen_verknuepfen(nach="")`.

**7. Regeln für das Modell und Regeln für das System trennen.** Der Systemprompt
soll nur enthalten, was das Modell **entscheiden** kann. Alles, was der Code
ohnehin erzwingt (Pfadgrenzen, Backups, Limits), gehört dort nicht hinein – es
kostet nur Platz und suggeriert Verhandelbarkeit.

### Die drei häufigsten Fehlregeln

| Fehlregel | Warum sie schadet |
|---|---|
| „Sei sorgfältig" / „Arbeite präzise" | Nicht prüfbar, nicht befolgbar, verbraucht Kontext |
| Eine Regel, die der Code schon erzwingt | Suggeriert, sie sei verhandelbar |
| Eine Regel ohne Test | Bricht bei der nächsten Änderung unbemerkt – wie die 65-%-Regel |

---

## 6. Wie du sie in Betrieb nimmst

1. Diese Datei ist die Quelle. Sie liegt im Repo, **nicht** nur in der Vault –
   sonst kann Zeus sie versehentlich umschreiben (R6-Geist).
2. Was auf Ebene 3 gehört, wandert in `agent.system_prompt()`. Kurz halten.
3. Was auf Ebene 1 gehört, bekommt eine Prüfung **und einen Test**. Ohne Test
   gilt die Regel als nicht erzwungen.
4. Bei jedem Vorfall: erst prüfen, ob eine bestehende Regel gebrochen wurde
   (dann fehlt die Erzwingung), oder ob eine neue nötig ist (dann diese Vorlage).
5. Einmal im Quartal gegenlesen: Welche Regel hat seit ihrer Einführung nie
   gegriffen? Die ist entweder überflüssig oder nicht erzwungen. Beides
   herausfinden.
