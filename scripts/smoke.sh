#!/usr/bin/env bash
# End-to-end smoke test against a running API.
#
#   scripts/smoke.sh                          # http://127.0.0.1:8000
#   AZIR_API=https://staging.example scripts/smoke.sh
#
# It asserts the *contract*, not the data: shapes, byte budget, problem details, temporal filtering,
# search in both scripts and the read-only guarantee. Exits non-zero on any failure, so CI and a
# human get the same answer. Requires curl and python3 only.

set -uo pipefail

API="${AZIR_API:-http://127.0.0.1:8000}"
PREFIX="${AZIR_API_PREFIX:-/api/v1}"
FAILURES=0
CHECKS=0

pass() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

# assert_py <name> <python expression over: data, status, ctype, size, raw>
assert_py() {
  local name="$1" expression="$2"
  CHECKS=$((CHECKS + 1))
  if printf '%s' "$RAW" | AZIR_STATUS="$STATUS" AZIR_CTYPE="$CTYPE" python3 -c "
import json, os, sys
raw = sys.stdin.read()
status = int(os.environ['AZIR_STATUS'])
ctype = os.environ['AZIR_CTYPE']
size = len(raw.encode('utf-8'))
try:
    data = json.loads(raw) if raw else None
except json.JSONDecodeError:
    data = None
sys.exit(0 if ($expression) else 1)
"; then
    pass "$name"
  else
    fail "$name  [status=$STATUS ctype=$CTYPE bytes=${#RAW}]"
  fi
}

# get <name> <path> <expression>
get() {
  local name="$1" path="$2" expression="$3" head_file body_file
  head_file="$(mktemp)"; body_file="$(mktemp)"
  STATUS="$(curl -sS -m 30 -D "$head_file" -o "$body_file" -w '%{http_code}' "$API$path" 2>/dev/null)" || {
    fail "$name (request to $API$path failed)"; rm -f "$head_file" "$body_file"; return
  }
  CTYPE="$(awk 'tolower($1)=="content-type:"{print tolower($2)}' "$head_file" | tr -d '\r;' | head -1)"
  RAW="$(cat "$body_file")"
  rm -f "$head_file" "$body_file"
  assert_py "$name" "$expression"
}

# body <name> <path>   → fetch and keep RAW/STATUS/CTYPE for a follow-up comparison
body() {
  local name="$1" path="$2" head_file body_file
  head_file="$(mktemp)"; body_file="$(mktemp)"
  STATUS="$(curl -sS -m 30 -D "$head_file" -o "$body_file" -w '%{http_code}' "$API$path" 2>/dev/null)"
  CTYPE="$(awk 'tolower($1)=="content-type:"{print tolower($2)}' "$head_file" | tr -d '\r;' | head -1)"
  RAW="$(cat "$body_file")"
  rm -f "$head_file" "$body_file"
}

echo "AZ-IR smoke test → $API$PREFIX"

echo "liveness"
get "healthz" "/healthz" "status == 200"
get "readyz" "/readyz" "status == 200"

echo "bootstrap"
get "meta exposes zoom levels, timeline, layers and the driver" "$PREFIX/meta" "
status == 200
and data.get('zoom_levels') and data.get('layers')
and data.get('timeline', {}).get('buckets')
and len(data.get('study_area', {}).get('bbox', [])) == 4
and data.get('driver') in ('fixtures', 'postgis')
"
get "layers come from the API, never from the frontend" "$PREFIX/atlas/layers" "
status == 200 and any(layer.get('id') for layer in data.get('data', []))
"

echo "map"
get "features are GeoJSON carrying certainty and rank" "$PREFIX/atlas/features?zoom=7&t=1510" "
status == 200 and 'geo+json' in ctype
and data.get('type') == 'FeatureCollection' and data.get('features')
and all('certainty' in f['properties'] and 'rank' in f['properties'] for f in data['features'])
"
get "a full-field payload respects the byte budget" "$PREFIX/atlas/features?zoom=6&fields=full" "
status == 200 and size <= 512 * 1024
"
get "timeline buckets are contiguous" "$PREFIX/atlas/timeline?from=1400&to=1600" "
status == 200 and len(data.get('data', [])) > 1
and all(b['to'] + 1 == data['data'][i + 1]['from'] for i, b in enumerate(data['data'][:-1]))
"
get "context answers 'what was here'" "$PREFIX/atlas/context?place_id=plc_ardabil&radius_km=50" "
status == 200 and isinstance(data.get('data'), list)
"

