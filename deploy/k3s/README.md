# Deploy inverter-dashboard (Python) on k3s

These public manifests are examples. Replace the gateway URL, portal identifier,
worker label and ingress host in a local copy before applying them. Keep real
addresses, cluster contexts, operational notes and credentials under
`.local-private/` (ignored by Git) or in a private configuration store.

Image: `alvit/inverter-dashboard` (Docker Hub); `kustomization.yaml` pins the tag.
The deployment example selects `worker-1`; choose a worker in your own cluster.
The namespace and resource names use the generic application name
`inverter-dashboard`.

## Prepare local configuration

```bash
mkdir -p .local-private/dashboard
chmod 700 .local-private .local-private/dashboard
cp deploy/k3s/*.yaml .local-private/dashboard/
chmod 600 .local-private/dashboard/*.yaml
```

Edit that local copy's ConfigMap, Deployment and Ingress for your environment.
Set `CERBO_PORTAL_ID` locally when portal-specific water/EV mapping is needed.
The public ConfigMap leaves it empty and uses `https://gateway.example.com` as
a documentation-only gateway address. Real configuration must stay out of commits.

`02-secret.example.yaml` is deliberately excluded from the Kustomize resources.
Create real Secrets out-of-band; never apply the placeholder Secret to production.
Mount `inverter-dashboard-config` at `/app/config` with your private
`local_config.py` (HA_URL / HA_TOKEN and any other local settings).

## Data source: inverter-gateway (IGW)

`GATEWAY_ENABLED=true` selects the gateway. `MQTT_HOST` is empty in the example,
which keeps the dashboard IGW-only. To enable the existing MQTT-first path with
IGW fallback, set a broker host in your private configuration. In local/dev use,
leave `GATEWAY_ENABLED` unset or false to keep the existing direct MQTT path.

Choose a gateway route for your deployment:

- HTTPS: `https://gateway.example.com`. Use the gateway bearer token and, when
  Cloudflare Access protects the endpoint, the corresponding service-token headers.
- In-cluster example: `http://inverter-gateway.gateway.svc.cluster.local:8080`.
  This uses the gateway bearer token without Cloudflare Access headers.
- NodePort example: `http://192.0.2.10:30080`. This documentation address must be
  replaced locally. LAN HTTP sends the bearer token and telemetry without TLS;
  use HTTPS when that network exposure is not acceptable.

Set `GATEWAY_URL` and `GATEWAY_POLL_INTERVAL` in the local ConfigMap. Keep
`GATEWAY_API_TOKEN`, `GATEWAY_ACCESS_CLIENT_ID` and
`GATEWAY_ACCESS_CLIENT_SECRET` in the `inverter-dashboard-gateway` Secret.
Leave both Access values empty for an explicitly selected route that does not
use Cloudflare Access. Do not copy credentials into the public example.

Create that Secret from a protected local environment file:

```bash
# Set these to your own context and namespace; never commit your kubeconfig.
export KUBE_CONTEXT="my-cluster"
export NAMESPACE="inverter-dashboard"

# Create this file privately with the three GATEWAY_* credential keys above.
chmod 600 .local-private/dashboard-gateway.env
kubectl --context "${KUBE_CONTEXT}" apply -f .local-private/dashboard/00-namespace.yaml
kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" create secret generic \
  inverter-dashboard-gateway \
  --from-env-file=.local-private/dashboard-gateway.env \
  --dry-run=client -o yaml | kubectl --context "${KUBE_CONTEXT}" apply -f -
```

The command creates the namespace before its Secrets. Check your existing
gateway deployments before starting another MQTT client for the same device.

## Ingress and apply

The public ingress uses `dashboard.example.com` and the generic `traefik` class.
Set the real host, ingress class and optional TLS configuration in your local
copy, and configure DNS through your own infrastructure.

```bash
# Apply only after configuring the local manifests and creating both Secrets.
kubectl --context "${KUBE_CONTEXT}" apply -k .local-private/dashboard
kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" get pods,ingress -o wide
```

This Python image is the multi-arch NAS/k3s path. It is distinct from the
Cerbo-oriented `victron-venus/inverter-dashboard-node-red` image.

## Smoke checks

Set these variables to your local deployment values:

```bash
export GATEWAY_URL="https://gateway.example.com"
export DASHBOARD_URL="https://dashboard.example.com"

curl -fsS "${GATEWAY_URL}/health"
curl -fsS "${DASHBOARD_URL}/" | grep -E 'id="app"|/assets/'
curl -fsS "${DASHBOARD_URL}/api/state"
kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" \
  logs deploy/inverter-dashboard --tail=50
```

A successful health request does not establish authenticated snapshot access or
connectivity from every worker. In IGW-only mode, verify `data_source=igw` and
`gateway_connected=true`; native MQTT may remain disconnected. Measured zero
values are valid. Keep deployment-specific results and diagnosis in private notes.

The server accepts both layouts: `frontend/dist` (Dockerfile) and flat `static/`
plus `templates/` (docker-publish image). Vite assets are mounted at `/assets`.
