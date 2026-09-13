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
# The editorial write path signs in with a development identity (ADR-0017). Override both when
# smoke-testing a staging deployment that has real accounts.
SMOKE_EDITOR_EMAIL="${AZIR_SMOKE_EMAIL:-admin@atlas.local}"
SMOKE_EDITOR_PASSWORD="${AZIR_DEV_PASSWORD:-atlas-dev-password}"
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

# The public corpus is read-only: writes to it must be refused, not silently accepted.
STATUS="$(curl -sS -m 10 -o /dev/null -w '%{http_code}' -X POST "$API$PREFIX/meta")"
CTYPE=""; RAW=""
assert_py "POST to the public API is refused" "status in (405, 403)"

# ------------------------------------------------------------------ editorial write path
#
# The whole loop over HTTP: session cookie, CSRF, roles, the lint gate, publication, audit.
# Skipped when the panel is off -- which is the correct state for a production URL (ADR-0017).
# Accounts come from `azir user sync-dev`; the password from $AZIR_DEV_PASSWORD.

req() {  # req <name> <method> <path> [json] [--no-csrf]
  local name="$1" method="$2" path="$3" json="${4:-}" flags="${5:-}"
  local head_file body_file args=(-sS -m 30 -b "$JAR" -c "$JAR" -X "$method")
  head_file="$(mktemp)"; body_file="$(mktemp)"
  [[ -n "$json" ]] && args+=(-H 'Content-Type: application/json' -d "$json")
  [[ "$flags" != "--no-csrf" ]] && args+=(-H "$CSRF_HEADER: $CSRF")
  STATUS="$(curl "${args[@]}" -D "$head_file" -o "$body_file" -w '%{http_code}' "$API$path" 2>/dev/null)" || {
    fail "$name (request to $API$path failed)"; rm -f "$head_file" "$body_file"; return
  }
  CTYPE="$(awk 'tolower($1)=="content-type:"{print tolower($2)}' "$head_file" | tr -d '\r;' | head -1)"
  RAW="$(cat "$body_file")"
  rm -f "$head_file" "$body_file"
  assert_py "$name" "$EXPRESSION"
}

echo "editorial write path"
JAR="$(mktemp)"
LOGIN_STATUS="$(curl -sS -m 20 -c "$JAR" -o /dev/null -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$SMOKE_EDITOR_EMAIL\",\"password\":\"$SMOKE_EDITOR_PASSWORD\"}" \
  "$API$PREFIX/auth/login")"
if [[ "$LOGIN_STATUS" != "200" ]]; then
  printf '  \033[33mskip\033[0m editorial write path (login returned %s: panel off or no dev users)\n' "$LOGIN_STATUS"
else
  CSRF="$(awk '$6=="azir_csrf"{print $7}' "$JAR")"
  CSRF_HEADER="${AZIR_CSRF_HEADER:-X-CSRF-Token}"
  SLUG="smoke-$(date +%s)"

  EXPRESSION="status == 403"
  req "a write without a CSRF token is refused" POST "$PREFIX/editorial/place" \
    "{\"slug\":\"$SLUG\",\"names\":[{\"form\":\"دودکش\",\"lang\":\"fa\"}]}" --no-csrf

  EXPRESSION="status == 201 and data['data']['status'] == 'draft' and data['data']['slug'] == '$SLUG'"
  req "an editor can create a draft" POST "$PREFIX/editorial/place" "{
    \"kind\": \"city\", \"slug\": \"$SLUG\",
    \"names\": [{\"form\": \"شهر دودکش\", \"lang\": \"fa\", \"kind\": \"preferred\"},
                {\"form\": \"Smoke Town\", \"lang\": \"en\", \"kind\": \"preferred\"}],
    \"temporal\": {\"from\": 1600, \"to\": 1700, \"precision\": \"range\", \"display\": \"۱۶۰۰–۱۷۰۰ م\"},
    \"geometries\": [{\"geojson\": {\"type\": \"Point\", \"coordinates\": [48.35, 38.25]},
                      \"kind\": \"point\", \"certainty\": \"uncertain\"}],
    \"summary\": \"رکوردی که smoke test می‌سازد.\",
    \"source_ids\": [\"src_tabari_tarikh\"]
  }"
  CREATED_ID="$(printf '%s' "$RAW" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])' 2>/dev/null || echo "")"

  EXPRESSION="status == 409 and data.get('status') == 'draft'"
  req "a draft cannot skip review" POST "$PREFIX/editorial/place/$CREATED_ID/transition" \
    "{\"action\": \"approve\"}"

  # A second record with no source: the lint gate must stop it, and say which rule.
  EXPRESSION="status == 201"
  req "a sourceless draft can be created" POST "$PREFIX/editorial/place" "{
    \"kind\": \"village\", \"slug\": \"$SLUG-nosource\",
    \"names\": [{\"form\": \"روستای بی‌منبع\", \"lang\": \"fa\", \"kind\": \"preferred\"}]
  }"
  BARE_ID="$(printf '%s' "$RAW" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])' 2>/dev/null || echo "")"
  EXPRESSION="status == 200"
  req "it can be submitted for review" POST "$PREFIX/editorial/place/$BARE_ID/transition" \
    "{\"action\": \"submit\"}"
  EXPRESSION="status == 422 and 'D4' in str(data.get('rules')) and data.get('findings')"
  req "publishing it is blocked by lint rule D4" POST "$PREFIX/editorial/place/$BARE_ID/transition" \
    "{\"action\": \"approve\"}"

  EXPRESSION="status == 200 and data['data']['status'] == 'in_review'"
  req "submit moves it into review" POST "$PREFIX/editorial/place/$CREATED_ID/transition" \
    "{\"action\": \"submit\"}"

  EXPRESSION="status == 200 and data['data']['status'] == 'published' and data['data']['revision'] == 1"
  req "approve publishes it" POST "$PREFIX/editorial/place/$CREATED_ID/transition" \
    "{\"action\": \"approve\"}"

  # Publishing a live record forks it: the public version must not move while the copy is edited.
  EXPRESSION="status == 200 and data['meta']['forked'] is True and data['data']['id'] != '$CREATED_ID'"
  req "editing a published record forks a reviewed copy" PATCH "$PREFIX/editorial/place/$CREATED_ID" \
    "{\"summary\": \"ویرایشِ بعد از انتشار.\"}"
  COPY_ID="$(printf '%s' "$RAW" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])' 2>/dev/null || echo "")"
  EXPRESSION="status == 200 and data['summary'] != 'ویرایشِ بعد از انتشار.'"
  req "the public version is untouched by the fork" GET "$PREFIX/entities/place/$CREATED_ID"
  EXPRESSION="status == 200 and data['data']['pending_revision'] == '$COPY_ID'"
  req "the panel shows the open revision" GET "$PREFIX/editorial/place/$CREATED_ID/state"

  EXPRESSION="status == 200 and data.get('slug') == '$SLUG' and data.get('status') == 'published'"
  req "the published record is now public" GET "$PREFIX/entities/place/$CREATED_ID"

  EXPRESSION="status == 200 and {e.get('action') for e in data.get('data', [])} >= {'create','submit','approve'}"
  req "every step is in the audit trail" GET "$PREFIX/editorial/audit?entity_id=$CREATED_ID"

  EXPRESSION="status == 200 and data.get('data', {}).get('actor', {}).get('role')"
  req "the session says who is signed in" GET "$PREFIX/auth/me"
fi
rm -f "$JAR"

printf '\n%d checks, %d failures\n' "$CHECKS" "$FAILURES"
test "$FAILURES" -eq 0
