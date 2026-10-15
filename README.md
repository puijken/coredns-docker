# 🧠 Dynamic Docker DNS with CoreDNS

This project provides a lightweight, automatic DNS service for Docker containers using **CoreDNS** and a **Python event listener**.  
It dynamically maps container hostnames and IP addresses into CoreDNS, supports reverse DNS (PTR) lookups automatically, and allows you to specify for what interfaces you want to create DNS records.

---

## 🚀 Features

✅ **Automatic DNS Record Management**  
- Every container attached to a matched network (see `NETWORK_PREFIX` below) gets a fully-qualified domain name (FQDN) automatically added to `/etc/coredns/hosts`.  
- CoreDNS automatically provides **forward (A)** and **reverse (PTR)** resolution.
- A matched container that's currently stopped still resolves to its **statically assigned** address, if it has one. Docker clears the runtime IP when a container stops but keeps the configured one, so an on-demand container resolves correctly *before* anything starts it. Only an attachment with no static address configured falls back to the `FALLBACK_IP` placeholder.
- Containers with **no** matched network get no entry at all — they don't resolve, rather than resolving to a placeholder.

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
- CoreDNS will not forward requests to upstream. Use this only for automated Docker container resolving.

---

## 🗂️ Project Structure
docker-coredns/

├── Dockerfile # Builds the CoreDNS + Python container

├── Corefile # CoreDNS configuration

├── scripts/update_hosts.py # Watches Docker events and updates hosts file

├── smoke_test.py # Address-selection logic against a fake Docker client

├── smoke_test_live.py # The built image serving real DNS against a real Docker daemon

└── docker-compose.yml # Example compose setup

---

## ⚙️ Configuration

Environment variables:

| Variable | Default | Description | Example |
|-----------|---------|--------------|----------|
| `DOMAIN`  | `docker.local` | DNS suffix for containers | `docker.local` |
| `NETWORK_PREFIX`  | `macvlan` | Comma-separated list of Docker network name prefixes to include | `macvlan, bridge` |
| `FALLBACK_IP`  | `0.0.0.0` | Placeholder IP for a matched container with **no** address at all — neither running nor statically assigned. A stopped container with a static IP uses that instead. | `0.0.0.0` |

---

## 🚀 How It Works

1. On startup, the Python script scans all Docker containers (running and stopped).  
2. For each container connected to a network matching one of the configured `NETWORK_PREFIX` values (e.g. `macvlan*`, `bridge*`), it writes an entry to `/etc/coredns/hosts`. Containers with no matching network are skipped entirely — no entry, no resolution.
3. CoreDNS reads this file using the `hosts` plugin and automatically serves both:
- Forward lookup: `container.docker.local → 192.168.10.100`
- Reverse lookup: `192.168.10.100 → container.docker.local`
4. The script subscribes to Docker events (`start`, `die`, `destroy`, `connect`, `disconnect`) and rewrites the hosts file whenever containers change; CoreDNS itself reloads that file every 2 seconds (see `Corefile`), so propagation is near-instant but not synchronous.

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

---

## 🧪 Tests

Two smoke tests, both stdlib-only:

- `smoke_test.py` runs `update_hosts.py`'s address selection against a fake Docker client:
  runtime address, the static-address fallback for stopped containers, `FALLBACK_IP`, the network
  prefix filter and the hosts-file line format.
- `smoke_test_live.py IMAGE` runs the built image against a real Docker daemon on a throwaway
  network and queries it over DNS: A and PTR records for a container that existed at startup, a
  record for one started later (the event listener), and a stopped container still resolving to
  its static address. It creates `cdsmoke-*` containers and network, uses the subnet
  `172.31.254.0/24`, and removes them afterwards.

```bash
docker build -t docker-coredns:dev .
docker run --rm -v "$PWD/smoke_test.py:/smoke_test.py:ro" docker-coredns:dev python3 /smoke_test.py
python3 smoke_test_live.py docker-coredns:dev
```

Both workflows run them: `pr-validate.yml` on pull requests, and `build.yml` **before** pushing the
image, so a change that breaks the records at runtime never reaches `:latest`. Building alone proves
nothing here, and `coredns/coredns:latest` is a floating tag that can change with no pull request.
