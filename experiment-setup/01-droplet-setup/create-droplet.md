# Creating the DigitalOcean Droplet

Procedures for provisioning the experimental droplet. DigitalOcean UI labels
may shift between revisions, but the core settings remain consistent.

## Option A — Web UI

1. Sign in at https://cloud.digitalocean.com.
2. Top right: **Create → Droplets**.
3. **Choose Region**: Singapore (SGP1) or Bangalore (BLR1) provide the
   lowest latency from South Asia. Any region functions correctly.
4. **Choose an image**: Ubuntu 24.04 (LTS) x64.
5. **Choose Size**:
   - Droplet Type: **Basic**
   - CPU options: **Premium Intel** — fastest clock; preferred for a
     compute-bound experiment
   - Plan: **4 vCPU / 8 GB RAM / 160 GB SSD** (~$48/month, $0.071/hour)
     — sized for the post-pilot campaign (maxReplicas=10 at 75% HPA
     target). The earlier 2 vCPU / 4 GB size was used only during pilot
     runs and does not match the counted-campaign environment.
6. **Authentication**: SSH Key → select the previously-added key.
7. **Finalize Details**:
   - Quantity: 1
   - Hostname: `k3s-research`
   - Tags: `research`, `hpa-study` (assists with billing filters)
8. Click **Create Droplet**. The provisioning completes in approximately
   60 seconds.
9. The droplet's public IPv4 address appears in the list — record it for
   the subsequent SSH steps.

## Option B — `doctl` CLI

```bash
# Install doctl
brew install doctl

# Authenticate using a Personal Access Token from the DO UI
doctl auth init

# List SSH keys to identify the key ID
doctl compute ssh-key list

# Create the droplet — substitute the correct SSH_KEY_ID
doctl compute droplet create k3s-research \
  --region sgp1 \
  --image ubuntu-24-04-x64 \
  --size s-4vcpu-8gb-intel \
  --ssh-keys SSH_KEY_ID \
  --tag-names research,hpa-study \
  --wait

# Retrieve the assigned public IPv4 address
doctl compute droplet list k3s-research
```

## Initial Log-in

```bash
ssh root@<IP>
# Accept the host fingerprint on first connection
```

Inside the droplet, Ubuntu's welcome MOTD is displayed. Sanity-check the
allocation:

```bash
uname -a
free -h
df -h
```

Expected: 8 GB RAM visible, ~155 GB disk free.

## One-Time Initial Setup

```bash
# Apply pending OS patches
apt update && apt upgrade -y

# Create a non-root user
adduser researcher
usermod -aG sudo researcher

# Replicate the SSH authorized_keys for the new user
rsync --archive --chown=researcher:researcher ~/.ssh /home/researcher

# Basic firewall rules
ufw allow OpenSSH
ufw allow 30080/tcp      # app NodePort (for load tests)
ufw allow 3000/tcp       # Grafana port-forward
ufw --force enable
ufw status
```

Subsequent SSH sessions use the new account: `ssh researcher@<IP>`.

## When Not Actively Experimenting

Two options to reduce cost:

- **Power Off** — keeps storage billed, but the droplet restarts quickly.
- **Snapshot + Destroy** — cheaper for longer pauses. In the UI:
  Droplet → Snapshots → Take Snapshot, then Destroy. Recreate from the
  snapshot to resume work.

Snapshot cost: approximately $0.06/GB/month × 160 GB = ~$9.60/month, which
is roughly 5× cheaper than leaving the droplet running.
