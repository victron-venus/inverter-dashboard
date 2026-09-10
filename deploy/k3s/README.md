# Deploy inverter-dashboard (Python) on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.

Image: `alvit/inverter-dashboard:latest` (Docker Hub; existing `docker-publish.yml`).

Default MQTT broker: cluster Mosquitto
`192.168.160.150:1883` (Cerbo Venus MQTT).

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

## Smoke (SPA)

After rollout of a new `alvit/inverter-dashboard:latest`, confirm the Vue index is served (not the "Vue SPA not built" stub):

```bash
curl -sS http://inverter-dashboard.mp.2560801.xyz/ | grep -E 'id="app"|/assets/'
```

Server accepts both layouts: `static/dist/index.html` (export_dist.sh) and flat `static/index.html` + `static/assets/` (docker-publish image). Vite assets are mounted at `/assets`.
