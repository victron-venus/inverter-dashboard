# Inverter Dashboard

[![Docker Hub](https://img.shields.io/docker/v/alvit/inverter-dashboard?label=Docker%20Hub&logo=docker)](https://hub.docker.com/r/alvit/inverter-dashboard)
[![Docker Pulls](https://img.shields.io/docker/pulls/alvit/inverter-dashboard?label=Docker%20Pulls&logo=docker)](https://hub.docker.com/r/alvit/inverter-dashboard)
[![CI](https://github.com/victron-venus/inverter-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/victron-venus/inverter-dashboard/actions/workflows/ci.yml)
[![CodeQL](https://github.com/victron-venus/inverter-dashboard/actions/workflows/codeql.yml/badge.svg)](https://github.com/victron-venus/inverter-dashboard/actions/workflows/codeql.yml)
[![Trivy](https://github.com/victron-venus/inverter-dashboard/actions/workflows/trivy-fs.yml/badge.svg)](https://github.com/victron-venus/inverter-dashboard/actions/workflows/trivy-fs.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub stars](https://img.shields.io/github/stars/victron-venus/inverter-dashboard)](https://github.com/victron-venus/inverter-dashboard/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/victron-venus/inverter-dashboard)](https://github.com/victron-venus/inverter-dashboard/network/members)
[![GitHub last commit](https://img.shields.io/github/last-commit/victron-venus/inverter-dashboard)](https://github.com/victron-venus/inverter-dashboard/commits/main)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/victron-venus/inverter-dashboard/graphs/commit-activity)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

Real-time web dashboard for monitoring Victron inverter systems via MQTT. Designed to work with [inverter-control](https://github.com/victron-venus/inverter-control) on Cerbo GX.

![Inverter Dashboard](images/Screenshot.png)

## Features

- Real-time power monitoring (Grid, Solar, Battery, Consumption)
- Interactive controls via WebSocket
- Live power charts with uPlot
- EV charging status
- Water system monitoring
- Home automation controls
- Mobile-friendly responsive UI

## Quick Start

### Docker (Recommended)

```bash
docker run -d \
  --name inverter-dashboard \
  -p 8080:8080 \
  -e MQTT_HOST=192.168.1.100 \
  alvit/inverter-dashboard:latest
```

### Docker Compose

```yaml
version: '3.8'
services:
  dashboard:
    image: alvit/inverter-dashboard:latest
    ports:
      - "8080:8080"
    environment:
      - MQTT_HOST=192.168.1.100
      - MQTT_PORT=1883
    restart: unless-stopped
```

### Portainer Stack

See [portainer-stack.yml](portainer-stack.yml) for Portainer deployment.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `MQTT_HOST` | `192.168.160.150` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `WEB_PORT` | `8080` | Web server port (inside the container) |
| `INVERTER_DASHBOARD_CONFIG` | `/app/config` | Host folder mounted read-only: `site_config.py` and optional TLS files |

### Secrets (`site_config.py`) + optional HTTPS

Committed template only: [`site_config.example.py`](site_config.example.py). Your real file is **`site_config.py`** in the **repository root** (next to `server.py`) — **gitignored** (never push). There is no separate `config/` folder in the repo.

If Cerbo **inverter-control** uses **`MQTT_SLIM_STATE`** (slim `inverter/state`), dishwasher/washer/dryer fields are omitted from MQTT — add **`HA_APPLIANCE_ENTITIES`** in **`site_config.py`** so the dashboard polls those sensors from Home Assistant (same keys as full MQTT state).

**Synology NAS (deploy path used in this repo):**

| Location | Purpose |
|---------|---------|
| `/volume1/docker/inverter-dashboard/config` | **On the NAS host only:** a folder that is **bind-mounted** read-only into the container as **`/app/config`**. Put **`site_config.py`** here together with optional **`dashboard.crt`** / **`dashboard.key`**. The folder name on disk is convention (matches [`docker-compose.yml`](docker-compose.yml) / [`portainer-stack.yml`](portainer-stack.yml)); it is **not** a `config/` directory inside the Git clone. |

- **After clone (any machine):** `./scripts/init-config.sh` creates **`./site_config.py`** from the example; fill in **`HA_TOKEN`** / **`HA_URL`** (typically the same long-lived token as inverter-control **`secrets.py`**).
- **`postinstall.sh`** (in repo root): run **on your Mac/PC** (not on the NAS). Put **`Host synology`** (user, hostname, keys) in **`~/.ssh/config`**, then simply **`./postinstall.sh`** — it runs **`ssh synology`** by default (override with **`SYNOLOGY_SSH`** only if you use another alias).

  Expects **passwordless `sudo`** on the NAS for **`docker` / `docker compose`** and for writing under **`/volume1/docker/...`**. Files are pushed with **`ssh` + stdin** (not `scp`), so it still works if Synology has disabled the SFTP/SCP subsystem. Then **`sudo install`** from a temp dir. Env: **`SYNOLOGY_SSH`**, **`REMOTE_BASE`**, **`SOURCE_CONFIG`** (defaults to **repo root** next to **`postinstall.sh`**), **`STACK_FILE`**, **`DOCKER`** (default **`sudo /usr/local/bin/docker`** — under **`sudo`** DSM often has no **`docker`** in **`PATH`**). On **macOS**, if **`dashboard.crt`** exists next to **`postinstall.sh`** or under **`.certs/`**, the script imports it as trusted when missing: tries **System** keychain (`System.keychain-db` / `System.keychain`), then **login** keychain if needed (**`SKIP_MAC_TRUST=1`** to skip).

If **both** cert files exist in that folder, the **entrypoint enables HTTPS on the same port** as HTTP would use; otherwise HTTP only.

### Command Line Arguments

```bash
python server.py --mqtt-host 192.168.1.100 --mqtt-port 1883 --port 8080
```

### HTTPS (why you still see `http://`)

By default the app and the published Docker image listen on **plain HTTP** (port `8080`). Nothing is wrong with your deploy — TLS is not enabled unless you add it.

**Choose one approach:**

1. **Reverse proxy (recommended for production / LAN DNS)**  
   Put **Caddy**, **Traefik**, or **nginx** in front of the container on port **443**, terminate Let’s Encrypt (or your certs) there, and proxy to `http://inverter-dashboard:8080`. You open `https://dashboard.example.com` in the browser; the container keeps HTTP internally.

2. **TLS inside the Python app** (good for quick tests / single host)

   **Docker (recommended layout):** mount your host config folder to **`/app/config`**, put **`dashboard.crt`** and **`dashboard.key`** next to **`site_config.py`**. The entrypoint detects both files and passes **`--ssl-cert`** / **`--ssl-key`** automatically on the **same** port as without TLS (default 8080). Map ports e.g. `"8443:8080"` if you want HTTPS on 8443 externally.

   Generate certs (repo includes a helper):

   ```bash
   ./scripts/ssl-local-deploy.sh
   # Optional: TLS_CN=myhost.local ./scripts/ssl-local-deploy.sh
   ```

   The helper includes **Subject Alternative Name (SAN)** entries (`TLS_CN`, `localhost`, `127.0.0.1`). Browsers require SAN for HTTPS hostname checks; an old cert with **CN-only** can still show “not private” even after trusting — regenerate, copy the new **`dashboard.crt`** / **`dashboard.key`** to the NAS folder that is mounted at **`/app/config`**, redeploy, then trust again (remove the previous cert from Keychain Access if needed).

   Trust the cert on your Mac (`postinstall.sh` does this automatically; the helper also prints `security add-trusted-cert`).

   **Local run (paths arbitrary):**

   ```bash
   python server.py --mqtt-host … --port 8443 \
     --ssl-cert .certs/dashboard.crt --ssl-key .certs/dashboard.key
   ```

   **Docker Compose / Portainer:** host config path (Synology):

   ```yaml
   volumes:
     - /volume1/docker/inverter-dashboard/config:/app/config:ro
   ```

   On a dev PC without `/volume1`, comment out this volume or bind a local folder (e.g. repo root or any directory that contains **`site_config.py`** and optional TLS files) to **`/app/config`** instead.

   If you omit **`dashboard.crt`** / **`dashboard.key`** on the host, the app stays on HTTP.

   The image **`HEALTHCHECK`** uses **`scripts/docker_healthcheck.py`**, which calls **`/api/state`** over HTTP or HTTPS depending on whether `dashboard.crt` + `dashboard.key` exist in the config directory.

3. **`mkcert`** — alternative to OpenSSL for local dev trust; still point the app at the generated `.pem` paths with `--ssl-cert` / `--ssl-key`.

## MQTT Topics

### Subscribed (incoming data)
- `inverter/state` - JSON with current system state
- `inverter/console` - Console log messages

### Published (commands)
- `inverter/cmd/toggle` - Toggle boolean entities
- `inverter/cmd/press` - Press button entities
- `inverter/cmd/setpoint` - Set power setpoint
- `inverter/cmd/dry_run` - Toggle dry run mode
- `inverter/cmd/limits` - Set power limits
- `inverter/cmd/ess_mode` - Toggle ESS mode
- `inverter/cmd/loop_interval` - Set control loop interval

## Expected State Format

```json
{
  "gt": 150,
  "g1": 100,
  "g2": 50,
  "tt": 2500,
  "t1": 1500,
  "t2": 1000,
  "solar_total": 3500,
  "battery_soc": 85,
  "battery_power": -500,
  "battery_voltage": 52.4,
  "setpoint": 0,
  "inverter_state": "Inverting",
  "dry_run": false,
  "ess_mode": {
    "mode_name": "Optimized (with BatteryLife)",
    "is_external": false
  },
  "booleans": {
    "auto_mode": true,
    "ev_boost": false
  },
  "daily_stats": {
    "produced_today": 25.5,
    "produced_dollars": 7.65,
    "grid_kwh": 2.3
  }
}
```


## Development

### Local Setup

```bash
# Clone repository
git clone https://github.com/victron-venus/inverter-dashboard.git
cd inverter-dashboard

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Optional: ./site_config.py for Home Assistant direct mode (gitignored)
./scripts/init-config.sh

# Run
python server.py --mqtt-host your-mqtt-broker
```

### Build Docker Image Locally

```bash
docker build -t inverter-dashboard .
docker run -p 8080:8080 -e MQTT_HOST=192.168.1.100 inverter-dashboard
```

## Multi-Architecture Support

Docker images are built for:
- `linux/amd64` (x86_64)
- `linux/arm64` (Raspberry Pi 4, Apple Silicon, etc.)

## Related Projects

This project is part of the Victron Venus OS integration suite:

| Project | Description |
|---------|-------------|
| [inverter-control](https://github.com/victron-venus/inverter-control) | Advanced ESS external control system with grid-zero targeting |
| **inverter-dashboard** (this) | Real-time web dashboard (Python/FastAPI) via MQTT |
| [inverter-dashboard-go](https://github.com/victron-venus/inverter-dashboard-go) | High-performance Go rewrite of the web dashboard |
| [inverter-desktop](https://github.com/victron-venus/inverter-desktop) | Native desktop application (Rust/Tauri) for system monitoring |
| [dbus-mqtt-battery](https://github.com/victron-venus/dbus-mqtt-battery) | MQTT to D-Bus bridge for JBD BMS battery integration |
| [dbus-tasmota-pv](https://github.com/victron-venus/dbus-tasmota-pv) | Tasmota smart plug integration as a PV inverter on D-Bus |
| [esphome-jbd-bms-mqtt](https://github.com/victron-venus/esphome-jbd-bms-mqtt) | ESP32 Bluetooth monitor for JBD BMS batteries |
| [inverter-monitoring](https://github.com/victron-venus/inverter-monitoring) | TIG (Telegraf, InfluxDB, Grafana) monitoring stack |
| [terraform-github-victron](https://github.com/4alvit/terraform-github-victron) | Infrastructure as Code for the GitHub organization |

## Author

Created by [@4alvit](https://github.com/4alvit)

## License

MIT License - see [LICENSE](LICENSE).

---

**Note:** This is a community project and is not affiliated with Victron Energy.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature-name`)
3. Commit your changes
4. Push to the branch (`git push origin feature-name`)
5. Create a Pull Request

## Support

For issues specific to:
- **MQTT connectivity**: Check broker reachability and topic subscriptions
- **WebSocket errors**: Verify port accessibility and firewall settings
- **Home Assistant integration**: Validate token and entity availability
- **Docker deployment**: Review container logs and volume mounts
- **This project**: Open an issue in this repository
