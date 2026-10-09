# Gate conclusions for one SHA from `gh api .../actions/runs?head_sha=SHA`
# (pm-acceptance.yml, step "Exact-SHA gate conclusions"). $r[0] is the run
# list. A run cancelled or skipped before it concluded carries no verdict and
# is never the run a gate reads; it is listed under no_verdict instead.
def no_verdict: (.conclusion == "cancelled" or .conclusion == "skipped");
def runs_of(n): [$r[0][] | select(.name == n)];
def latest(n): ([runs_of(n)[] | select(no_verdict | not)] | sort_by(.created_at) | last);
def green(n): (latest(n) // {} | (.status == "completed" and .conclusion == "success"));
{backend_tests_green: green("backend-tests"),
 capital_critical_green: green("capital-critical"),
 commit_guard_green: green("commit-guard"),
 engine_diagnostic_green: green("engine-diagnostic"),
 runs: {backend_tests: latest("backend-tests"),
        capital_critical: latest("capital-critical"),
        commit_guard: latest("commit-guard"),
        engine_diagnostic: latest("engine-diagnostic")},
 no_verdict: [ ["backend-tests", "capital-critical", "commit-guard", "engine-diagnostic"][] as $n
               | runs_of($n)[] | select(no_verdict) | {name, id, conclusion, created_at} ]}
