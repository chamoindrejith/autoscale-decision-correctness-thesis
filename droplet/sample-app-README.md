# Sample App (Ballerina)

The workload under test. A small HTTP service with one CPU-intensive endpoint
and structured "time-taken" logs — the data source for Latency_before /
Latency_after in the SES formula.

## Endpoints

| Method | Path              | Purpose                                   |
|--------|-------------------|-------------------------------------------|
| GET    | `/api/health`     | Liveness / readiness probe                |
| GET    | `/api/compute?n=` | CPU-intensive handler. `n` = iterations.  |
| GET    | `/api/light`      | Cheap endpoint (baseline)                 |

Ports:
- `9000` — HTTP
- `9797` — Prometheus `/metrics`

## Files

| File              | What it is                                                        |
|-------------------|-------------------------------------------------------------------|
| `Ballerina.toml`  | Package manifest. `observabilityIncluded = true` enables metrics. |
| `Config.toml`     | Runtime config. Enables Prometheus reporter on port 9797.         |
| `service.bal`     | The service code.                                                 |
| `Dockerfile`      | Multi-stage image build.                                          |

## Build locally (on the Droplet)

```bash
# Install Ballerina CLI (if not done)
bash ../01-droplet-setup/install-ballerina.sh

bal build
# -> target/bin/autoscale_test.jar
```

## Run locally

```bash
bal run
# in another shell:
curl 'http://localhost:9000/api/compute?n=1000000'
curl  http://localhost:9797/metrics | head
```

## Containerize

```bash
docker build -t autoscale-sample:v1 .

# Sanity check
docker run --rm -p 9000:9000 -p 9797:9797 autoscale-sample:v1 &
curl http://localhost:9000/api/health
docker stop $(docker ps -lq)
```

## Push into k3s

k3s runs containerd (not Docker) so images built with `docker build` need to
be imported:

```bash
docker save autoscale-sample:v1 | sudo k3s ctr images import -
sudo k3s ctr images ls | grep autoscale-sample
```

Now the Deployment (see `../03-kubernetes-manifests/01-deployment.yaml`) can
reference `autoscale-sample:v1` with `imagePullPolicy: Never`.

## Tuning the work size

The default `defaultIterations = 500000` in `Config.toml` is a starting point.
On a 2vCPU Intel Droplet, one request should take ~30–80 ms. You want:

- Idle: < ~10 requests/sec keeps CPU below HPA threshold.
- Step load: 50–100 req/sec should push CPU well over threshold.

Measure once, then tune `defaultIterations` up or down so the numbers land in
that range.
