# 🧠 Dynamic Docker DNS with CoreDNS

This project provides a lightweight, automatic DNS service for Docker containers using **CoreDNS** and a **Python event listener**.  
It dynamically maps container hostnames and IP addresses into CoreDNS, supports reverse DNS (PTR) lookups automatically, and allows you to specify for what interfaces you want to create DNS records.

---

## 🚀 Features

✅ **Automatic DNS Record Management**  
- Each Docker container gets a fully-qualified domain name (FQDN) automatically added to `/etc/coredns/hosts`.  
- CoreDNS automatically provides **forward (A)** and **reverse (PTR)** resolution.

✅ **Event-Based Updates**  
- The DNS host file updates **instantly** on Docker events (start, stop, destroy, etc.).  
- No need for polling — it’s lightweight and real-time.

✅ **Multi-Network Support**  
- Supports one or more Docker network prefixes.  
- Example: `NETWORK_PREFIX=macvlan,dmz` will include all containers in any network that starts with `macvlan` or `dmz`.

✅ **Custom Domain**  
- Easily define your internal domain via the `DOMAIN` environment variable.  
- Example: `DOMAIN=docker.local`.
- If no variable is entered the default value 'docker.local' will be used.

✅ **Reverse DNS Support**  
- CoreDNS automatically provides reverse (PTR) lookups for all listed IPs.

✅ **No DNS forwarding**  
- CoreDNS will not forward requests to upstream. Use this only for automatated Docker container resolving.

---

## 🗂️ Project Structure
docker-coredns-dynamic/

├── Dockerfile # Builds the CoreDNS + Python container

├── Corefile # CoreDNS configuration

├── update_hosts.py # Watches Docker events and updates hosts file

└── docker-compose.yml # Example compose setup

---

## ⚙️ Configuration

Environment variables:

| Variable | Description | Example |
|-----------|--------------|----------|
| `DOMAIN`  | Default DNS suffix for containers | `docker.local` |
| `NETWORK_PREFIX`  | Comma-separated list of Docker network name prefixes to include | `macvlan, bridge` |

---

## 🚀 How It Works

1. On startup, the Python script scans all Docker containers.  
2. For each container connected to a `macvlan*,bride*` network, it writes an entry to `/etc/coredns/hosts`
3. CoreDNS reads this file using the `hosts` plugin and automatically serves both:
- Forward lookup: `container.docker.local → 192.168.10.100`
- Reverse lookup: `192.168.10.100 → container.docker.local`
4. The script subscribes to Docker events (`start`, `die`, `connect`, etc.) and automatically updates the hosts file whenever containers change.

---

## 🔧 Example Query

Forward lookup:
```bash
dig @192.168.10.10 container.docker.local
```
Reverse lookup:
```bash
dig -x 192.168.10.100 @192.168.10.10
```
