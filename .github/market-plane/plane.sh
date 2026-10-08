# market-plane.yml's body (see the workflow header). Secrets are read into
# shell variables, masked line by line, passed to jq through the environment
# and never echoed; the create body is written to RUNNER_TEMP and removed.
set -uo pipefail
KEY="${KEY_SECRET:-}"
[ -z "$KEY" ] && { echo "no RENDER_API_KEY secret"; exit 1; }
echo "::add-mask::$KEY"
API=https://api.render.com/v1
SPEC=ops/render_market_plane_provision.json
NAME=$(jq -r .name "$SPEC")
TMP="${RUNNER_TEMP:-/tmp}/plane.$$"
mkdir -p "$TMP"; chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

get() { curl -sS --max-time 60 -H "Authorization: Bearer $KEY" -H "Accept: application/json" "$API$1"; }
send() {  # method path [body-file] -> HTTP code; response in $TMP/resp.json
  if [ -n "${3:-}" ]; then
    curl -sS -o "$TMP/resp.json" -w '%{http_code}' --max-time 90 -X "$1" \
      -H "Authorization: Bearer $KEY" -H "Accept: application/json" \
      -H "Content-Type: application/json" --data @"$3" "$API$2"
  else
    curl -sS -o "$TMP/resp.json" -w '%{http_code}' --max-time 90 -X "$1" \
      -H "Authorization: Bearer $KEY" -H "Accept: application/json" "$API$2"
  fi
}
svc_id() { get "/services?name=$1&limit=20" | jq -r --arg n "$1" '.[]? | select(.service.name==$n) | .service.id' | head -1; }
need_confirm() { [ "${CONFIRM:-}" = "DO" ] || { echo "refused: '$ACTION' needs confirm=DO"; exit 1; }; }
mask_value() { while IFS= read -r ln; do [ -n "$ln" ] && echo "::add-mask::$ln"; done <<<"$1"; }

guard_check() {  # the names the plane would hold vs market_plane_guard
  python3 -I - "$SPEC" <<'PY'
import json, sys
sys.path.insert(0, "backend")
from sportsassets import market_plane_guard as G
spec = json.load(open(sys.argv[1]))
names = set(spec["env"]) | set(spec["copy_secrets"])
bad = sorted(n for n in names if n in G.FORBIDDEN_ENV)
extra = sorted(set(spec["copy_secrets"]) - set(G.ALLOWED_SECRET_ENV))
print("guard check: forbidden=%s secrets_outside_allowed=%s"
      % (bad, extra))
sys.exit(1 if bad or extra else 0)
PY
}

