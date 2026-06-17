# Experimental Setup Guide
## Evaluating Kubernetes Autoscaling Correctness Using Observability Data

This guide walks you through building the full experimental environment on DigitalOcean, from zero to a running Kubernetes cluster with a Ballerina sample app, HPA, Prometheus, Grafana, and an HPA watcher. Everything is explained as if you've never touched this area before.

---

## Part 0 — Quick Orientation (Read this first)

Before we touch anything, let's make sure you understand the pieces. If you already know something, skip it.

### What is Kubernetes?
Kubernetes (often written as **k8s**, because there are 8 letters between k and s) is software that runs and manages your applications across one or more computers. You tell Kubernetes *what* you want ("run 3 copies of my app, expose it on port 80") and it figures out *how*. If one copy crashes, Kubernetes restarts it. If traffic spikes, Kubernetes can scale up.

### What is k3s?
k3s is a small, easy-to-install version of Kubernetes. A full Kubernetes install takes hundreds of MB and many components. k3s packs it all into a single binary around 60MB. It's real Kubernetes — the same APIs, the same behavior — just lighter. Perfect for a research experiment on one VM.

### What is a Pod, Deployment, Service, HPA?
- **Pod** — the smallest unit Kubernetes manages. A pod wraps one container (your running app).
- **Deployment** — a controller that says "I want N pods of this app, always." If a pod dies, the Deployment makes a new one.
- **Service** — a stable network address in front of the pods. Pods come and go; the Service stays.
- **HPA (Horizontal Pod Autoscaler)** — watches a metric (like CPU) and changes the Deployment's pod count automatically. This is the thing your research is studying.

### What is Prometheus? Grafana?
- **Prometheus** — a metrics database. It scrapes numbers from your apps every few seconds (CPU %, request count, latency) and stores them as time-series data.
- **Grafana** — a dashboard tool that draws graphs from Prometheus data.
- Together, they're the most common open-source observability stack for Kubernetes.

### What is Ballerina?
Ballerina is a programming language built for cloud-native services (APIs, integrations). It's written for this kind of work — it has built-in HTTP handling, structured logging, and native Prometheus metrics. The `bal` command compiles `.bal` files into a runnable JAR and can even generate container images.

### What is a Droplet?
On DigitalOcean, a "Droplet" is just their name for a virtual machine (VM). Think of it as a rented Linux computer in the cloud that you access over SSH.

### How our experiment fits together

```
                    ┌────────────────────────────────────────────┐
                    │        DigitalOcean Droplet (VM)           │
                    │                                            │
                    │   ┌────────────────────────────────────┐   │
                    │   │       k3s (Kubernetes)             │   │
                    │   │                                    │   │
                    │   │  ┌──────────┐   ┌──────────────┐   │   │
                    │   │  │ Ballerina│   │ HPA Watcher  │   │   │
                    │   │  │ sample   │◄──│ (records     │   │   │
                    │   │  │ app pods │   │  decisions)  │   │   │
                    │   │  └────┬─────┘   └──────────────┘   │   │
                    │   │       │                            │   │
                    │   │       │ metrics                    │   │
                    │   │       ▼                            │   │
                    │   │  ┌──────────┐  ┌──────────┐        │   │
                    │   │  │Prometheus│─►│ Grafana  │        │   │
                    │   │  └──────────┘  └──────────┘        │   │
                    │   │       ▲                            │   │
                    │   │       │                            │   │
                    │   │  ┌────┴─────┐                      │   │
                    │   │  │   HPA    │                      │   │
                    │   │  └──────────┘                      │   │
                    │   └────────────────────────────────────┘   │
                    └──────────────▲─────────────────────────────┘
                                   │
                                   │ load traffic
                                   │
                          ┌────────┴────────┐
                          │  Your laptop    │
                          │  (k6 / hey)     │
                          └─────────────────┘
```

---

## Part 1 — DigitalOcean Account & Credit Activation

### 1.1 Redeem your GitHub Student credit
1. Go to https://education.github.com/pack and sign in with GitHub.
2. Find **DigitalOcean** in the list. Click **Get access**.
3. Follow the link to DigitalOcean and create an account (use your student email).
4. The $200 credit is automatically applied. You'll see it in **Billing → Credits**.

> The credit expires in 12 months. Stop/destroy the Droplet when you're not actively running experiments to save money.

### 1.2 Add SSH key to DigitalOcean

SSH (Secure Shell) is how you log in to a remote Linux server from your terminal. You authenticate with a **key pair** (a private key on your Mac, a public key on the server). Passwords are weaker and DO recommends keys.

On your **Mac terminal**, run:

```bash
# Check if you already have a key
ls ~/.ssh/id_ed25519.pub

# If you get "No such file", create one
ssh-keygen -t ed25519 -C "chamodiindrejith@gmail.com"
# Press Enter to accept the default location
# Optionally set a passphrase (or leave blank for convenience)

# Print the public key — you'll paste this into DigitalOcean
cat ~/.ssh/id_ed25519.pub
```

In the DigitalOcean UI: **Settings → Security → Add SSH Key**. Paste the output of `cat` above. Give it a name like `mac-laptop`.

