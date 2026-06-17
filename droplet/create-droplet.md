# Creating the DigitalOcean Droplet

Step-by-step. If anything looks different because DO updated their UI, the core settings stay the same.

## Option A — Using the Web UI (recommended for first time)

1. Sign in at https://cloud.digitalocean.com.
2. Top right: **Create → Droplets**.
3. **Choose Region**: Singapore (SGP1) or Bangalore (BLR1) for lowest latency from South Asia. Any region works.
4. **Choose an image**: Ubuntu 24.04 (LTS) x64.
5. **Choose Size**:
   - Droplet Type: **Basic**
   - CPU options: **Premium Intel** (fastest clock, best for a compute-bound experiment)
   - Plan: **2 vCPU / 4 GB RAM / 80 GB SSD** — about $24/month ($0.036/hour).
6. **Authentication**: SSH Key → select the key you added (`mac-laptop`).
7. **Finalize Details**:
   - Quantity: 1
   - Hostname: `k3s-research`
   - Tags: `research`, `hpa-study` (helpful for filtering bills later)
8. Click **Create Droplet**. Wait ~60 seconds.
9. The Droplet's public IPv4 appears in the list. Copy it — e.g., `159.223.xx.xx`.

## Option B — Using `doctl` CLI (once you're comfortable)

```bash
# Install doctl
brew install doctl

# Authenticate with a Personal Access Token from DO UI
doctl auth init

# List SSH keys (to get the ID of yours)
doctl compute ssh-key list

# Create the Droplet (replace SSH_KEY_ID)
doctl compute droplet create k3s-research \
  --region sgp1 \
  --image ubuntu-24-04-x64 \
  --size s-2vcpu-4gb-intel \
  --ssh-keys SSH_KEY_ID \
  --tag-names research,hpa-study \
  --wait

# Get the IP
doctl compute droplet list k3s-research
```

## Log in

```bash
ssh root@<IP>
# Type 'yes' on first connect to accept fingerprint
```

Inside the Droplet, you should see Ubuntu's welcome MOTD. Run:

```bash
uname -a
free -h
df -h
```

Confirm: 4GB RAM visible, ~75GB disk free.

## One-time hardening (optional but recommended)

```bash
# Update the system
apt update && apt upgrade -y

# Create a non-root user (use your name or 'researcher')
adduser researcher
usermod -aG sudo researcher

# Copy your SSH key to the new user
rsync --archive --chown=researcher:researcher ~/.ssh /home/researcher

# Basic firewall
ufw allow OpenSSH
ufw allow 30080/tcp      # app NodePort (for load tests)
ufw allow 3000/tcp       # Grafana port-forward
ufw --force enable
ufw status
```

From now on you can SSH as the new user: `ssh researcher@<IP>`.

## When you're done experimenting for the day

Either:
- **Power Off** (still billed for storage) — quick to restart.
- **Snapshot + Destroy** (cheap long-term). In UI: Droplet → Snapshots → Take Snapshot. Then Destroy. Recreate from snapshot when you come back.

Snapshot cost: ~$0.06/GB/month × ~80GB = ~$4.80/month. That's 5x cheaper than keeping it running.
