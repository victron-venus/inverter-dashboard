# Deploy inverter-dashboard (Python) on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.

Image: `alvit/inverter-dashboard:latest` (Docker Hub; existing `docker-publish.yml`).

Default MQTT broker: cluster Mosquitto
`mosquitto.homeassistant.svc.cluster.local:1883`.

## Ingress / DNS

Uses **Traefik on mp** (`ingressClassName: traefik-mp`, externalIP
`192.168.151.107`). See `4alvit/k3s-self-healing` → `deployments/00-traefik-mp/`.

- Host: `http://inverter.mp.2560801.xyz`
- OpenWRT (one line): `address=/mp.2560801.xyz/192.168.151.107`

## Apply

```bash
# Replace placeholder Secret with a real local_config.py (HA_TOKEN etc.) before prod use
kubectl --context k3s-heaven apply -k deploy/k3s
kubectl --context k3s-heaven -n inverter-dashboard get pods,ingress -o wide
# expect NODE=mp, class traefik-mp, host inverter.mp.2560801.xyz
```

Probes use `/api/state` (root `/` returns 404 until the Vue SPA is bundled).
`HOST=0.0.0.0` and MQTT args target the in-cluster Mosquitto broker.

Do **not** confuse with `inverter-dashboard-go` (Cerbo-oriented). This Python image is the multi-arch NAS/k3s path.
