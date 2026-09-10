# Deploy inverter-dashboard (Python) on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.

Image: `alvit/inverter-dashboard:latest` (Docker Hub; existing `docker-publish.yml`).

Default MQTT broker: cluster Mosquitto
`mosquitto.homeassistant.svc.cluster.local:1883`.

## Apply

```bash
# Replace placeholder Secret with a real local_config.py (HA_TOKEN etc.) before prod use
kubectl apply -k deploy/k3s
kubectl -n inverter-dashboard get pods -o wide   # expect NODE=mp
```

Do **not** confuse with `inverter-dashboard-go` (Cerbo-oriented). This Python image is the multi-arch NAS/k3s path.

## Smoke (SPA)

After rollout of a new `alvit/inverter-dashboard:latest`, confirm the Vue index is served (not the "Vue SPA not built" stub):

```bash
curl -sS http://inverter-dashboard.mp.2560801.xyz/ | grep -E 'id="app"|/assets/'
```

Server accepts both layouts: `static/dist/index.html` (export_dist.sh) and flat `static/index.html` + `static/assets/` (docker-publish image). Vite assets are mounted at `/assets`.
