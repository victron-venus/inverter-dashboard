# Inverter Dashboard · Python

[English](README.md) · [Русский](README.ru.md) · [Українська](README.uk.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Nederlands](README.nl.md)

Een zelf te hosten webdashboard voor Victron-installaties. De Python/FastAPI-server leest native Cerbo GX-gegevens via MQTT of via HTTPS van [inverter-gateway](https://github.com/victron-venus/inverter-gateway) (IGW) en levert de gedeelde Vue-interface. De optionele [inverter-control](https://github.com/victron-venus/inverter-control) voegt regelbeleid, historie, prognoses en tarieven toe. Native meetwaarden werken ook zonder deze controller. Home Assistant voegt alleen zelf ingestelde apparaten toe.

Dit project is bedoeld voor een Python-server, container of NAS. Er zijn ook een [Go-implementatie](https://github.com/victron-venus/inverter-dashboard-go) en een [native toepassing](https://github.com/victron-venus/inverter-desktop); de pakketten zijn niet onderling uitwisselbaar.

![Architectuur met afzonderlijke paden voor browser, Python, native telemetrie, controller, Home Assistant en Web Push](docs/assets/architecture.svg)

## Functies

- Net en verbruik per fase, DC-/AC-zonne-energie, batterijapparaten, EV-laden, native verbruikers, waterniveau, pomp en klep. Ontbrekende meetwaarden blijven onderscheiden van gemeten nul.
- Victron-waarschuwingen met hun oorspronkelijke tijd, ook inactieve maar nog niet bevestigde waarschuwingen.
- Zes ESS-keuzes als de controller deze ondersteunt: Off, On, Optimized with battery life, Optimized without battery life, Keep batteries charged en External control.
- Opdrachten met controles op mogelijkheden, verbinding en actualiteit: controllerknoppen, Water Mode, blijvende Setpoint Override en tarieven met revisiecontrole. Acceptatie door het transport is nog geen bevestigde fysieke uitvoering.
- Instelbare Home-knoppen, zonwering, mediaspelers, scènes, sensoren en getalsvelden. Er is geen ingebouwde persoonlijke lijst met HA-entiteiten. Kaarten voor huishoudelijke apparaten vereisen passende zichtbaarheid en beschikbare/actieve gegevens.
- Historie, prognoses en dagenergie met informatie over de dekking. Een onvolledige dag wordt niet als volledig weergegeven; tariefeditors staan buiten de compacte samenvattingsregel.
- Optionele Web Push met blijvende abonnementen, deduplicatie en stille initialisatie na opnieuw verbinden.

Vertaalde README-bestanden betekenen niet dat de nieuwe interface met zes talen al is geïnstalleerd. Het opgenomen Vue-pakket staat in [source-info.json](src/inverter_dashboard/static/source-info.json). [Taalondersteuning](docs/i18n.md) beschrijft het verschil tussen documentatie, Vue-broncode en opgenomen officiële uitgave.

## Lokaal starten

Vereist: Python 3.12 of nieuwer en `uv`. De vastgelegde Vue-bestanden staan al in de repository; normaal starten vereist geen nieuwe frontendbuild.

```sh
git clone https://github.com/victron-venus/inverter-dashboard.git
cd inverter-dashboard
uv sync --locked
# Eerste start alleen op loopback, zonder verbinding met apparatuur.
GATEWAY_ENABLED=false uv run --locked inverter-dashboard --mqtt-host ""
```

Open `http://127.0.0.1:8080`. Zonder gegevensbron zijn er geen live meetwaarden. Stel vóór externe toegang een willekeurig `DASHBOARD_SECRET` en HTTPS in. Een leeg geheim laat beheer-API's onbeschermd; de standaard loopbackbinding vervangt geen authenticatie.

Voor MQTT stelt u `MQTT_HOST`, `MQTT_PORT` en `CERBO_PORTAL_ID` in, zo nodig met aanmeldgegevens en gecontroleerde TLS. Voor IGW gebruikt u `MQTT_HOST=""`, `GATEWAY_ENABLED=true`, `GATEWAY_URL=https://gateway.example.com` en `GATEWAY_API_TOKEN`. Beide Cloudflare Access-waarden worden samen ingesteld, uitsluitend als die toegang ze vereist. `.example.com` is een fictieve voorbeeldnaam.

Als beide transporten zijn ingesteld, heeft bereikbaar MQTT voorrang en dient IGW als terugval. Laat de MQTT-host leeg voor uitsluitend IGW. Een expliciete portal-ID maakt starten bij een stille broker betrouwbaar. IGW vereist een HTTPS-origin zonder padprefix of omleiding. Opgeslagen verbindingen worden bij het starten toegepast; expliciete `--mqtt-host`/`--mqtt-port` hebben voorrang. Zie de [configuratiegids](docs/configuration.md).

## Containers en kant-en-klare programma's

Kies bewust een kanaal van de [officiële uitgaven](https://github.com/victron-venus/inverter-dashboard/releases). ZIP-pakketten voor Linux x86_64, macOS arm64/x86_64 en Windows x86_64 bevatten Python en de interface. Controleer vóór uitpakken SHA-256 met het bijbehorende `.sha256`-bestand en gebruik daarna `inverter-dashboard --help` of `inverter-dashboard.exe --help`. Beta en RC zijn voorversies.

Gebruik voor containers een gecontroleerde image-digest, publiceer de poort expliciet en zet indien nodig `HOST=0.0.0.0` binnen de container. [Compose](docker-compose.yml), [Portainer](portainer-stack.yml) en [k3s](deploy/k3s/README.md) zijn voorbeelden die u moet aanpassen. Het proces gebruikt UID/GID 1000 en heeft leesbare configuratie plus aparte beschrijfbare privéopslag nodig.

Met `INVERTER_DASHBOARD_SETTINGS_FILE=/var/lib/inverter-dashboard/settings/dashboard_settings.json` bewaart u voorkeuren op een blijvend volume. Een alleen-lezen `/app/config` kan daarvoor niet dienen. Web Push vereist daarnaast `WEB_PUSH_DATA_DIR=/var/lib/inverter-dashboard/notifications` op lokale blijvende opslag en één proces/replica. Bewaar VAPID-identiteit, abonnementen en deduplicatie bij updates. Privébestanden horen nooit in de statische webmap.

## Home Assistant en huishoudelijke apparaten

Voer `bash scripts/init-config.sh` uit en bewerk de door Git genegeerde `local_config.py`. In een container bepaalt `INVERTER_DASHBOARD_CONFIG` de map. Vul uw eigen `HA_URL`/`HA_TOKEN` in en activeer `HA_DIRECT_CONTROLS=True` voor directe HA-integratie. Publiceer het token niet.

`HA_SWITCH_ENTITIES` is een dictionary met vaste namen van Home-knoppen als sleutels. Elke waarde kan een entiteits-ID als tekst, een paar `(entity, label)` of een object met `entity`, `label`, `order`, `enabled` zijn. Een lege dictionary (`{}`) betekent geen knoppen. Uitgeschakelde items worden niet getoond, uitgelezen of als opdrachtdoel toegestaan. HA-spiegels vervangen geen native Water/EV/vermogensgegevens of controllerregels. Andere kaarten kiest u met `HA_FILTERED_ENTITIES`.

Stel eigen `HA_APPLIANCE_ENTITIES` in voor dishwasher/washer/dryer en hun zichtbaarheid. Een bestaande timer bewijst geen actieve cyclus; een gedeelde stopcontactmeter meet niet één afzonderlijk apparaat. De [HA-gids](docs/configuration.md#home-assistant) legt structuren en beperkingen uit.

## Meldingen en veiligheid

Een banner toont de gebeurtenistijd, niet het moment van terugkeer op het thuisnetwerk. Een onbekende tijd blijft onbekend. Lokaal verbergen en bevestigen op het apparaat zijn verschillende handelingen. Web Push vereist `WEB_PUSH_ENABLED=true`, blijvende opslag en bewuste browsertoestemming. De testknop bevestigt alleen plaatsing in de wachtrij, niet weergave door het besturingssysteem.

De serverzender wordt ondersteund op Linux en macOS Apple Silicon. Op Windows en Intel macOS blijft het dashboard bruikbaar, maar meldt de zender `unsupported_platform`. Browsers op die systemen kunnen push ontvangen van een Linux-server. De [meldingengids](docs/notifications.md) behandelt termijnen, stille initialisatie en platformgrenzen.

Opdrachten vereisen de actuele bron en recente gezaghebbende gegevens. Een bronwissel trekt de bevoegdheid van oude callbacks en wachtende opdrachten in. Omzeil geen uitgeschakelde knoppen en herhaal een onzekere fysieke opdracht niet blind. Setpoint Override is bewust onafhankelijk van DRY en blijft door de controller uitgevoerd totdat u expliciet stopt. Tariefrevisies voorkomen onbedoeld overschrijven van gelijktijdige wijzigingen.

TLS controleert certificaat en hostnaam. Stel bij een privé-CA vertrouwen in in plaats van verificatie uit te schakelen. URL-tokens kunnen in browserhistorie en proxylogs belanden; gebruik voor API-clients bij voorkeur Authorization. Zie [TLS-beleid](docs/tls-policy.md) en [Beveiliging](SECURITY.md).

## Problemen oplossen en ontwikkelen

`/health/live` controleert het proces, niet recente apparaatmetingen. `/api/state` geeft zonder authenticatie verbindingsinformatie; live velden vereisen authenticatie als een geheim is ingesteld. Controleer bron, portal, brokerrechten en apparaatmogelijkheden. Uitgeschakelde bediening kan duiden op oude gegevens of ontbrekende directe HA-verbinding. Controleer bij opslagfouten pad, rechten en vrije ruimte; tijdelijke volumes overleven geen vervanging van een pod.

Vergelijk bij een oude interface eerst de werkelijk geserveerde build met de goedgekeurde uitgave. Verwijder sleutels of abonnementen niet op goed geluk. Meer informatie: [Wiki](docs/wiki/Home.md), [probleemoplossing](docs/wiki/Troubleshooting.md), [architectuur](docs/architecture.md), [ontwikkeling](docs/development.md), [bijdragen](CONTRIBUTING.md). `bash scripts/ci.sh` voert de gezaghebbende lokale controles uit; gebruik geïsoleerde testgegevens en geen echte actuatoren.

De [uitgavestrategie](RELEASING.md) en [beheerdersgids](docs/release-workflow.md) beschrijven Beta, RC en Stable. Een samengevoegd PR betekent niet automatisch een Stable-uitgave. Licentie: [MIT](LICENSE). Meld kwetsbaarheden privé volgens [SECURITY.md](SECURITY.md).
