# SC Info — Serverdiagnose für Star Citizen

**Eine Taste drücken und in zehn Sekunden wissen, ob der Server taugt.**

SC Info tippt `r_displayinfo 3` selbst ins Spiel, fotografiert die Anzeige oben
rechts, liest die Werte aus und bewertet den Server mit einer Note von 0 bis 10.

➡ **[Download (neueste Version)](../../releases/latest)**

---

## Was es dir sagt

| Wert | Bedeutung |
|---|---|
| **Server-FPS** | Der wichtigste Wert. 30 ist derzeit das Maximum, unter 15 werden Inventar, NPCs und Missionen zäh. |
| **Ping** | Verzögerung zum Server. Unter 50 ms sehr gut, über 140 ms im Kampf spürbar. |
| **Hitches** | Kurze Server-Aussetzer. 0 ist das Ziel. |
| **Paketverlust** | Die Ursache vieler Ruckler. |
| **Shard-Größe** | Wie viele Spieler in deiner Welt sind (Grenze rund 500) und wie viele davon auf deinem Server-Knoten. |

Daraus wird eine Note und eine klare Empfehlung: *bleiben* oder *neu joinen*.
Der Verlauf sammelt jede Messung mit Zeit, Region und Note — so siehst du, ob
der neue Server wirklich besser ist als der alte.

## Bedienung

| Taste | Was passiert |
|---|---|
| **F6** | Konsole auf → Befehl tippen → Enter → Konsole zu → messen → auswerten |
| **F9** | Nur messen. Tippt **nichts** ins Spiel. |
| **F7** | Anzeige wieder aus (`r_displayinfo 0`). |
| **Alt+M** | SC Info nach vorn holen bzw. wieder wegschicken — für Spieler mit nur einem Bildschirm. |

Alternativ das Häkchen **„Immer im Vordergrund"**: SC Info bleibt dann klein am
Rand über dem Spiel sichtbar. Nur nicht über die Anzeige oben rechts legen —
das ist der Bereich, den SC Info fotografieren muss.

Alle Tasten sind oben im Fenster umstellbar — auch auf **„Keine“**, wenn eine Funktion
keine Taste bekommen soll (dann behält das Spiel die Taste). Die Spielkonsole öffnet SC Info mit
`^` (links neben der 1) — das ist in Star Citizen fest.

> Die gewählten Tasten gehören dann SC Info allein — das Spiel bekommt sie
> nicht mehr. F5 ist bewusst nicht voreingestellt, dort liegt in Star Citizen
> das Pin-System.

Ein **Hilfe**-Knopf im Programm zeigt die komplette Bedienung — mit den gerade
eingestellten Tasten.

## Installation

1. ZIP herunterladen und **komplett entpacken**
2. `SC Info.exe` starten
3. Im Spiel einmal `r_displayinfo 3` eingeben
4. Auf *Bereich festlegen* klicken und die Anzeige mit der Maus einrahmen

Kein Python, keine Installation, keine Registry-Einträge.

> **Windows zeigt beim ersten Start eine Warnung.** Das ist bei jedem Programm
> ohne gekaufte Herausgeber-Signatur so: *Weitere Informationen* →
> *Trotzdem ausführen*.

## Tipp: ohne Helm messen

Mit aufgesetztem Helm legt das Spiel HUD-Symbole und Missions-Einblendungen
über genau die Ecke, in der die Anzeige steht — die Schrift wird dadurch
teilweise verdeckt. Ohne Helm ist die Ecke frei und die Werte werden
zuverlässig gelesen.

## Deine Daten bleiben bei dir

Die Texterkennung läuft **komplett auf deinem PC** (RapidOCR). Das Programm hat
überhaupt keine Netzwerkfunktion — es fragt nichts ab, meldet nichts, sendet
nichts. Einstellungen und Verlauf liegen als lesbare Dateien in deinem
SC-Info-Ordner.

## Zum Tasten-Senden

Bei **F6** und **F7** schickt SC Info Tastendrücke ans Spiel, so wie es VoiceAttack oder
AutoHotkey auch tun. `r_displayinfo 3` ist ein reiner Anzeigebefehl: Er zeigt
Werte, die das Spiel ohnehin kennt, und verschafft niemandem einen Vorteil.

Trotzdem bleibt das eine eigene Entscheidung. Wem das nicht geheuer ist,
schaltet die Anzeige einmal von Hand ein und nutzt ausschließlich **F9** —
dabei wird garantiert keine einzige Taste ans Spiel geschickt.

Zwei Sicherheitsnetze, bevor auch nur ein Buchstabe getippt wird: Getippt wird
**nur**, wenn Star Citizen im Vordergrund ist — und **nur**, wenn die Konsole
nachweislich aufgegangen ist. Sonst würden die Buchstaben des Befehls zu
Spielbefehlen (Inventar, springen, Waffe ziehen). Dafür beim Drücken kurz
stillstehen.

## Voraussetzungen

- Windows 10 oder 11
- Star Citizen im **randlosen Fenstermodus** (die übliche Einstellung) — bei
  echtem Vollbild liefert Windows beim Screenshot manchmal ein schwarzes Bild

Getestet gegen 1080p, 1440p, Ultrawide und 4K; die Erkennung passt ihre
Vergrößerung automatisch an die Auflösung an.

## Selbst bauen

```
Start.bat          startet aus dem Quellcode (legt beim ersten Mal ein venv an)
Paket bauen.bat    erzeugt die fertige EXE und das Weitergabe-ZIP
```

Gebraucht wird Python 3.11. Die EXE entsteht mit PyInstaller nach der
Bauanleitung in `sc_info.spec`.

---

Kostenlos, ohne Gewähr, Weitergabe ausdrücklich erwünscht.
Star Citizen ist ein Produkt von Cloud Imperium Games; dieses Werkzeug steht in
keiner Verbindung zu CIG.
