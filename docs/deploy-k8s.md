# Running it on Kubernetes

```bash
kind create cluster --name atmpl
docker build --target runtime -t atmpl:local .
kind load docker-image atmpl:local --name atmpl

kubectl kustomize deploy/k8s/overlays/dev \
  | sed 's#ghcr.io/mohamed3042/enterprise-ai-automation-templates:latest#atmpl:local#' \
  | kubectl apply -f -

kubectl -n atmpl-dev rollout status deployment/atmpl --timeout=180s
kubectl -n atmpl-dev port-forward svc/atmpl 8080:80
# then: http://127.0.0.1:8080  ·  /llmops  ·  /evals  ·  /metrics
```

That is what CI does on every push (`k8s-smoke`), so the commands above are the ones that are
known to work rather than the ones that ought to.

## What each overlay is for

| | `dev` | `prod` |
|---|---|---|
| Replicas | 1 | 2 |
| Database | SQLite on an `emptyDir` | PostgreSQL, URL from a Secret |
| Schema | created in-process | `atmpl db upgrade` in an init container |
| API | open demo (`ATMPL_DEMO_OPEN_API=1`) | credential-only |
| Traces | `console` | `otlp` |
| Namespace | `atmpl-dev` | `atmpl` |

`dev` exists to stand up with nothing provisioned — no database, no secrets, no ingress
controller — which is why it is the one CI can smoke-test. `prod` is the shape a real cluster
would take.

## Before a real deployment

1. **Replace the Secret.** `deploy/k8s/base/secret.yaml` is a template; every value reads
   `REPLACE_…` and a test asserts it stays that way. Supply the real thing from your secret
   manager, or:

   ```bash
   kubectl -n atmpl create secret generic atmpl-secrets \
     --from-literal=ATMPL_SECRET_JWT_SIGNING_KEY="$(openssl rand -hex 32)" \
     --from-literal=ATMPL_SECRET_SESSION_SIGNING_KEY="$(openssl rand -hex 32)" \
     --from-literal=ATMPL_ADMIN_USER=you \
     --from-literal=ATMPL_ADMIN_PASSWORD_HASH="$(atmpl users hash-password)"
   ```

   The Deployment marks the Secret optional, so a cluster without it still starts — with
   process-lifetime signing keys, which `atmpl doctor` reports as `EPHEMERAL`.

2. **Create the database Secret** `prod` expects: `kubectl -n atmpl create secret generic
   atmpl-database --from-literal=url='postgresql+psycopg://…'`.

3. **Set the Ingress host.** `atmpl.example.invalid` is a placeholder, and the TLS secret
   `atmpl-tls` is yours to provision.

4. **Read the NetworkPolicy.** It is default-deny both ways, and the allows are explicit:
   DNS, PostgreSQL by pod label, outbound 443 for signed webhook deliveries and a hosted
   model provider, inbound 8000 from `ingress-nginx` and `monitoring`. A deployment that
   runs entirely on the mock provider should delete the 443 egress rule.

5. **Know what `/metrics` is.** It is open inside the cluster by design — counts and
   histograms, never payloads — and the NetworkPolicy plus the Ingress are what keep it off
   the internet. `ATMPL_METRICS_PUBLIC=0` additionally requires the `metrics:read` scope.

## Things that will bite

- **A read-only root filesystem needs `/app/var`.** The hash-chained ledger is a file. Remove
  that mount and the pod starts, serves, and cannot audit.
- **The rate limiter is per-process.** With two replicas the effective ceiling is twice
  `ATMPL_RATE_LIMIT`. A shared store is named in `SECURITY.md` as missing, not implied.
- **`emptyDir` means the ledger dies with the pod.** That is correct for `dev` and wrong for
  anything real: `prod` should mount a PersistentVolumeClaim there, or ship the ledger to an
  object-locked bucket.
- **A ConfigMap key that is not a real setting is silently ignored** by pydantic-settings.
  `tests/test_deploy.py` checks every key against `Settings.model_fields` for that reason.

## Validating without a cluster

```bash
kubectl kustomize deploy/k8s/overlays/prod | kubeconform -strict -summary -
python -m pytest tests/test_deploy.py
```

The first answers "is this valid Kubernetes"; the second answers "does it still say what we
promised" — non-root, read-only, three probes, default-deny, placeholders only. CI runs both.
