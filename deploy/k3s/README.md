# Deploy inverter-dashboard (Python) on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.

Image: `alvit/inverter-dashboard:latest` (Docker Hub; existing `docker-publish.yml`).

## Data source: inverter-gateway (IGW)

> **Warning:** `02-secret.example.yaml` is **not** in `kustomization.yaml` resources. Apply real Secrets out-of-band; never `kubectl apply -f 02-secret.example.yaml` against prod.

**Production ConfigMap uses IGW — not Cerbo MQTT.**

Synology runs [inverter-gateway](https://github.com/victron-venus/inverter-gateway)
in namespace `synology-apps` (Service `inverter-gateway`, port `8080`, also
NodePort `30150`). Dashboard pods on `mp` poll IGW over LAN (NodePort or ClusterIP) with
**`GATEWAY_API_TOKEN` bearer only** (no Cloudflare Access headers). That avoids
CF 302s that break pod→public egress and keeps a **single** Cerbo MQTT client on
the NAS (avoid every app hammering `192.168.160.150:1883`).

### GATEWAY_URL choices

| Mode | `GATEWAY_URL` | Auth |
|------|---------------|------|
| **k3s / mp (live default)** | `http://192.168.175.130:30150` (syn NodePort) | Bearer `GATEWAY_API_TOKEN` only; leave CF Access id/secret empty |
| ClusterIP (preferred when overlay healthy) | `http://inverter-gateway.synology-apps.svc:8080` | Bearer only |
| Public / off-cluster | `https://victron.2560801.xyz` | CF Access service-token headers + bearer |

**Gap:** from `mp`, ClusterIP to `synology-apps` on node `syn` has been observed to fail
(rising `gateway_errors`, `gateway_connected=false`) while the same `/health` and
`/v1/snapshot` succeed from `h7` and via syn NodePort `192.168.175.130:30150`.
`mp` also flaps NotReady (kubelet 502), which breaks Ingress/`kubectl exec` during
recovery. Prefer NodePort on the Synology LAN until overlay/DNS from `mp` is stable.
CF public URL remains for laptop/desktop Remote Gateway clients — not for in-cluster
pods (CF 302 breaks pod egress).

`fastapi-mqtt-gateway` on mp stays scaled to 0 on purpose — do not stand up a
second Cerbo client flood.

| Env | Where | Purpose |
|-----|-------|---------|
| `GATEWAY_ENABLED` | ConfigMap | `true` on mp |
| `GATEWAY_URL` | ConfigMap | In-cluster IGW (see table); public CF optional |
| `GATEWAY_POLL_INTERVAL` | ConfigMap | seconds (default 2) |
| `GATEWAY_ACCESS_CLIENT_ID` | Secret `inverter-dashboard-gateway` | CF Access (empty for ClusterIP) |
| `GATEWAY_ACCESS_CLIENT_SECRET` | Secret | CF Access (empty for ClusterIP) |
| `GATEWAY_API_TOKEN` | Secret | App bearer (`Authorization: Bearer …`) |
| `CERBO_PORTAL_ID` | ConfigMap | water/EV instance defaults when mapping snapshot |
| `MQTT_HOST` | ConfigMap `""` | Empty on mp (IGW-only). Set a broker host to enable dual-path MQTT-first + IGW fallback. |

Create the gateway Secret from local files (never commit):

```bash
# ClusterIP path: CF Access literals empty; token from inverter-gateway/.env
kubectl --context k3s-heaven -n inverter-dashboard create secret generic \
  inverter-dashboard-gateway \
  --from-literal=GATEWAY_ACCESS_CLIENT_ID= \
  --from-literal=GATEWAY_ACCESS_CLIENT_SECRET= \
  --from-literal=GATEWAY_API_TOKEN=… \
  --dry-run=client -o yaml | kubectl --context k3s-heaven apply -f -

# Public CF path only: also set GATEWAY_ACCESS_* from
# foss-cloudflare-infrastructure/local.generated.service-token.json
# and GATEWAY_URL=https://victron.2560801.xyz
```

Local/dev: leave `GATEWAY_ENABLED` unset/false and set `MQTT_HOST` to the Cerbo
broker as before. Dual-path (both `MQTT_HOST` and IGW): MQTT wins when the broker
accepts TCP; otherwise IGW, with recovery probes while on IGW.

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
# expect data_source=igw, gateway_connected=true, mqtt_connected=true (IGW plane), non-zero gt/tt/battery tiles
kubectl --context k3s-heaven -n inverter-dashboard logs deploy/inverter-dashboard --tail=50
# expect: IGW connected to http://192.168.175.130:30150 (or ClusterIP if used)
```

Server accepts both layouts: `static/dist/index.html` (export_dist.sh) and flat `static/index.html` + `static/assets/` (docker-publish image). Vite assets are mounted at `/assets`.
