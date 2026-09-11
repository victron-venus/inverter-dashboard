# Deploy inverter-dashboard (Python) on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.

Image: `alvit/inverter-dashboard:latest` (Docker Hub; existing `docker-publish.yml`).

## Data source: inverter-gateway (IGW)

> **Warning:** `02-secret.example.yaml` is **not** in `kustomization.yaml` resources. Apply real Secrets out-of-band; never `kubectl apply -f 02-secret.example.yaml` against prod.

**Production ConfigMap uses IGW — not Cerbo MQTT.**

Synology already runs [inverter-gateway](https://github.com/victron-venus/inverter-gateway)
(loopback `127.0.0.1:9150`, Cloudflare Tunnel). Dashboard pods on `mp` poll

`https://victron.2560801.xyz/v1/snapshot`

with Cloudflare Access service-token headers + `GATEWAY_API_TOKEN` bearer
(same path as inverter-desktop Remote Gateway). This keeps a **single** Cerbo
MQTT client on the NAS and avoids every app hammering `192.168.160.150:1883`.

`fastapi-mqtt-gateway` on mp stays scaled to 0 on purpose — do not stand up a
second Cerbo client flood.

| Env | Where | Purpose |
|-----|-------|---------|
| `GATEWAY_ENABLED` | ConfigMap | `true` on mp |
| `GATEWAY_URL` | ConfigMap | `https://victron.2560801.xyz` |
| `GATEWAY_POLL_INTERVAL` | ConfigMap | seconds (default 2) |
| `GATEWAY_ACCESS_CLIENT_ID` | Secret `inverter-dashboard-gateway` | CF Access service token |
| `GATEWAY_ACCESS_CLIENT_SECRET` | Secret | CF Access service token |
| `GATEWAY_API_TOKEN` | Secret | App bearer (`Authorization: Bearer …`) |
| `CERBO_PORTAL_ID` | ConfigMap | water/EV instance defaults when mapping snapshot |
| `MQTT_HOST` | — | **omit on mp** (Cerbo-direct is local/dev only) |

Create the gateway Secret from local files (never commit):

```bash
# values from foss-cloudflare-infrastructure/local.generated.service-token.json
# + inverter-gateway/.env GATEWAY_API_TOKEN
kubectl --context k3s-heaven -n inverter-dashboard create secret generic \
  inverter-dashboard-gateway \
  --from-literal=GATEWAY_ACCESS_CLIENT_ID=… \
  --from-literal=GATEWAY_ACCESS_CLIENT_SECRET=… \
  --from-literal=GATEWAY_API_TOKEN=… \
  --dry-run=client -o yaml | kubectl --context k3s-heaven apply -f -
```

Local/dev: leave `GATEWAY_ENABLED` unset/false and set `MQTT_HOST` to the Cerbo
broker as before.

## Ingress / DNS

Uses **Traefik on mp** (`ingressClassName: traefik-mp`, externalIP
`192.168.151.107`). See `4alvit/k3s-self-healing` → `deployments/00-traefik-mp/`.

- Host: `http://inverter-dashboard.mp.2560801.xyz` (full project name)
- OpenWRT (one line): `address=/mp.2560801.xyz/192.168.151.107`

## Apply

```bash
# Replace placeholder Secret with a real local_config.py (HA_TOKEN etc.) before prod use
kubectl --context k3s-heaven apply -k deploy/k3s
kubectl --context k3s-heaven -n inverter-dashboard get pods,ingress -o wide
# expect NODE=mp, class traefik-mp, host inverter-dashboard.mp.2560801.xyz
```

Do **not** confuse with `inverter-dashboard-go` (Cerbo-oriented). This Python image is the multi-arch NAS/k3s path.

## Smoke (SPA + IGW)

```bash
curl -sS http://inverter-dashboard.mp.2560801.xyz/ | grep -E 'id="app"|/assets/'
curl -sS http://inverter-dashboard.mp.2560801.xyz/api/state
# expect data_source=igw, gateway_connected=true, mqtt_connected=true (IGW plane)
kubectl --context k3s-heaven -n inverter-dashboard logs deploy/inverter-dashboard --tail=50
# expect: IGW connected to https://victron.2560801.xyz
```

Server accepts both layouts: `static/dist/index.html` (export_dist.sh) and flat `static/index.html` + `static/assets/` (docker-publish image). Vite assets are mounted at `/assets`.
