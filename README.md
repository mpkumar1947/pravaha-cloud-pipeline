# Pravaha

> **Disclaimer:** This repository is for **educational purposes only**. It serves as a personal exercise in systems architecture, container orchestration, and cloud storage management. The configurations and code provided here are templates to demonstrate how one might handle API rate limits, dynamic resource allocation, and automated recovery on a Linux system. It does not condone or encourage copyright infringement.

**Pravaha** (Sanskrit: प्रवाह, meaning "flow") is a self-hosted, cloud-backed media automation pipeline. It demonstrates how to orchestrate a suite of containerized services on a resource-constrained Virtual Private Server (VPS) while leveraging cloud storage to create a practically infinite backend.

Built during Summer 2026, this project focuses on **resiliency, self-healing, and dynamic resource management**.

---

## 🏗️ The Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        REQUEST PORTAL (:5055)                       │
│                        (User facing UI)                             │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ request
              ┌────────────┴────────────┐
              ▼                         ▼
      TV/ANIME MANAGER            MOVIE MANAGER
      (API Port: 8989)            (API Port: 7878)
              │                         │
              └────────────┬────────────┘
                           │ search via
                           ▼
                    INDEXER GATEWAY (:9696)
                           │
                           │ grab payload
                           ▼
                    DOWNLOAD CLIENT (:8080)
                    Downloads to MergerFS pool
                           │
                    ┌──────┴──────┐
                    ▼             ▼
            Import back      Smart Queue
            to Manager       Daemon (v5)
                    │        stall detection
                    │        blocklist nuking
                    ▼        dynamic slots
              /mnt/vault
         (rclone FUSE mount)
                    │
                    ▼
    ┌───────────────┴───────────────┐
    │     RCLONE VFS CACHE          │
    │   /var/cache/rclone (root)    │
    │   writes → encrypts → uploads │
    └───────────────┬───────────────┘
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
   Cloud Account 1:      Cloud Account 2:
   (Encrypted Remote)    (Encrypted Remote)
         │                     │
         └──────────┬──────────┘
                    ▼
              Union Remote:
         (unified namespace)
                    │
                    ▼
              MEDIA SERVER (:8096)
              Stream anywhere
```

---

## 🛠️ The Tech Stack

| Component | Purpose |
|---|---|
| **Docker Compose** | Orchestrates 7 interconnected services |
| **MergerFS** | Pools multiple physical block storage volumes into one logical staging drive |
| **rclone** | Manages encrypted, dual-account cloud storage with a VFS write-back cache |
| **systemd** | Manages the custom Python daemons and rclone mount lifecycle |
| **Python 3** | Powers the intelligent queue-management and failsafe daemon |

---

## 🧠 Key Engineering Challenges & Solutions

### 1. The Cloud API Rate Limit Wall (Storage Strategy)
Cloud providers often impose strict daily upload limits (e.g., 750GB/day). 
* **The Solution:** I engineered a dual-account encrypted cloud storage layer using `rclone`. By wrapping two separate cloud accounts in `crypt` remotes and combining them using a `union` filesystem with a "Most Free Space" (`mfs`) create policy, the system naturally load-balances uploads, effectively doubling the daily throughput and providing automatic failover.

### 2. Cross-Filesystem I/O Contention
Initially, the download client and the cloud upload cache were competing for the same disk I/O on the pooled storage, causing massive stalls.
* **The Solution:** I isolated the `rclone` VFS write-back cache to the root NVMe drive (`/var/cache/rclone`), while keeping the active downloads on the MergerFS block storage pool. This eliminated the I/O contention bug and allowed max-speed parallel operations.

### 3. The "Infinite Re-grab" Loop
Automated media managers often get stuck in a loop: a dead file stalls, the manager deletes it, and then re-downloads the *exact same* dead file.
* **The Solution:** I built a custom **Python health-monitoring daemon** (`qbt-smart-queue.py`). It detects stalled jobs, reaches into the manager's REST API, and performs an idempotent cleanup using `blocklist` semantics. This ensures the manager learns that the specific file is dead and searches for an alternative, breaking the loop.

### 4. Dynamic Resource Throttling & Failsafes
Resource-constrained VPS environments (4GB RAM, limited disk) easily lock up under heavy queues.
* **The Solution:** The Python daemon continuously monitors real-time disk telemetry. It dynamically throttles the download client's concurrency (adjusting active slots) based on available bytes. Furthermore, if the root cache disk nears capacity (due to hitting cloud upload limits), a disk-space failsafe automatically pauses all I/O and resumes only when space recovers. **Zero manual intervention required.**

---

## 📁 Repository Structure

```
.
├── docker-compose.yml              # The core orchestration file
├── config/
│   └── rclone/rclone.conf.example  # Template for the dual-cloud union setup
├── scripts/
│   ├── qbt-smart-queue.py          # The Python health-monitoring daemon (v5)
│   └── pravaha-upload.sh           # Manual rclone push helper script
└── systemd/
    ├── rclone-media.service        # Service to maintain the FUSE mount
    ├── qbt-smart-queue.service     # Daemon execution service
    └── qbt-smart-queue.timer       # 1-minute interval timer for the daemon
```

## 🚀 Setup & Usage (Template)

1. Clone this repository to your target server.
2. Review `docker-compose.yml` and adjust volume mounts (e.g., `/mnt/torrents`, `/mnt/vault`) to match your filesystem.
3. Configure `rclone`: Copy `config/rclone/rclone.conf.example` to your rclone config directory and fill in your cloud provider credentials.
4. Set up the `systemd` services to ensure the rclone FUSE mount and the Python daemon start on boot.
5. Update the API keys in `scripts/qbt-smart-queue.py` to match your local services.

> **Note:** All API keys and personal credentials have been scrubbed from this repository. You must supply your own configurations.

---
*Built as a summer exploration into Linux systems administration and automation.*