# Temporal filtering is the product's core promise: prove it changes the answer.
body "" "$PREFIX/atlas/features?zoom=6.4&t=1400&l=political_entities"
BEFORE="$RAW"
body "" "$PREFIX/atlas/features?zoom=6.4&t=1510&l=political_entities"
AFTER="$RAW"
CHECKS=$((CHECKS + 1))
if BEFORE="$BEFORE" AFTER="$AFTER" python3 -c "
import json, os, sys
def ids(payload):
    return {f['id'] for f in json.loads(payload).get('features', [])}
before, after = ids(os.environ['BEFORE']), ids(os.environ['AFTER'])
sys.exit(0 if (after and before != after and 'pol_safavid' not in before and 'pol_safavid' in after) else 1)
"; then
  pass "the map at 1400 differs from 1510 (Safavids appear only after they exist)"
else
  fail "temporal filtering did not change the political-entity set"
fi

echo "entities, articles, sources"
get "a place resolves by slug with names and relationships" "$PREFIX/entities/place/tabriz" "
status == 200 and data.get('id') == 'plc_tabriz'
and data.get('names', {}).get('display')
and isinstance(data.get('relationships'), list) and isinstance(data.get('sources'), list)
"
get "disagreements survive with every position" "$PREFIX/entities/political_entity/safavid-dynasty" "
status == 200
and all(group.get('positions') for group in data.get('disagreements', []))
"
get "an article links back to entities and carries a body" "$PREFIX/articles/rise-of-the-safavids" "
status == 200
and (data.get('article') or {}).get('body_md')
and (data.get('article') or {}).get('entities')
and (data.get('article') or {}).get('map_state', {}).get('center')
"
get "sources carry reliability" "$PREFIX/sources" "
status == 200 and data.get('data') and all(s.get('reliability') for s in data['data'])
"
get "search finds Tabriz in Persian" "$PREFIX/search?q=%D8%AA%D8%A8%D8%B1%DB%8C%D8%B2" "
status == 200 and any(hit.get('id') == 'plc_tabriz' for hit in data.get('data', []))
"
get "search finds Tabriz in English" "$PREFIX/search?q=Tabriz" "
status == 200 and any(hit.get('id') == 'plc_tabriz' for hit in data.get('data', []))
"
get "search without the half-space still matches" "$PREFIX/search?q=%D8%B5%D9%81%DB%8C%D8%A7%D9%84%D8%AF%DB%8C%D9%86" "
status == 200 and any(hit.get('id') == 'prs_sheikh_safi' for hit in data.get('data', []))
"

echo "error contract"
get "an unknown entity is 404 problem+json" "$PREFIX/entities/place/does-not-exist" "
status == 404 and 'problem+json' in ctype
and str(data.get('type', '')).startswith('http') and data.get('status') == 404
"
get "a malformed bbox is a validation problem, never a 500" "$PREFIX/atlas/features?bbox=not,a,bbox" "
status == 422 and 'problem+json' in ctype and str(data.get('type', '')).endswith('validation')
"
get "an empty search query is rejected" "$PREFIX/search?q=" "
status == 422 and 'problem+json' in ctype
"

# The API is read-only in Phase 1: writes must be refused, not silently accepted.
STATUS="$(curl -sS -m 10 -o /dev/null -w '%{http_code}' -X POST "$API$PREFIX/meta")"
CTYPE=""; RAW=""
assert_py "POST is refused (read-only API)" "status in (405, 403)"

printf '\n%d checks, %d failures\n' "$CHECKS" "$FAILURES"
test "$FAILURES" -eq 0
