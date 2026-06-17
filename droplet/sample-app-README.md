# Sample App (Ballerina)

The workload under test: a small HTTP service with one CPU-intensive endpoint
and structured "time-taken" logs. Its request latency is the data source for
`Latency_before` and `Latency_after` in the SES formula.

## Endpoints

| Method | Path              | Purpose                                   |
|--------|-------------------|-------------------------------------------|
| GET    | `/api/health`     | Liveness / readiness probe                |
| GET    | `/api/compute?n=` | CPU-intensive handler; `n` = iterations   |
| GET    | `/api/light`      | Cheap endpoint (baseline)                 |

Ports:
- `9000` — HTTP
- `9797` — Prometheus `/metrics`

## Files

| File              | Description                                                       |
|-------------------|-------------------------------------------------------------------|
| `Ballerina.toml`  | Package manifest; `observabilityIncluded = true` enables metrics  |
| `Config.toml`     | Runtime config; enables Prometheus reporter on port 9797          |
| `service.bal`     | The service code                                                  |
| `Dockerfile`      | Multi-stage image build                                           |

## Build (on the droplet)

```bash
# Install Ballerina CLI if not already installed
bash ../install-ballerina.sh

bal build
# Output: target/bin/autoscale_test.jar
```

## Run

```bash
bal run
# In another shell:
curl 'http://localhost:9000/api/compute?n=1000000'
curl  http://localhost:9797/metrics | head
```

## Containerise

```bash
docker build -t autoscale-sample:v1 .

# Sanity check
docker run --rm -p 9000:9000 -p 9797:9797 autoscale-sample:v1 &
curl http://localhost:9000/api/health
docker stop $(docker ps -lq)
```

## Import into k3s

k3s runs containerd rather than Docker, so images built with `docker build`
must be imported:

```bash
docker save autoscale-sample:v1 | sudo k3s ctr images import -
sudo k3s ctr images ls | grep autoscale-sample
```

The Deployment manifest (`configs/01-deployment.yaml`) then references
`autoscale-sample:v1` with `imagePullPolicy: Never`.

## Tuning the Work Size

The default `defaultIterations = 500000` in `Config.toml` is a starting
value. On a 2-vCPU Intel droplet, a single request should take ~30–80 ms.
Target ranges:

- Idle: fewer than ~10 requests/sec keeps CPU below the HPA threshold.
- Step load: 50–100 req/sec drives CPU well over the threshold.

A baseline measurement should be taken once, then `defaultIterations`
adjusted so the observed numbers land in that range.