### 1.3 Create the Droplet
In DigitalOcean, click **Create → Droplet**:

| Setting | Value |
|---|---|
| Region | Pick the one closest to you (e.g., Singapore, Bangalore) |
| OS | Ubuntu 24.04 LTS |
| Droplet type | Basic → Regular (SSD) |
| CPU | Premium Intel, **2 vCPU / 4 GB RAM** (~$24/month) |
| Authentication | SSH Key (the one you just added) |
| Hostname | `k3s-research` |

Why 4GB? k3s needs ~1GB, Prometheus+Grafana ~1GB, the app pods + overhead fill the rest. 2GB would be too tight. $24/month × 8 months = ~$192, well within your $200 credit.

After a minute you'll get a public IPv4 like `134.209.x.x`. Copy it.

### 1.4 Log in

```bash
ssh root@<DROPLET_IP>
```

If this is the first time, type `yes` when asked about the fingerprint. You should see the Ubuntu welcome banner. You're in!

See: `experiment-setup/01-droplet-setup/create-droplet.md` for a printable version of this.

---

## Part 2 — Install k3s

k3s installs in one command. On the Droplet (after you SSH in):

```bash
curl -sfL https://get.k3s.io | sh -
```

That's it. This:
- Downloads the k3s binary
- Starts it as a systemd service
- Creates a kubeconfig at `/etc/rancher/k3s/k3s.yaml`

Verify:

```bash
sudo k3s kubectl get nodes
# You should see one Ready node
```

`kubectl` is the command-line tool to talk to a Kubernetes cluster. `k3s kubectl` is the bundled version. To avoid typing `sudo k3s` every time, set up a nicer alias:

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config

# Install the standalone kubectl
sudo snap install kubectl --classic

# Now you can just type:
kubectl get nodes
```

See: `experiment-setup/01-droplet-setup/install-k3s.sh` for an all-in-one script.

### 2.1 Install Helm
Helm is Kubernetes' package manager (think `apt` for k8s). We'll use it to install Prometheus/Grafana in Part 5.

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
```

### 2.2 Install Docker (to build the Ballerina image)
k3s uses its own container runtime (containerd), but you need Docker to *build* images:

```bash
curl -fsSL https://get.docker.com | sh
docker version
```

---

## Part 3 — The Ballerina Sample App

### What this app does
We need a small HTTP service that:
1. Has one endpoint we can hit with load.
2. **Burns CPU** in a controlled way — otherwise, no load test will ever trigger HPA.
3. **Logs each request's time-taken** (the "time-taken audit log" from your research PDF).
4. Exposes Prometheus metrics so we can graph latency.

Ballerina's built-in observability gives us (3) and (4) almost for free.

### Files (already created for you)
Look in `experiment-setup/02-sample-app/`:
- `Ballerina.toml` — project manifest (like `package.json` or `pom.xml`)
- `Config.toml` — runtime config (enables Prometheus metrics)
- `service.bal` — the actual service code
- `Dockerfile` — turns the compiled JAR into a container image
- `README.md` — local build instructions

### How to build
On your Droplet, clone (or copy) the project folder over, then:

```bash
# Install Ballerina CLI on the Droplet
curl -L https://dist.ballerina.io/downloads/2201.10.0/ballerina-2201.10.0-swan-lake-linux-x64.deb -o ballerina.deb
sudo dpkg -i ballerina.deb
bal version

# Build the project
cd ~/experiment-setup/02-sample-app
bal build

# Build the container image
docker build -t autoscale-sample:v1 .

# Import the image into k3s (so it can find it without a registry)
docker save autoscale-sample:v1 | sudo k3s ctr images import -
```

> **Why `ctr images import`?** k3s uses containerd, not Docker, to run containers. When you `docker build`, the image lives in Docker's cache, not containerd's. This command copies it over. In a real setup you'd push to a registry (Docker Hub / DigitalOcean Container Registry) instead.

---

## Part 4 — Deploy the App + HPA

### Files
Look in `experiment-setup/03-kubernetes-manifests/`:
- `00-namespace.yaml` — a folder-like boundary for our resources
- `01-deployment.yaml` — tells k8s to run N copies of the app
- `02-service.yaml` — gives the pods a stable internal address
- `03-hpa.yaml` — the HPA policy (your research subject!)
- `04-servicemonitor.yaml` — tells Prometheus to scrape our app

### Apply them

```bash
cd ~/experiment-setup/03-kubernetes-manifests
kubectl apply -f .

# Watch pods come up
kubectl get pods -n autoscale-research -w
# Press Ctrl+C when all show Running

# Check the HPA
kubectl get hpa -n autoscale-research
```

### HPA threshold — "Idle Usage + 25%"
Your research PDF says the HPA threshold should be set to `idle usage + 25%`. This is tuning. The idea: we don't want HPA to fire during normal background noise; it should fire only when there's real load.

Workflow:
1. Deploy the app with a **dummy HPA** (e.g., threshold = 80%).
2. Let it sit idle for 10 minutes. Look at actual CPU usage with:
   ```bash
   kubectl top pod -n autoscale-research
   ```
