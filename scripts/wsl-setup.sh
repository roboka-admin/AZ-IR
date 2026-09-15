#!/usr/bin/env bash
# Bootstrap the AZ-IR development environment on Linux / WSL2 (Ubuntu 22.04+).
#
#   scripts/wsl-setup.sh --check      # only report what is present and what is missing
#   scripts/wsl-setup.sh              # python venv + backend deps + frontend deps + run tests
#   scripts/wsl-setup.sh --with-apt   # also install missing system packages (uses sudo)
#   scripts/wsl-setup.sh --with-node  # also install Node 22 from NodeSource (uses sudo)
#   scripts/wsl-setup.sh --with-db    # also start PostgreSQL+PostGIS (Docker if present, else apt)
#   scripts/wsl-setup.sh --all        # --with-apt --with-node --with-db
#
# Idempotent: running it twice is safe. It never deletes data and never edits files outside the
# repository (except `git config core.autocrlf`, which is local to this clone).

set -uo pipefail

WITH_APT=0 WITH_NODE=0 WITH_DB=0 CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check)     CHECK_ONLY=1 ;;
    --with-apt)  WITH_APT=1 ;;
    --with-node) WITH_NODE=1 ;;
    --with-db)   WITH_DB=1 ;;
    --all)       WITH_APT=1; WITH_NODE=1; WITH_DB=1 ;;
    -h|--help)   sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

