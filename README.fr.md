# Inverter Dashboard · Python

[English](README.md) · [Русский](README.ru.md) · [Українська](README.uk.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Nederlands](README.nl.md)

Un tableau de bord web à héberger soi-même pour les installations Victron. Le serveur Python/FastAPI reçoit les données natives du Cerbo GX par MQTT ou via HTTPS depuis [inverter-gateway](https://github.com/victron-venus/inverter-gateway) (IGW), puis sert l'interface Vue commune. [inverter-control](https://github.com/victron-venus/inverter-control), facultatif, apporte les règles de commande, l'historique, les prévisions et les tarifs. La télémétrie native fonctionne sans ce contrôleur. Home Assistant ajoute uniquement les appareils que vous configurez.

Ce dépôt convient à un serveur Python, un conteneur ou un NAS. Il existe aussi une [version Go](https://github.com/victron-venus/inverter-dashboard-go) et une [application native](https://github.com/victron-venus/inverter-desktop) ; leurs paquets ne sont pas interchangeables.

[![Inverter Dashboard v1.5.0 en perspective](images/dashboard-hero.png)](images/Screenshot.png)

*Illustration de l’interface de la version 1.5.0. [Voir la capture originale](images/Screenshot.png).*

![Architecture séparant navigateur, Python, télémétrie native, contrôleur, Home Assistant et Web Push](docs/assets/architecture.svg)

[Processus et résultats](docs/process-and-results.md) : schémas en couleur du déroulement et de l’heure des événements, corrections concrètes et limites de validation.

## Fonctionnalités

- Réseau et consommation par phase, solaire DC/AC, batteries, recharge de véhicule, charges natives, niveau d'eau, pompe et vanne. Une mesure absente reste distincte d'un zéro mesuré.
- Alertes Victron avec leur date d'origine, y compris les alertes inactives mais non acquittées.
- Six modes ESS lorsque le contrôleur les prend en charge : Off, On, Optimized with battery life, Optimized without battery life, Keep batteries charged et External control.
- Commandes contrôlant capacités, connexion et fraîcheur : options du contrôleur, Water Mode, Setpoint Override persistant et tarifs avec vérification de révision. Une commande acceptée par le transport n'est pas encore une action physique confirmée.
- Boutons Home, volets, lecteurs multimédias, scènes, capteurs et valeurs numériques configurables. Aucune liste personnelle d'entités HA n'est intégrée. Les cartes d'électroménager dépendent de la visibilité et de données disponibles/actives.
- Historique, prévisions et énergie quotidienne avec indication de couverture. Une journée incomplète n'est pas présentée comme complète ; les éditeurs de tarif restent hors de la ligne de synthèse compacte.
- Web Push facultatif, abonnements persistants, déduplication et initialisation silencieuse après reconnexion.

L’interface intégrée provient de l’artefact officiel **Vue v2.2.9** (version du paquet `2.2.9`, source `f86943ebc764688c32bc4389f2276bc12682a90e`). La [preuve de provenance](src/inverter_dashboard/static/source-info.json) contient les métadonnées et empreintes des fichiers. Les fichiers sont identiques octet pour octet à la RC validée `v2.2.9-rc.2` (exécution de compilation `39039690585`). Les textes de l’interface, les dates, les heures, les erreurs conservées de l’application et les libellés des thèmes sont disponibles en anglais, russe, ukrainien, allemand, français et néerlandais ; les messages provenant de sources externes conservent leur texte d’origine. Voir le [guide des langues](docs/i18n.md).

## Démarrage local

Prérequis : Python 3.12 ou ultérieur et `uv`. Les fichiers Vue enregistrés sont déjà présents ; le lancement normal du serveur ne nécessite pas de reconstruire l'interface.

```sh
git clone https://github.com/victron-venus/inverter-dashboard.git
cd inverter-dashboard
uv sync --locked
# Premier lancement sur loopback uniquement, sans connexion aux appareils.
GATEWAY_ENABLED=false uv run --locked inverter-dashboard --mqtt-host ""
```

Ouvrez `http://127.0.0.1:8080`. Sans source, aucune mesure en direct n'apparaît. Avant un accès distant, configurez un `DASHBOARD_SECRET` aléatoire et HTTPS. Un secret vide laisse les API de gestion sans protection ; l'écoute locale par défaut ne remplace pas l'authentification.

Pour MQTT, renseignez `MQTT_HOST`, `MQTT_PORT`, `CERBO_PORTAL_ID`, puis les identifiants et TLS vérifié si nécessaire. Pour IGW, utilisez `MQTT_HOST=""`, `GATEWAY_ENABLED=true`, `GATEWAY_URL=https://gateway.example.com` et `GATEWAY_API_TOKEN`. Les deux paramètres Cloudflare Access se fournissent ensemble uniquement si ce chemin les exige. `.example.com` est un exemple fictif.

Avec deux transports configurés, MQTT accessible est prioritaire et IGW sert de secours. Laissez l'hôte MQTT vide pour rester exclusivement sur IGW. Un portail explicite fiabilise le démarrage sur un broker silencieux. IGW exige une origine HTTPS sans préfixe de chemin ni redirection. Les paramètres enregistrés sont appliqués au démarrage ; les options explicites `--mqtt-host`/`--mqtt-port` sont prioritaires. Voir la [configuration](docs/configuration.md).

## Conteneurs et programmes prêts à l'emploi

Choisissez explicitement le canal d'une [publication officielle](https://github.com/victron-venus/inverter-dashboard/releases). Les ZIP Linux x86_64, macOS arm64/x86_64 et Windows x86_64 contiennent Python et l'interface. Vérifiez SHA-256 avec le fichier `.sha256` correspondant avant extraction, puis consultez `inverter-dashboard --help` ou `inverter-dashboard.exe --help`. Beta et RC sont des préversions.

Pour un conteneur, utilisez un digest d'image approuvé, publiez le port explicitement et définissez si nécessaire `HOST=0.0.0.0` à l'intérieur. Les exemples [Compose](docker-compose.yml), [Portainer](portainer-stack.yml) et [k3s](deploy/k3s/README.md) doivent être adaptés à votre installation. Le processus utilise UID/GID 1000 et doit pouvoir lire la configuration et écrire dans un stockage privé distinct.

Définissez `INVERTER_DASHBOARD_SETTINGS_FILE=/var/lib/inverter-dashboard/settings/dashboard_settings.json` sur un volume persistant. Un `/app/config` en lecture seule ne permet pas d'enregistrer les préférences. Web Push nécessite aussi `WEB_PUSH_DATA_DIR=/var/lib/inverter-dashboard/notifications` sur un stockage local persistant, avec un seul processus/réplica. Conservez identité VAPID, abonnements et déduplication lors des mises à jour ; ne placez jamais ces fichiers parmi les ressources web statiques.

## Home Assistant et électroménager

Exécutez `bash scripts/init-config.sh`, puis modifiez `local_config.py`, ignoré par Git. Dans un conteneur, `INVERTER_DASHBOARD_CONFIG` choisit son répertoire. Renseignez vos `HA_URL`/`HA_TOKEN` et activez `HA_DIRECT_CONTROLS=True` pour l'intégration directe. Ne publiez pas le jeton.

`HA_SWITCH_ENTITIES` est un dictionnaire dont les clés sont des noms stables de boutons Home. Chaque valeur peut être un identifiant d'entité sous forme de chaîne, une paire `(entity, label)` ou un objet avec `entity`, `label`, `order`, `enabled`. Un dictionnaire vide (`{}`) ne crée aucun bouton. Les entrées désactivées ne sont ni affichées, ni interrogées, ni autorisées comme cibles. Les miroirs HA ne remplacent pas les données natives Water/EV/puissance ou les règles du contrôleur. `HA_FILTERED_ENTITIES` choisit les autres cartes.

Configurez vos `HA_APPLIANCE_ENTITIES` pour dishwasher/washer/dryer et leurs préférences de visibilité. Un minuteur existant ne prouve pas un cycle actif ; un compteur de prises commun ne mesure pas un appareil individuel. Le [guide HA](docs/configuration.md#home-assistant) détaille les structures et restrictions.

## Notifications et sécurité

Un bandeau affiche l'heure de l'événement, pas celle du retour sur le réseau domestique. Une heure inconnue reste inconnue. Masquer localement et acquitter sur l'appareil sont deux opérations différentes. Web Push exige `WEB_PUSH_ENABLED=true`, un stockage persistant et une autorisation explicite du navigateur. Le bouton de test confirme la mise en file, pas l'affichage par le système.

L'émetteur serveur fonctionne sous Linux et macOS Apple Silicon. Sous Windows et Intel macOS, le tableau de bord reste utilisable, mais l'émetteur indique `unsupported_platform`. Les navigateurs de ces systèmes peuvent recevoir les messages d'un serveur Linux. Consultez les [notifications](docs/notifications.md) pour les délais, l'initialisation silencieuse et les limites des plateformes.

Les commandes exigent une source actuelle et des observations fraîches. Un changement de connexion retire l'autorité aux anciens callbacks et travaux en attente. Ne contournez pas les boutons désactivés et ne répétez pas aveuglément une commande physique incertaine. Setpoint Override est volontairement indépendant de DRY et reste géré par le contrôleur jusqu'à un arrêt explicite. Les révisions de tarif empêchent l'écrasement silencieux d'un changement concurrent.

TLS vérifie certificats et noms d'hôte. Pour une autorité privée, configurez sa confiance plutôt que désactiver la vérification. Les jetons en URL peuvent apparaître dans l'historique et les journaux du proxy ; préférez Authorization pour les API. Voir [TLS](docs/tls-policy.md) et [Sécurité](SECURITY.md).

## Dépannage et développement

`/health/live` teste le processus, pas la fraîcheur des appareils. `/api/state` sans authentification retourne la connectivité ; avec un secret configuré, les champs en direct nécessitent l'authentification. Vérifiez source, portail, droits MQTT et capacités. Une commande désactivée peut signaler des données périmées ou une connexion HA directe absente. Pour un échec d'enregistrement, vérifiez chemin, permissions et espace libre ; un volume éphémère ne survit pas au remplacement du pod.

Si l'interface semble ancienne, comparez d'abord le build réellement servi à la publication approuvée. Ne supprimez pas les clés et abonnements au hasard. Ressources : [Wiki](docs/wiki/Home.md), [dépannage](docs/wiki/Troubleshooting.md), [architecture](docs/architecture.md), [développement](docs/development.md), [contributions](CONTRIBUTING.md). `bash scripts/ci.sh` lance les contrôles locaux de référence ; utilisez des données isolées, jamais des actionneurs réels pour ces tests.

La [stratégie de publication](RELEASING.md) et le [guide opérateur](docs/release-workflow.md) définissent Beta, RC et Stable. Fusionner un PR ne publie pas automatiquement une version Stable. Licence [MIT](LICENSE) ; signalez les vulnérabilités en privé selon [SECURITY.md](SECURITY.md).
