# Deploy inverter-dashboard (Python) on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.

Image: `alvit/inverter-dashboard:latest` (Docker Hub; existing `docker-publish.yml`).

## Data source: inverter-gateway (IGW)

> **Warning:** `02-secret.example.yaml` is **not** in `kustomization.yaml` resources. Apply real Secrets out-of-band; never `kubectl apply -f 02-secret.example.yaml` against prod.

**The checked-in ConfigMap uses the HTTPS IGW endpoint.**

The recovered September 10, 2026 deployment draft records
[inverter-gateway](https://github.com/victron-venus/inverter-gateway) in namespace
`synology-apps` (Service `inverter-gateway`, port `8080`, NodePort `30150`).
On September 13, the documented NodePort `/health` returned HTTP 200 with
`status: ok` and `mqtt_connected: true`; the Kubernetes Service layout was not
independently rechecked. The checked-in URL is the public HTTPS endpoint and requires Cloudflare Access
headers plus `GATEWAY_API_TOKEN`. Operators can select a LAN route when public
egress is unavailable; NodePort and ClusterIP use the bearer token only. All
options share the gateway's Cerbo MQTT client.

### GATEWAY_URL choices

| Mode | `GATEWAY_URL` | Auth |
|------|---------------|------|
| LAN alternative | `http://192.168.175.130:30150` (syn NodePort) | Bearer `GATEWAY_API_TOKEN` only; leave CF Access id/secret empty |
| ClusterIP (preferred when overlay healthy) | `http://inverter-gateway.synology-apps.svc:8080` | Bearer only |
| **Checked-in default / off-cluster** | `https://victron.2560801.xyz` | CF Access service-token headers + bearer |

**Historical diagnosis (September 10 draft):** from `mp`, ClusterIP to
`synology-apps` on node `syn` was reported to fail
(rising `gateway_errors`, `gateway_connected=false`) while the same `/health` and
`/v1/snapshot` succeed from `h7` and via syn NodePort `192.168.175.130:30150`.
The draft also reported `mp` flapping NotReady (kubelet 502), interrupting
Ingress/`kubectl exec`. These node and overlay conditions were not revalidated. The NodePort route can be selected while diagnosing overlay/DNS from `mp`.
LAN HTTP sends the bearer token and telemetry without TLS. Use that option only
on a network whose exposure is acceptable; otherwise retain HTTPS and repair
the Cloudflare Access service-token configuration. A successful `/health` request
does not verify authenticated snapshots or connectivity from every cluster node.

The same draft kept `fastapi-mqtt-gateway` on `mp` scaled to zero to avoid
duplicate Cerbo clients. Check current deployments before changing that layout.

| Env | Where | Purpose |
|-----|-------|---------|
| `GATEWAY_ENABLED` | ConfigMap | `true` on mp |
| `GATEWAY_URL` | ConfigMap | HTTPS default; optional LAN routes above |
| `GATEWAY_POLL_INTERVAL` | ConfigMap | seconds (default 2) |
| `GATEWAY_ACCESS_CLIENT_ID` | Secret `inverter-dashboard-gateway` | CF Access (empty for NodePort/ClusterIP) |
| `GATEWAY_ACCESS_CLIENT_SECRET` | Secret | CF Access (empty for NodePort/ClusterIP) |
| `GATEWAY_API_TOKEN` | Secret | App bearer (`Authorization: Bearer …`) |
| `CERBO_PORTAL_ID` | ConfigMap | water/EV instance defaults when mapping snapshot |
| `MQTT_HOST` | ConfigMap `""` | Empty on mp (IGW-only). Set a broker host to enable dual-path MQTT-first + IGW fallback. |

Create the gateway Secret from local files (never commit):

```bash
# HTTPS default: CF Access service token plus inverter-gateway API token
kubectl --context k3s-heaven -n inverter-dashboard create secret generic \
  inverter-dashboard-gateway \
  --from-literal=GATEWAY_ACCESS_CLIENT_ID=… \
  --from-literal=GATEWAY_ACCESS_CLIENT_SECRET=… \
  --from-literal=GATEWAY_API_TOKEN=… \
  --dry-run=client -o yaml | kubectl --context k3s-heaven apply -f -

# For an explicitly selected NodePort/ClusterIP route, leave both
# GATEWAY_ACCESS_CLIENT_ID and GATEWAY_ACCESS_CLIENT_SECRET empty.
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
# expect data_source=igw, gateway_connected=true (native MQTT may remain disconnected in IGW-only mode); measured zero values are valid
kubectl --context k3s-heaven -n inverter-dashboard logs deploy/inverter-dashboard --tail=50
# expect: IGW connected to the selected GATEWAY_URL
```

Server accepts both layouts: `static/dist/index.html` (export_dist.sh) and flat `static/index.html` + `static/assets/` (docker-publish image). Vite assets are mounted at `/assets`.