build_body() {  # -> $TMP/body.json (secret values from the environment)
  SHAPE=$(jq -r .shape_from "$SPEC")
  SHAPE_ID=$(svc_id "$SHAPE"); [ -z "$SHAPE_ID" ] && { echo "no service $SHAPE to take the image shape from"; return 1; }
  get "/services/$SHAPE_ID" > "$TMP/shape.json"
  declare -A IDS=()
  for k in $(jq -r '.copy_secrets | keys[]' "$SPEC"); do
    src=$(jq -r --arg k "$k" '.copy_secrets[$k]' "$SPEC")
    [ -z "${IDS[$src]:-}" ] && IDS[$src]=$(svc_id "$src")
    [ -z "${IDS[$src]}" ] && { echo "no service $src (source of $k)"; return 1; }
    [ -f "$TMP/env_$src.json" ] || get "/services/${IDS[$src]}/env-vars?limit=100" > "$TMP/env_$src.json"
    v=$(jq -r --arg k "$k" '[.[]? | .envVar | select(.key==$k) | .value] | first // empty' "$TMP/env_$src.json")
    [ -z "$v" ] && { echo "$k is not set on $src: nothing to copy"; return 1; }
    mask_value "$v"
    export "SEC_$k=$v"
    echo "  $k <- $src (${#v} chars)"
  done
  jq -n --slurpfile s "$SPEC" --slurpfile w "$TMP/shape.json" '
    $s[0] as $s | $w[0] as $w |
    {type: $s.type, name: $s.name, ownerId: $w.ownerId, repo: $w.repo,
     branch: $s.branch, autoDeploy: $s.autoDeploy, rootDir: ($w.rootDir // ""),
     envVars: ([$s.env | to_entries[] | {key, value}]
               + [$s.copy_secrets | keys[] | {key: ., value: $ENV["SEC_" + .]}]),
     serviceDetails: {runtime: "docker", plan: $s.plan,
       region: $w.serviceDetails.region, numInstances: 1,
       envSpecificDetails: {
         dockerCommand: $s.dockerCommand,
         dockerContext: ($w.serviceDetails.envSpecificDetails.dockerContext // "."),
         dockerfilePath: ($w.serviceDetails.envSpecificDetails.dockerfilePath // "./backend/Dockerfile")}}}' \
    > "$TMP/body.json"
  chmod 600 "$TMP/body.json"
}

redacted() { jq --slurpfile s "$SPEC" '($s[0].copy_secrets | keys) as $sec | .envVars |= map(if (.key | IN($sec[])) then .value = "<\(.value | length) chars>" else . end)' "$TMP/body.json"; }

case "${ACTION:-}" in
  plan)
    guard_check || exit 1
    ID=$(svc_id "$NAME")
    echo "service $NAME: ${ID:-ABSENT}"
    build_body || exit 1
    echo "== create body (secrets as lengths) =="
    redacted
    ;;
  status)
    ID=$(svc_id "$NAME"); [ -z "$ID" ] && { echo "service $NAME: ABSENT"; exit 0; }
    get "/services/$ID" | jq '{id, name, type, branch, autoDeploy, suspended, plan: .serviceDetails.plan, region: .serviceDetails.region, dockerCommand: .serviceDetails.envSpecificDetails.dockerCommand}'
    get "/services/$ID/deploys?limit=5" | jq -r '.[]? | .deploy | "\(.createdAt) -> \(.finishedAt // "(running)")  \(.status)  \(.id)  \(.commit.id // "-")"'
    get "/services/$ID/env-vars?limit=100" | jq -r '"env names: " + ([.[]? | .envVar.key] | sort | join(" "))'
    ;;
  create)
    need_confirm
    guard_check || exit 1
    ID=$(svc_id "$NAME"); [ -n "$ID" ] && { echo "refused: $NAME already exists ($ID); use deploy-commit"; exit 1; }
    build_body || exit 1
    redacted | jq -c '{name, branch, autoDeploy, plan: .serviceDetails.plan, region: .serviceDetails.region, env: [.envVars[] | "\(.key)=\(.value)"]}'
    code=$(send POST /services "$TMP/body.json"); rm -f "$TMP/body.json"
    echo "create -> HTTP $code"
    jq '{id: (.service.id // .id), name: (.service.name // .name), deployId, message}' "$TMP/resp.json" 2>/dev/null || head -c 400 "$TMP/resp.json"
    case "$code" in 200|201) ;; *) exit 1;; esac
    ;;
  deploy-commit)
    need_confirm
    echo "$ARG" | grep -Eq '^[0-9a-f]{40}$' || { echo "refused: arg must be a full 40-hex sha (got '$ARG')"; exit 1; }
    ID=$(svc_id "$NAME"); [ -z "$ID" ] && { echo "service $NAME: ABSENT"; exit 1; }
    jq -n --arg c "$ARG" '{commitId: $c, clearCache: "do_not_clear"}' > "$TMP/deploy.json"
    code=$(send POST "/services/$ID/deploys" "$TMP/deploy.json")
    echo "deploy $ARG -> HTTP $code"
    jq -r '"deploy \(.id // "?") status=\(.status // "?") commit=\(.commit.id // "?")"' "$TMP/resp.json" 2>/dev/null || head -c 400 "$TMP/resp.json"
    case "$code" in 200|201|202) ;; *) exit 1;; esac
    ;;
  suspend)
    need_confirm
    ID=$(svc_id "$NAME"); [ -z "$ID" ] && { echo "service $NAME: ABSENT"; exit 0; }
    code=$(send POST "/services/$ID/suspend"); echo "suspend -> HTTP $code"
    ;;
  *) echo "unknown action '${ACTION:-}'"; exit 1 ;;
esac
