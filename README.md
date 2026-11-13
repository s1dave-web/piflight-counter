# piFlight counter

Counts how many [piFlight](https://github.com/s1dave-web/piFlight-v2) LED flight displays are online.
Only devices whose owner said yes check in, once a day:

    POST /checkin   {"id": "<random 32 hex>", "version": "0.5.4", "style": "modern", "planes": 1234}

The reply `{"online": n, "planes": n}` is shown in each device's Settings. `/` is a one-number page, and
`/stats` gives JSON: online in the last 48 hours, this week and this month, total planes, versions and display styles.

## Privacy

Each device sends only:
- a random ID made on the device;
- its piFlight version and display style;
- if its owner agreed, how many planes it has shown in total.

The server keeps:
- a **salted hash** of the ID, never the ID itself;
- the first and last check-in times;
- the version, the style and the plane count.

Devices not seen for 400 days are deleted.

**No IP addresses are stored or logged.** The counter has no access log. It uses IPs only in memory, to stop one
address inventing more than 20 new devices an hour. Behind Cloudflare it reads `CF-Connecting-IP` for that; Cloudflare's own logging is separate.

This is all of the code: [`counter.py`](counter.py), Python standard library plus SQLite.

## Run it

The image is `ghcr.io/s1dave-web/piflight-counter:latest`, built for amd64 and arm64 by GitHub Actions
from this repo. Use [`docker-compose.yml`](docker-compose.yml), either:
- in Portainer: *Stacks → Add stack → Web editor*, then paste it;
- or with `docker compose up -d`.

Then point your reverse proxy at `127.0.0.1:8085`, with HTTPS and access logging off. For nginx, that's `access_log off;`.

- **Update:** in Portainer, *Pull and redeploy*; or `docker compose pull && docker compose up -d`.
- **Back up** the `counter-data` volume (`counter.db` and `salt`). Keep `salt`: without it every device would count as new.
- **Settings (environment):** `COUNTER_DATA` (default `/data`) and `COUNTER_PORT` (default `8080`).
