#!/usr/bin/env just --justfile

[default]
[doc('List available recipes')]
list:
    just --list

[doc('Stage updated files and run pre-commit hooks')]
pre-commit:
    git add --update
    uv run pre-commit

alias p := pre-commit

# --cov goes last because it takes an optional value, which would swallow a test path after it.
# Only a full run can reach the coverage floor, so any arguments (usually selecting tests) turn it off.
[doc('Run type checking, then the tests with coverage, passing any arguments on to pytest')]
[positional-arguments]
test *ARGS:
    uv run mypy
    uv run pytest -n auto --dist worksteal --failed-first "$@" --cov {{ if ARGS == "" { "" } else { "--cov-fail-under=0" } }}

alias t := test

[doc('Run mutation testing, or re-run only the named mutants (globs work)')]
[positional-arguments]
mutate *MUTANTS:
    uv run mutmut run "$@"

[doc('Time how long the app takes to answer keys on generated diffs, passing any arguments on (see --help)')]
[positional-arguments]
bench *ARGS:
    uv run python -m benchmarks.keys "$@"

[doc('Profile one benchmark workload with the sampling profiler, writing a flamegraph to profiles/')]
profile WORKLOAD="large/down":
    mkdir -p profiles
    uv run python -m profiling.sampling run --mode cpu --binary -o profiles/{{ replace(WORKLOAD, "/", "-") }}.bin -m benchmarks.keys --only {{ WORKLOAD }} --repeat 1
    uv run python -m profiling.sampling replay --flamegraph -o profiles/{{ replace(WORKLOAD, "/", "-") }}.html profiles/{{ replace(WORKLOAD, "/", "-") }}.bin

[doc('Upgrade all dependencies, and the tools in mise.toml')]
upgrade:
    uv lock --upgrade
    mise upgrade --local --bump
    mise lock