3. If idle is ~5%, set HPA threshold to `5 + 25 = 30`. Edit `03-hpa.yaml`, change `averageUtilization`, re-apply.

We start with `averageUtilization: 50` in the manifest — adjust after measuring.

---

## Part 5 — Prometheus and Grafana

We use the **kube-prometheus-stack** Helm chart. It bundles Prometheus, Grafana, Alertmanager, and dashboards together.

```bash
cd ~/experiment-setup/04-monitoring
bash install-monitoring.sh
```

After ~2 minutes:

```bash
kubectl get pods -n monitoring
```

You should see pods for `prometheus`, `grafana`, `kube-state-metrics`, etc., all Running.

### Access Grafana
Grafana runs inside the cluster. To view it from your laptop, port-forward:

```bash
# On the Droplet
kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80 --address 0.0.0.0
```

Then open `http://<DROPLET_IP>:3000` in your browser. Default login: `admin` / `prom-operator`.

> Firewall note: DigitalOcean Droplets are open by default. If you added a Cloud Firewall, allow port 3000 inbound.

A pre-built dashboard for HPA + latency is included at `experiment-setup/04-monitoring/grafana-dashboard.json`. Import it via **Dashboards → New → Import**.

---

## Part 6 — HPA Watcher (The Research Instrument)

This is the crucial piece for your research. Kubernetes emits events when HPA scales, but they're not structured for analysis. The watcher:
1. Uses the Kubernetes API to watch HPA objects.
2. Detects when `desiredReplicas` changes.
3. Records a JSON event: `{timestamp, from, to, reason, metrics}`.
4. Writes events to a file (and to stdout for Prometheus to scrape, later).

### Files
Look in `experiment-setup/05-hpa-watcher/`:
- `watcher.py` — the Python watcher
- `requirements.txt` — `kubernetes` client library
- `Dockerfile`
- `watcher-deployment.yaml` — runs the watcher inside the cluster with RBAC permissions

### Deploy

```bash
cd ~/experiment-setup/05-hpa-watcher
docker build -t hpa-watcher:v1 .
docker save hpa-watcher:v1 | sudo k3s ctr images import -
kubectl apply -f watcher-deployment.yaml

# Check logs
kubectl logs -n autoscale-research -l app=hpa-watcher -f
```

Every scaling decision will be written to `/data/hpa-events.jsonl` inside the pod. We'll analyze them in a later phase.

---

## Part 7 — Load Testing (Preview)

The load-gen scripts for the four workloads (Step, Burst, Ramp, Noisy) are in `experiment-setup/06-load-tests/`, written for **k6**. You'll run these in Phase 2 of the project.

From your Mac (install k6 with `brew install k6`):

```bash
# Point at the Droplet's app endpoint — details once the Service is exposed
k6 run experiment-setup/06-load-tests/step-load.js
```

To expose the app externally for load testing, use a NodePort Service (already set in `02-service.yaml` — accessible at `<DROPLET_IP>:30080`).

---

## Part 8 — What Comes Next (after setup)

Once setup is working, the research phases per your PDF are:
1. ✅ Cluster + app + HPA + Prometheus + Grafana + watcher (this guide)
2. Establish idle baseline, retune HPA to idle+25%
3. Run each workload pattern (Step, Burst, Ramp, Noisy), 5+ repetitions each
4. Collect: HPA decisions (watcher), latency (Ballerina logs + Prometheus), CPU/memory (Prometheus)
5. Calculate SRD and SES per decision (`07-analysis/calculate-scores.py` — to be added)
6. Classify each decision (Correct & Timely / Correct but Late / Unnecessary / Ineffective)
7. Write up findings

We'll tackle each of these as a follow-up.

---

## Cost Management Tips

- **Snapshot + destroy** when you're not experimenting. A snapshot costs $0.06/GB/month vs. $24/month for a running Droplet. Restore when you need to run again.
  ```bash
  # In DigitalOcean UI: Droplet → Snapshots → Take Snapshot → then Destroy the Droplet
  ```
- **Monitor billing**: Account → Billing. Set a $150 alert so you get an email before credits run out.
- **Use `doctl`** (DO's CLI) later if you want to automate create/destroy.

---

## Troubleshooting Cheatsheet

| Symptom | Check |
|---|---|
| `kubectl` command not found | Run `sudo snap install kubectl --classic` |
| Pod stuck in `ImagePullBackOff` | You didn't import the image to containerd. Re-run `docker save … \| sudo k3s ctr images import -` |
| HPA shows `<unknown>` for metrics | Metrics server not ready yet (wait 1–2 min) or resource requests missing in Deployment |
| Grafana shows "No data" | Check `kubectl get servicemonitor -n autoscale-research` exists and labels match Prometheus selector |
| Out of memory on Droplet | Upgrade to 8GB, or reduce Prometheus retention in `prometheus-values.yaml` |

---

## Where to ask me next

- "Walk me through creating the Droplet step by step"
- "Explain the Ballerina code"
- "The HPA isn't scaling — help me debug"
- "Now build the load tests and scoring script"

I'll wait for you to run through the setup and then we'll move to the measurement phase.
