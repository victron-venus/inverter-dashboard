# Inverter Dashboard · Python

[English](README.md) · [Русский](README.ru.md) · [Українська](README.uk.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Nederlands](README.nl.md)

Eine selbst betriebene Weboberfläche für Victron-Systeme. Der Python/FastAPI-Server liest native Cerbo-GX-Daten über MQTT oder über HTTPS von [inverter-gateway](https://github.com/victron-venus/inverter-gateway) (IGW) und liefert die gemeinsame Vue-Oberfläche aus. Der optionale [inverter-control](https://github.com/victron-venus/inverter-control) ergänzt Steuerungsregeln, Verlauf, Prognosen und Tarife. Native Messwerte benötigen diesen Controller nicht. Home Assistant ergänzt ausschließlich selbst konfigurierte Geräte.

Dieses Projekt eignet sich für Python-Server, Container und NAS. Daneben gibt es die [Go-Implementierung](https://github.com/victron-venus/inverter-dashboard-go) und die [native Anwendung](https://github.com/victron-venus/inverter-desktop); deren Pakete sind nicht austauschbar.

![Architektur mit getrennten Pfaden für Browser, Python, native Telemetrie, Controller, Home Assistant und Web Push](docs/assets/architecture.svg)

[Ablauf und Ergebnisse](docs/process-and-results.md): farbige Diagramme zum Arbeitsablauf und zur Ereigniszeit, konkrete Korrekturen und Grenzen der Prüfung.

## Funktionen

- Netz und Verbrauch je Phase, DC-/AC-Solarleistung, Batteriegeräte, Fahrzeugladung, native Verbraucher, Wasserstand sowie Pumpen- und Ventilzustand. Nicht verfügbare Daten bleiben von gemessenen Nullwerten unterscheidbar.
- Victron-Warnungen mit ursprünglicher Ereigniszeit, einschließlich inaktiver, noch nicht bestätigter Warnungen.
- Sechs ESS-Auswahlen bei Unterstützung durch den Controller: Off, On, Optimized with battery life, Optimized without battery life, Keep batteries charged und External control.
- Befehle mit Prüfung von Fähigkeiten, Verbindung und Aktualität: Controller-Schalter, Water Mode, dauerhafter Setpoint Override und Tarifbearbeitung mit Revisionsprüfung. Transportannahme bedeutet noch keine physische Ausführung.
- Frei konfigurierte Home-Schaltflächen, Rollläden, Medien, Szenen, Sensoren und Zahlenwerte. Es gibt keine fest eingebaute persönliche HA-Geräteliste. Haushaltsgerätekarten benötigen passende Sichtbarkeit und verfügbare/aktive Daten.
- Verlauf, Prognosen und Tagesenergie mit Angaben zur Datenabdeckung. Ein unvollständiger Tag wird nicht als vollständig ausgegeben. Tarifeditoren stehen außerhalb der kompakten Zusammenfassungszeile.
- Optionale Web-Push-Benachrichtigungen mit dauerhaften Abonnements, Deduplizierung und stiller Initialisierung nach Wiederverbindung.

Eingebettet ist das offizielle Artefakt **Vue v2.2.9** (Paketversion `2.2.9`, Quellstand `f86943ebc764688c32bc4389f2276bc12682a90e`). Der [Herkunftsnachweis](src/inverter_dashboard/static/source-info.json) enthält Metadaten und Datei-Hashes. Die Dateien sind bytegleich mit der akzeptierten RC `v2.2.9-rc.2` (Build-Lauf `39039690585`). Oberflächentexte, Datum, Uhrzeit, gespeicherte Anwendungsfehler und Designbezeichnungen unterstützen Englisch, Russisch, Ukrainisch, Deutsch, Französisch und Niederländisch; Meldungen externer Quellen behalten ihren Originaltext. Siehe [Sprachunterstützung](docs/i18n.md).

## Lokal starten

Voraussetzungen: Python 3.12 oder neuer und `uv`. Die aufgezeichneten Vue-Dateien liegen bereits im Repository; ein normaler Backend-Start benötigt keinen Frontend-Build.

```sh
git clone https://github.com/victron-venus/inverter-dashboard.git
cd inverter-dashboard
uv sync --locked
# Erster Start nur auf Loopback, ohne Verbindung zu Geräten.
GATEWAY_ENABLED=false uv run --locked inverter-dashboard --mqtt-host ""
```

Öffnen Sie `http://127.0.0.1:8080`. Ohne Datenquelle erscheinen keine Live-Messwerte. Vor Fernzugriff müssen ein zufälliges `DASHBOARD_SECRET` und HTTPS eingerichtet werden. Ein leeres Secret lässt Verwaltungsendpunkte ungeschützt; die Loopback-Voreinstellung ersetzt keine Authentifizierung.

Für MQTT setzen Sie `MQTT_HOST`, `MQTT_PORT` und `CERBO_PORTAL_ID`, gegebenenfalls Zugangsdaten und geprüftes TLS. Für IGW setzen Sie `MQTT_HOST=""`, `GATEWAY_ENABLED=true`, `GATEWAY_URL=https://gateway.example.com` und `GATEWAY_API_TOKEN`. Die beiden Cloudflare-Access-Werte werden nur für einen entsprechend geschützten Zugang gemeinsam benötigt. `.example.com` ist ein Platzhalter.

Bei zwei konfigurierten Transporten wird erreichbares MQTT bevorzugt, IGW dient als Ausweichweg. Für ausschließlich IGW bleibt der MQTT-Host leer. Eine explizite Portal-ID ermöglicht zuverlässigen Start an einem stillen Broker. IGW benötigt einen HTTPS-Origin ohne Pfadpräfix oder Weiterleitung. Gespeicherte Verbindungswerte gelten beim Neustart; explizite `--mqtt-host`/`--mqtt-port` haben Vorrang. Einzelheiten stehen in der [Konfigurationsreferenz](docs/configuration.md).

## Container und fertige Programme

Wählen Sie bewusst einen Kanal der [offiziellen Releases](https://github.com/victron-venus/inverter-dashboard/releases). ZIP-Pakete für Linux x86_64, macOS arm64/x86_64 und Windows x86_64 enthalten Python und die Oberfläche. Prüfen Sie vor dem Entpacken SHA-256 anhand der passenden `.sha256`-Datei und starten Sie anschließend `inverter-dashboard --help` beziehungsweise `inverter-dashboard.exe --help`. Beta und RC sind Vorabversionen.

Container sollten einen geprüften Image-Digest verwenden. Veröffentlichen Sie den Port ausdrücklich und setzen Sie bei Bedarf `HOST=0.0.0.0` im Container. [Compose](docker-compose.yml), [Portainer](portainer-stack.yml) und [k3s](deploy/k3s/README.md) sind anzupassende Vorlagen. Der Prozess verwendet UID/GID 1000 und benötigt lesbare Konfiguration sowie ein separates beschreibbares privates Datenverzeichnis.

Mit `INVERTER_DASHBOARD_SETTINGS_FILE=/var/lib/inverter-dashboard/settings/dashboard_settings.json` speichern Sie Einstellungen auf einem dauerhaften Volume. Ein schreibgeschütztes `/app/config` eignet sich dafür nicht. Web Push benötigt zusätzlich `WEB_PUSH_DATA_DIR=/var/lib/inverter-dashboard/notifications` auf lokalem persistentem Speicher und genau einen Prozess/eine Replik. VAPID-Schlüssel, Abonnements und Deduplizierung müssen Updates überstehen. Private Dateien gehören niemals in das statische Webverzeichnis.

## Home Assistant und Haushaltsgeräte

Erzeugen Sie mit `bash scripts/init-config.sh` die von Git ignorierte `local_config.py`. Im Container bestimmt `INVERTER_DASHBOARD_CONFIG` deren Verzeichnis. Tragen Sie eigene `HA_URL`/`HA_TOKEN` ein und aktivieren Sie für direkte HA-Integration `HA_DIRECT_CONTROLS=True`. Tokens dürfen nicht veröffentlicht werden.

`HA_SWITCH_ENTITIES` ist ein Dictionary mit stabilen Namen der Home-Schaltflächen als Schlüsseln. Jeder Wert kann eine Entity-ID als Zeichenfolge, ein Paar `(entity, label)` oder ein Objekt mit `entity`, `label`, `order`, `enabled` sein. Ein leeres Dictionary (`{}`) bedeutet keine Schaltflächen. Deaktivierte Einträge werden weder angezeigt noch abgefragt oder als Befehlsziele zugelassen. HA-Spiegel dürfen native Wasser-/EV-/Leistungsdaten und Controller-Regeln nicht ersetzen. Weitere Karten werden mit `HA_FILTERED_ENTITIES` ausgewählt.

Ordnen Sie dishwasher/washer/dryer über eigene `HA_APPLIANCE_ENTITIES` zu. Ein Timer allein beweist keinen laufenden Waschgang; ein gemeinsamer Steckdosenstromkreis misst nicht ein einzelnes Gerät. Sichtbarkeit, Verfügbarkeit und Aktivität bleiben getrennt. Beispiele enthält der [HA-Abschnitt](docs/configuration.md#home-assistant).

## Benachrichtigungen und Sicherheit

Banner zeigen die Ereigniszeit, nicht den Zeitpunkt der Rückkehr ins Heimnetz. Unbekannte Zeiten bleiben unbekannt. Lokales Ausblenden und Bestätigung am Gerät sind verschiedene Aktionen. Für Web Push benötigen Sie `WEB_PUSH_ENABLED=true`, dauerhaften Speicher und eine bewusste Browserfreigabe. Der Testknopf bestätigt nur die Warteschlange, nicht die Betriebssystemanzeige.

Der serverseitige Sender wird auf Linux und macOS Apple Silicon unterstützt. Auf Windows und Intel macOS bleibt das Dashboard nutzbar, der Sender meldet jedoch `unsupported_platform`. Browser auf diesen Systemen können Push von einem Linux-Server empfangen. Ablauf, Fristen und Plattformgrenzen stehen unter [Benachrichtigungen](docs/notifications.md).

Befehle benötigen eine aktuelle Verbindung und frische autoritative Daten. Ein Quellenwechsel entzieht alten Callbacks und ausstehenden Befehlen die Berechtigung. Umgehen Sie keine gesperrten Schaltflächen und wiederholen Sie unbestätigte physische Befehle nicht blind. Setpoint Override ist absichtlich unabhängig von DRY und läuft im Controller bis zum ausdrücklichen Stoppen. Tarife verwenden Revisionen gegen unbeabsichtigtes Überschreiben.

TLS prüft Zertifikat und Hostnamen. Für private Zertifizierungsstellen konfigurieren Sie Vertrauen, statt Prüfungen abzuschalten. URL-Tokens können in Browserhistorie und Proxy-Logs landen; API-Clients sollten Authorization verwenden. Siehe [TLS-Richtlinie](docs/tls-policy.md) und [Sicherheit](SECURITY.md).

## Fehlerdiagnose und Entwicklung

`/health/live` prüft den Prozess, nicht frische Gerätedaten. `/api/state` liefert ohne Anmeldung Verbindungsdaten; Live-Felder erfordern bei gesetztem Secret eine Anmeldung. Prüfen Sie Quelle, Portal, Brokerrechte und Gerätefähigkeiten. Gesperrte Steuerelemente können auf veraltete Daten oder fehlende direkte HA-Verbindung hinweisen. Bei Speicherfehlern prüfen Sie Pfad, Rechte und freien Platz; temporäre Volumes überstehen keinen Pod-Austausch.

Bei alter Oberfläche vergleichen Sie zuerst den ausgelieferten Build mit dem freigegebenen Release. Löschen Sie Schlüssel und Abonnements nicht auf Verdacht. Weitere Informationen: [Wiki](docs/wiki/Home.md), [Fehlerdiagnose](docs/wiki/Troubleshooting.md), [Architektur](docs/architecture.md), [Entwicklung](docs/development.md), [Beiträge](CONTRIBUTING.md). `bash scripts/ci.sh` führt die maßgeblichen lokalen Prüfungen aus; Tests verwenden isolierte Daten und keine echten Aktoren.

[Release-Strategie](RELEASING.md) und [Betriebsanleitung](docs/release-workflow.md) definieren Beta, RC und Stable. Ein gemergter PR ist keine Stable-Veröffentlichung. Es gilt die [MIT-Lizenz](LICENSE); Sicherheitslücken bitte vertraulich gemäß [SECURITY.md](SECURITY.md) melden.
