---
description: Build and deploy HomeDeck to the Raspberry Pi over SSH
argument-hint: "[commit message] (omit if HEAD is already pushed)"
---

Deploy the current work to the Raspberry Pi running HomeDeck.

The pi runs the image CI publishes: `.github/workflows/docker-publish.yml` rebuilds
`ghcr.io/jserrats/homedeck:latest` on every push to `main`, and the pi's
`docker-compose.yml` pulls that tag. So deploying means: push to `main`, wait for the
build, pull on the pi. Never build on the pi — CI's multi-arch build is the source of
truth for what runs there.

## Facts about the target

| | |
|---|---|
| SSH host | `pi` → `pi-we-071.iot` (`~/.jserrats/ssh/common.conf`) |
| Deploy dir | `/home/jserrats/homedeck` |
| Image | `ghcr.io/jserrats/homedeck:latest` |
| Container | `homedeck` |

**Docker on the pi needs `sudo -n`** — the `jserrats` user is not in the `docker` group,
so a bare `docker compose` fails with "permission denied … /var/run/docker.sock". `sudo -n`
(no password prompt) works, so use it for every docker call.

**`pi-we-071.iot` only resolves on the home IoT network.** `Could not resolve hostname
pi-we-071.iot` means this Mac is off that network (VPN, tethering, elsewhere) — not that the
pi is down. Stop and say so; do not go looking for another address. Steps 1-3 are still worth
doing, since pushing is what queues the build.

**`timeout` does not exist on this Mac.** Use the Bash tool's own `timeout` parameter, or
`run_in_background`, instead of wrapping commands in `timeout`.

## Steps

1. **Check the tree.** `git status --short` and `git log --oneline -1`. Run
   `.venv/bin/python -m pytest -q` — do not deploy a failing suite; stop and report instead.

2. **Commit and push.** If there are uncommitted changes, commit them (use `$ARGUMENTS` as
   the message when given, otherwise write one from the diff) and `git push origin main`.
   If the tree is already clean, confirm `HEAD` is on `origin/main`
   (`git rev-parse HEAD origin/main`) and skip ahead. Note the deployed SHA — every later
   check compares against it.

3. **Wait for the build.** `gh run list --limit 3 --json databaseId,status,conclusion,headSha`
   to find the run for that SHA, then watch it in the **background** (it takes ~5 min; QEMU
   builds arm64 and arm/v7 alongside amd64):

   ```
   gh run watch <id> --exit-status --interval 30
   ```

   `--exit-status` makes a failed build a non-zero exit. If the build fails, stop — do not
   deploy, and report the failing step.

4. **Pull and restart** (takes a couple of minutes on the pi's connection):

   ```
   ssh pi 'cd /home/jserrats/homedeck && sudo -n docker compose pull && sudo -n docker compose up -d'
   ```

5. **Verify the new image is actually what's running.** A successful `up -d` does not prove
   the pull landed the new build, so check the image's own revision label against the SHA
   you deployed:

   ```
   ssh pi 'sudo -n docker inspect --format "{{index .Config.Labels \"org.opencontainers.image.revision\"}} running={{.State.Running}} restarts={{.RestartCount}}" homedeck'
   ssh pi 'cd /home/jserrats/homedeck && sudo -n docker compose logs --tail 25'
   ```

   Expect the label to equal the deployed SHA, `running=true`, `restarts=0`, and empty logs
   (the container runs without `-v`, so it only logs warnings and errors).

6. **Confirm it stayed up.** Re-check `restarts` after ~90s — a crash-loop looks healthy for
   the first few seconds. Use a background `until` loop rather than a foreground sleep.

If the pi's revision label already equals `HEAD` before step 4, there is nothing to deploy —
say so instead of pulling.

Report the deployed SHA, the CI run conclusion, and the final container state. If any step
fails, say which one and leave the pi as it is — the old container keeps running until a
pull succeeds, so a failed deploy is not an outage.
