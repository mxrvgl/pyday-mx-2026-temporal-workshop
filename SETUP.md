# Pyday Mexico 2026 - Temporal Workshop Setup

In the workshop you'll run a small shop on your laptop and move its checkout onto [Temporal](https://temporal.io). Everything runs in containers, so you won't install Python, Postgres or Temporal yourself. You only need the tools below, installed and working, before the day.

It takes about 15 minutes. Most of that is downloads, so do it on a good connection (conference wifi is not one).

## 1. Install

| Tool | Version | How to get it |
| --- | --- | --- |
| **Docker** | A recent release (2024 or later) | [Docker Desktop](https://docs.docker.com/get-docker/) on macOS and Windows; Docker Engine on Linux |
| **Docker Compose**, the v2 plugin (`docker compose`, not the old `docker-compose`) | **2.24.4 or later**: the workshop uses features older versions reject | Included in Docker Desktop. On Linux: the `docker-compose-plugin` package |
| **make** | GNU Make 3.81 or later | macOS: `xcode-select --install`. It installs Apple's Command Line Tools, a few hundred MB rather than all of Xcode. Debian or Ubuntu: `sudo apt install make`. Fedora: `sudo dnf install make` |
| **git** | Any recent version | Included in the macOS Command Line Tools above; `sudo apt install git` or `sudo dnf install git` on Linux |
| **Windows only:** [WSL 2](https://learn.microsoft.com/windows/wsl/install) | | Turn on Docker Desktop's WSL integration for your distribution, then run every command in this guide inside your WSL terminal |

**Also bring:**
- A terminal that can show two panes side by side.
- A browser.
- An editor with Python highlighting.

**Not needed:** Python, the Temporal CLI, or a Temporal Cloud account.

## 2. Check it works

Run each command. You should see roughly what's on the right.

| Command | Expect |
| --- | --- |
| `docker run --rm hello-world` | `Hello from Docker!` (this proves the daemon is running, not just installed) |
| `docker compose version` | `Docker Compose version v2.24.4` or later |
| `make --version` | `GNU Make 3.81` or later |
| `git --version` | Any version |

Then check your resources:

- **Memory:** Docker needs 4 GB or more. `docker info --format '{{.MemTotal}}'` should print 4000000000 or more. If not, raise it in Docker Desktop → Settings → Resources.
- **Disk:** about 3 GB free. The images take about 1.5 GB.
- **Ports:** 8000, 8001, 8002, 5432, 7233 and 8233 must be free on the day. A local Postgres, or a `temporal server start-dev` you left running, will clash, so stop them before the workshop.

Both Intel/AMD and Apple Silicon/ARM machines work.

## 3. Save time on the day (optional)

Download the images the workshop uses now, so you don't have to on the conference wifi:

```sh
docker pull postgres:17-alpine
docker pull python:3.13-slim
docker pull temporalio/temporal:1.9.1
```

## 4. For Part 2 only: an Anthropic API key

Part 2 of the workshop, a support agent built on Claude, needs your own API key. Part 1 doesn't. Create one at [console.anthropic.com](https://console.anthropic.com); Part 2 uses Claude Haiku and costs a few cents. Check it works:

```sh
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
  -d '{"model": "claude-haiku-5-5", "max_tokens": 16, "messages": [{"role": "user", "content": "Say ok."}]}'
```

With `ANTHROPIC_API_KEY` set in your shell, it should print a JSON answer with `"type":"message"`, not an `"error"`.

## 5. Get the workshop

Clone the repository, then follow **Before the session** in its README:

```sh
git clone https://github.com/mxrvgl/pyday-mx-2026-temporal-workshop.git
cd pyday-mx-2026-temporal-workshop
make preflight   # checks Docker, Compose, memory and ports
make setup       # downloads and builds everything (this covers step 3 too)
```

For Part 2, put your API key in `.env` there: `cp .env.example .env`, then set `ANTHROPIC_API_KEY`.

## Or let your coding agent check for you

Paste this into Claude Code, Cursor, or whichever coding agent you use:

```text
I'm attending a hands-on Temporal workshop that runs everything in Docker.
Check that my machine is ready for it. Don't install, upgrade or change anything
without asking me first.

1. Docker is installed and the daemon is running: `docker run --rm hello-world`
   succeeds.
2. `docker compose version` reports 2.24.4 or later. It must be the Compose v2
   plugin, not the legacy `docker-compose` v1 binary.
3. `make` is available (GNU Make 3.81 or later), and so is `git`. If one is
   missing, tell me how to install it on my OS (on macOS: `xcode-select --install`)
   and offer to do it.
4. Docker has at least 4 GB of memory, and there are at least 3 GB of free disk.
5. Ports 8000, 8001, 8002, 5432, 7233 and 8233 are free on localhost. If one is
   taken, tell me which process is using it.
6. On Windows: I'm inside a WSL 2 terminal, and Docker Desktop's WSL integration
   is enabled for this distribution.

Then offer to clone https://github.com/mxrvgl/pyday-mx-2026-temporal-workshop
and, inside it, run `make preflight` and `make setup`.

Report each item as pass or fail. For each failure, give me the exact command or
setting that fixes it, for my operating system.
```