ok()   { printf '  \033[32mok\033[0m      %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mmissing\033[0m %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
run()  { printf '  $ %s\n' "$*"; "$@"; }

HAVE_SUDO=0
command -v sudo >/dev/null 2>&1 && HAVE_SUDO=1
apt_install() {
  if [ "$WITH_APT" -ne 1 ]; then
    warn "would install: $* (re-run with --with-apt to do it)"
    return 1
  fi
  if [ "$HAVE_SUDO" -eq 1 ]; then run sudo apt-get install -y --no-install-recommends "$@"
  else run apt-get install -y --no-install-recommends "$@"; fi
}

# ----------------------------------------------------------------- 1. environment

step "1/6 environment"
if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  ok "WSL detected ($(grep -oiE 'microsoft[a-z0-9]*' /proc/version | head -1))"
  if [ -d /mnt/c ]; then
    case "$ROOT" in
      /mnt/*) bad "the repository is on the Windows filesystem ($ROOT). Move it to the Linux side
          (e.g. ~/AZ-IR): file watching is unreliable there and npm/pip are 5-10x slower." ;;
      *) ok "repository lives on the Linux filesystem ($ROOT)" ;;
    esac
  fi
else
  ok "plain Linux (not WSL) — same steps apply"
fi

PYTHON=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
    case "$version" in
      3.11|3.12|3.13) PYTHON="$candidate"; ok "python $version ($candidate)"; break ;;
      *) warn "python $version found but 3.11+ is required" ;;
    esac
  fi
done
[ -n "$PYTHON" ] || bad "python 3.11+ (Ubuntu 22.04: sudo apt install python3.11 python3.11-venv)"

if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  if [ "$NODE_MAJOR" -ge 20 ]; then ok "node $(node -v)"; else bad "node $(node -v) — 20+ required"; fi
  command -v npm >/dev/null 2>&1 && ok "npm $(npm -v)" || bad "npm"
else
  bad "node 20+ (re-run with --with-node, or: nvm install 22)"
fi

for tool in git make curl; do
  command -v "$tool" >/dev/null 2>&1 && ok "$tool $(command -v "$tool")" || bad "$tool"
done

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then ok "docker is running ($(docker --version))"
  else warn "docker is installed but not reachable — on WSL2 enable Docker Desktop → Settings →
          Resources → WSL integration for this distro, then restart the terminal"; fi
else
  warn "docker not found (optional: only needed for PostGIS via compose)"
fi

# Line endings: a Windows checkout with CRLF breaks scripts/smoke.sh and the Makefile.
if [ -d .git ] && [ "$CHECK_ONLY" -eq 0 ]; then
  current="$(git config --get core.autocrlf || true)"
  if [ "$current" != "input" ] && [ "$current" != "false" ]; then
    run git config core.autocrlf input
    ok "git core.autocrlf=input (LF in the working tree)"
  else
    ok "git core.autocrlf=$current"
  fi
  if git ls-files --eol 2>/dev/null | grep -q 'w/crlf'; then
    warn "some files are checked out with CRLF — run: git rm --cached -r . && git reset --hard"
  fi
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  step "check-only: nothing was installed"
  echo "  next: scripts/wsl-setup.sh            (venv + dependencies + tests)"
  echo "        scripts/wsl-setup.sh --with-apt (also system packages)"
  exit 0
fi

# ----------------------------------------------------------------- 2. system packages

step "2/6 system packages"
missing_pkgs=()
command -v make >/dev/null 2>&1 || missing_pkgs+=(make)
[ -n "$PYTHON" ] || missing_pkgs+=(python3.11 python3.11-venv)
if [ -n "$PYTHON" ] && ! "$PYTHON" -c 'import venv' 2>/dev/null; then missing_pkgs+=(python3-venv); fi
command -v curl >/dev/null 2>&1 || missing_pkgs+=(curl)
# psycopg2-binary ships wheels, but libpq5 is needed at runtime and libpq-dev helps if it compiles.
ldconfig -p 2>/dev/null | grep -q libpq.so || missing_pkgs+=(libpq5)
if [ ${#missing_pkgs[@]} -eq 0 ]; then
  ok "nothing to install"
elif ! command -v apt-get >/dev/null 2>&1; then
  warn "no apt-get on this system; install manually: ${missing_pkgs[*]}"
else
  apt_install "${missing_pkgs[@]}"
fi

if ! command -v node >/dev/null 2>&1 || [ "${NODE_MAJOR:-0}" -lt 20 ]; then
  if [ "$WITH_NODE" -eq 1 ]; then
    step "installing Node 22 (NodeSource)"
    curl -fsSL https://deb.nodesource.com/setup_22.x | ( [ "$HAVE_SUDO" -eq 1 ] && sudo -E bash - || bash - )
    apt_install nodejs
  else
    warn "Node 20+ missing — re-run with --with-node, or install nvm: https://github.com/nvm-sh/nvm"
  fi
fi

# ----------------------------------------------------------------- 3. backend

step "3/6 backend (python)"
if [ -n "$PYTHON" ]; then
  [ -d .venv ] || run "$PYTHON" -m venv .venv
  run ./.venv/bin/pip install --quiet --upgrade pip
  run ./.venv/bin/pip install --quiet -e "backend[dev]"
  ok "backend installed into .venv"
  echo "  hint: use  source .venv/bin/activate   or  make PYTHON=\$PWD/.venv/bin/python <target>"
else
  bad "skipped: no suitable python"
fi

# ----------------------------------------------------------------- 4. frontend

step "4/6 frontend (node)"
if command -v npm >/dev/null 2>&1; then
  ( cd frontend && run npm ci --no-audit --no-fund )
  ok "node_modules ready"
else
  bad "skipped: npm missing"
fi

# ----------------------------------------------------------------- 5. database (optional)

step "5/6 database (optional)"
if [ "$WITH_DB" -eq 1 ]; then
  if docker info >/dev/null 2>&1; then
    run docker compose up -d db
    echo "  waiting for postgres to accept connections..."
    for _ in $(seq 1 30); do
      docker compose exec -T db pg_isready -U azir -d azir >/dev/null 2>&1 && break
      sleep 1
    done
    ok "PostGIS 16-3.4 on localhost:5432 (azir/azir, database azir)"
    if [ -x .venv/bin/python ]; then
      run make migrate PYTHON="$PWD/.venv/bin/python"
      run make seed PYTHON="$PWD/.venv/bin/python"
      ok "schema migrated and fixture corpus seeded"
    fi
  else
    warn "docker is not reachable; installing PostgreSQL+PostGIS natively instead"
    apt_install postgresql postgresql-contrib postgis postgresql-16-postgis-3 || \
      apt_install postgresql postgis
    if [ "$HAVE_SUDO" -eq 1 ]; then run sudo service postgresql start; else run service postgresql start; fi
    echo "  create the role and database once:"
    echo "    sudo -u postgres psql -c \"CREATE USER azir WITH PASSWORD 'azir' CREATEDB;\""
    echo "    sudo -u postgres createdb -O azir azir"
    echo "    sudo -u postgres psql -d azir -c 'CREATE EXTENSION IF NOT EXISTS postgis;'"
  fi
else
  warn "skipped (fixtures driver needs no database). Re-run with --with-db for PostGIS."
fi

# ----------------------------------------------------------------- 6. verify

step "6/6 verify"
if [ -x .venv/bin/python ]; then
  run ./.venv/bin/python -m pytest backend/tests -p no:warnings 2>&1 | tail -2
  run ./.venv/bin/python -m azir.cli --human-logs doctor >/dev/null && ok "azir doctor: healthy (fixtures driver)"
fi
if command -v npm >/dev/null 2>&1; then
  ( cd frontend && run npm run typecheck ) && ok "frontend types clean"
fi

step "done — run the atlas"
cat <<'NEXT'
  Terminal 1:  make dev-backend        # API  → http://localhost:8000/docs
  Terminal 2:  make dev-web            # web  → http://localhost:3000
  Optional:    make tiles              # PMTiles archives (fa+en) → the map serves tiles
  Check:       make check              # lint + types + 400+ tests
               make smoke              # 44 end-to-end contract checks against the running API

  With PostGIS (after --with-db, or `make db-up migrate seed`):
               AZIR_DB_DRIVER=postgis make dev-backend   # the Makefile passes AZIR_DB_URL
               AZIR_DB_DRIVER=postgis make tiles         # archives built from the real database
               make dev-users                           # the four logins (AZIR_DEV_PASSWORD)
               make db-test && make test-postgis        # contract suite against both drivers
NEXT
