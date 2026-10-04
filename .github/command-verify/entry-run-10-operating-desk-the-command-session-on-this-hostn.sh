set -uo pipefail
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo '```' >> "$GITHUB_STEP_SUMMARY"
A="${API:-https://sportsassets-api.onrender.com}"
H="X-Admin-Token: ${ADMIN_TOKEN}"
J=/tmp/cj.txt
rm -f "$J"

say "== E1 . THE SESSION COOKIE, ON THIS HOSTNAME =="
say "  Serving the page here does not establish that the COMMAND"
say "  session works here. The cookie is minted, then every read"
say "  below is made with the COOKIE ALONE and no operator token."
SC=$(curl -sS -o /tmp/sess.json -w '%{http_code}' -X POST \
       -H "$H" -c "$J" "$A/api/command/session/operator" \
       || echo 000)
say "  mint the session               HTTP $SC (expect 200)"
jq -r '"  cookie                        " + (.cookie // "-")
       + "   path " + (.cookie_path // "-")
       + "   token in body " + (.token_in_body | tostring)' \
  /tmp/sess.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
if grep -q "bt_command" "$J"; then
  say "  the jar holds bt_command      yes"
  say "  cookie attributes as sent     $(grep bt_command "$J" | cut -f1-6 | tr '\t' ' ')"
else
  say "  the jar holds bt_command      NO -- nothing was set"
fi
for U in "/api/command/bettor/desk/page" \
         "/api/command/bettor/desk" \
         "/api/command/bettor/control" \
         "/api/command/bettor/desk/page"; do
  printf '  cookie only  %-34s HTTP %s\n' "$U" \
    "$(curl -sS -o /dev/null -w '%{http_code}' -b "$J" "$A$U")" \
    | tee -a "$GITHUB_STEP_SUMMARY"
done
say "  (the fourth line is a REFRESH of the page, same cookie)"
WC=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -b "$J" \
       -H 'Content-Type: application/json' -d '{}' \
       "$A/api/command/bettor/control/pause" || echo 000)
say "  a WRITE with the cookie alone  HTTP $WC (expect 403 -- the"
say "                                 read roles stay read-only)"

say ""
say "== E2 . NO CREDENTIAL IN THE PAGE, THE URL OR STORAGE =="
curl -sS -b "$J" -o /tmp/page2.html \
  "$A/api/command/bettor/desk/page" || true
for PAT in "X-Admin-Token: [A-Za-z0-9]" "localStorage\." \
           "sessionStorage\." "token=" "password="; do
  if grep -qE "$PAT" /tmp/page2.html; then
    say "  FOUND in the served page      $PAT"
  else
    say "  absent from the served page   $PAT"
  fi
done
say "  the page reads /api/command/bettor/desk with"
say "  credentials: same-origin and no token in any URL."

say ""
say "== E3 . THE SERVER DECIDES ACTIVATION, AND SAYS WHICH WAY =="
say "  A REFUSAL IS 409 AND AN AUTHORISATION IS 200. Production"
say "  has no account bound and no owner-approved limit set, so"
say "  409 is the expected answer here; a 200 would name the"
say "  verdict AUTHORIZED_FOR_A_TEST_VENUE and would still enable"
say "  no capital -- funded submission is off in code."
AC=$(curl -sS -o /tmp/act.json -w '%{http_code}' -X POST -H "$H" \
       -H 'Content-Type: application/json' -d '{"by":"CI"}' \
       "$A/api/command/bettor/control/activate" || echo 000)
say "  activate                       HTTP $AC (expect 409 here)"
jq -r '(.detail // .) as $d
       | "  ok                             "
           + (($d.ok // false) | tostring),
         "  verdict                        "
           + ($d.verdict // "-"),
         "  refusal                        "
           + ($d.refusal // "-"),
         "  venue class                    "
           + ($d.venue_class // "-"),
         "  funded submission              "
           + ($d.funded_submission // "-"),
         "  authorises capital             "
           + (($d.authorises_capital // false) | tostring),
         "  unmet prerequisites            "
           + (($d.failed.unmet // []) | join(", "))' \
  /tmp/act.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true

say ""
say "== E4 . THE CONTROL SESSION IS SCOPED, AND SERVER-SIDE =="
say "  No operator password is held by this runner, so what is"
say "  checked is the REFUSAL side: a deliberately wrong password"
say "  must be rejected with a status, not a 200 carrying"
say "  ok:false, and it must set no cookie."
CS=$(curl -sS -o /tmp/ctlno.json -D /tmp/ctlno.hdr -w '%{http_code}' \
       -X POST -H 'Content-Type: application/json' \
       -d '{"password":"deliberately-not-the-operator-password"}' \
       "$A/api/command/session/control" || echo 000)
say "  wrong operator password        HTTP $CS (expect 401, or 503"
say "                                 if it is not configured yet)"
if grep -qi 'set-cookie: *bt_control' /tmp/ctlno.hdr; then
  say "  FOUND a control cookie on a refusal -- that is a defect"
else
  say "  no control cookie was set on the refusal"
fi
jq -r '"  reason                         "
         + ((.detail.reason // .reason) // "-")' \
  /tmp/ctlno.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
if grep -q 'id="oppass"' /tmp/page2.html; then
  say "  the page offers an operator PASSWORD field, not a token"
else
  say "  the page has no operator password field -- check the build"
fi
echo '```' >> "$GITHUB_STEP_SUMMARY"
