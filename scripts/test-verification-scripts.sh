#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/symgov-script-tests.XXXXXX")
FAKE_BIN=$TMP_ROOT/bin
TIMEOUT_LOG=$TMP_ROOT/timeout.log
mkdir -p "$FAKE_BIN"
trap 'rm -rf "$TMP_ROOT"' EXIT HUP INT TERM

cat >"$FAKE_BIN/timeout" <<'EOF'
#!/bin/sh
: "${SYMGOV_TEST_TIMEOUT_LOG:?}"
printf '%s\n' "$@" >"$SYMGOV_TEST_TIMEOUT_LOG"
exit "${SYMGOV_TEST_TIMEOUT_EXIT:-0}"
EOF
chmod +x "$FAKE_BIN/timeout"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

expect_exit() {
  expected=$1
  shift
  set +e
  "$@"
  actual=$?
  set -e
  [ "$actual" -eq "$expected" ] || fail "expected exit $expected, got $actual: $*"
}

expect_log() {
  expected=$1
  actual=$(cat "$TIMEOUT_LOG")
  [ "$actual" = "$expected" ] || {
    printf 'FAIL: timeout argv mismatch\nexpected:\n%s\nactual:\n%s\n' "$expected" "$actual" >&2
    exit 1
  }
}

run_with_fake_timeout() {
  PATH="$FAKE_BIN:$PATH" SYMGOV_TEST_TIMEOUT_LOG="$TIMEOUT_LOG" "$@"
}

expect_build_arguments_rejected() {
  argument_class=$1
  shift
  : >"$TIMEOUT_LOG"
  BUILD_ERROR=$TMP_ROOT/build-error.log
  set +e
  run_with_fake_timeout env SYMGOV_BUILD_OUT_DIR="$TMP_ROOT/external/build" \
    "$ROOT_DIR/scripts/build-frontend-isolated.sh" "$@" 2>"$BUILD_ERROR"
  build_exit=$?
  set -e
  [ "$build_exit" -eq 2 ] || \
    fail "expected $argument_class arguments to exit 2 before npm, got $build_exit: $*"
  [ ! -s "$TIMEOUT_LOG" ] || fail "$argument_class arguments invoked npm: $*"
  expect_count 1 \
    "build-frontend-isolated.sh does not accept CLI arguments; use SYMGOV_BUILD_OUT_DIR to select an output directory" \
    "$BUILD_ERROR"
  [ ! -e "$CALLER_ARGUMENT_OUTPUT" ] || \
    fail "$argument_class arguments wrote caller-controlled output"
  [ ! -e "$REPOSITORY_ARGUMENT_OUTPUT" ] || \
    fail "$argument_class arguments wrote inside the repository"
}

expect_count() {
  expected=$1
  needle=$2
  file=$3
  actual=$(grep -F -x -c -- "$needle" "$file" || true)
  [ "$actual" -eq "$expected" ] || \
    fail "expected $expected occurrences of '$needle' in $file, got $actual"
}

# The full backend gate must partition every collected test exactly once. Vlad's
# retired legacy-runner assertion is host-state dependent, while its other nodes
# remain portable repository tests.
VLAD_TEST=$ROOT_DIR/tests/test_vlad_hardening.py
expect_count 1 "@pytest.mark.external_workspace" "$VLAD_TEST"
marked_vlad_node=$(sed -n '/^@pytest.mark.external_workspace$/{n;p;}' "$VLAD_TEST")
[ "$marked_vlad_node" = "def test_legacy_vlad_runner_code_is_retired():" ] || \
  fail "only Vlad's retired legacy-runner node may use the external workspace marker"
BACKEND_TIMEOUT_LOG=$TMP_ROOT/backend-timeout.log
BACKEND_FAKE_BIN=$TMP_ROOT/backend-bin
mkdir -p "$BACKEND_FAKE_BIN"
cat >"$BACKEND_FAKE_BIN/timeout" <<'EOF'
#!/bin/sh
: "${SYMGOV_BACKEND_TIMEOUT_LOG:?}"
printf '%s\n' --- "$@" >>"$SYMGOV_BACKEND_TIMEOUT_LOG"
exit "${SYMGOV_BACKEND_TIMEOUT_EXIT:-0}"
EOF
chmod +x "$BACKEND_FAKE_BIN/timeout"
: >"$BACKEND_TIMEOUT_LOG"
PATH="$BACKEND_FAKE_BIN:$PATH" SYMGOV_BACKEND_TIMEOUT_LOG="$BACKEND_TIMEOUT_LOG" \
  "$ROOT_DIR/scripts/test-backend.sh" --full
expect_count 8 --- "$BACKEND_TIMEOUT_LOG"
expect_count 1 "$ROOT_DIR/tests/test_vlad_hardening.py" "$BACKEND_TIMEOUT_LOG"
expect_count 1 "not external_workspace" "$BACKEND_TIMEOUT_LOG"
expect_count 2 "external_workspace" "$BACKEND_TIMEOUT_LOG"

# Both backend timeout knobs must reject invalid durations before invoking pytest.
for backend_timeout_var in \
  SYMGOV_BACKEND_TIMEOUT_SECONDS \
  SYMGOV_EXTERNAL_TEST_TIMEOUT_SECONDS
do
  for invalid_timeout in '' 0 00 -1 1.5 abc ' 9'
  do
    : >"$BACKEND_TIMEOUT_LOG"
    BACKEND_ERROR=$TMP_ROOT/backend-error.log
    set +e
    env PATH="$BACKEND_FAKE_BIN:$PATH" \
      SYMGOV_BACKEND_TIMEOUT_LOG="$BACKEND_TIMEOUT_LOG" \
      "$backend_timeout_var=$invalid_timeout" \
      "$ROOT_DIR/scripts/test-backend.sh" --full 2>"$BACKEND_ERROR"
    backend_exit=$?
    set -e
    [ "$backend_exit" -eq 2 ] || \
      fail "expected exit 2 for $backend_timeout_var=$invalid_timeout, got $backend_exit"
    [ ! -s "$BACKEND_TIMEOUT_LOG" ] || \
      fail "$backend_timeout_var=$invalid_timeout invoked pytest"
    expect_count 1 "$backend_timeout_var must be a positive integer" "$BACKEND_ERROR"
  done
done

# Valid portable and external timeout overrides are forwarded independently.
: >"$BACKEND_TIMEOUT_LOG"
PATH="$BACKEND_FAKE_BIN:$PATH" SYMGOV_BACKEND_TIMEOUT_LOG="$BACKEND_TIMEOUT_LOG" \
  SYMGOV_BACKEND_TIMEOUT_SECONDS=9 SYMGOV_EXTERNAL_TEST_TIMEOUT_SECONDS=11 \
  "$ROOT_DIR/scripts/test-backend.sh" --full
expect_count 1 9s "$BACKEND_TIMEOUT_LOG"
expect_count 7 11s "$BACKEND_TIMEOUT_LOG"

# The build output must be absolute and resolve outside the repository.
expect_exit 2 env SYMGOV_BUILD_OUT_DIR=dist "$ROOT_DIR/scripts/build-frontend-isolated.sh"
expect_exit 2 env SYMGOV_BUILD_OUT_DIR= TMPDIR=. "$ROOT_DIR/scripts/build-frontend-isolated.sh"
expect_exit 2 env SYMGOV_BUILD_OUT_DIR="$ROOT_DIR/../$(basename "$ROOT_DIR")/dist" "$ROOT_DIR/scripts/build-frontend-isolated.sh"
ln -s "$ROOT_DIR" "$TMP_ROOT/repo-link"
expect_exit 2 env SYMGOV_BUILD_OUT_DIR="$TMP_ROOT/repo-link/not-created/../dist" "$ROOT_DIR/scripts/build-frontend-isolated.sh"

# Arbitrary Vite arguments can select a different root or configuration whose
# Rollup output overrides the wrapper's outDir. Fail closed for every argument,
# before timeout/npm, rather than maintaining an incomplete option denylist.
CALLER_ARGUMENT_OUTPUT=$TMP_ROOT/caller-controlled-output
REPOSITORY_ARGUMENT_OUTPUT=$ROOT_DIR/dist-argument-probe
[ ! -e "$REPOSITORY_ARGUMENT_OUTPUT" ] || \
  fail "repository argument-probe path already exists"
ARGUMENT_CONFIG=$TMP_ROOT/vite.argument.config.mjs
ARGUMENT_ROOT=$TMP_ROOT/argument-root
mkdir -p "$ARGUMENT_ROOT"
cat >"$ARGUMENT_CONFIG" <<EOF
export default { build: { rollupOptions: { output: { dir: '$CALLER_ARGUMENT_OUTPUT' } } } };
EOF
cat >"$ARGUMENT_ROOT/vite.config.mjs" <<EOF
export default { build: { rollupOptions: { output: { dir: '$REPOSITORY_ARGUMENT_OUTPUT' } } } };
EOF

expect_build_arguments_rejected "standalone --" --
expect_build_arguments_rejected "split --outDir" --outDir "$REPOSITORY_ARGUMENT_OUTPUT"
expect_build_arguments_rejected "equals --outDir" --outDir="$REPOSITORY_ARGUMENT_OUTPUT"
expect_build_arguments_rejected "split --config" --config "$ARGUMENT_CONFIG"
expect_build_arguments_rejected "short -c" -c "$ARGUMENT_CONFIG"
expect_build_arguments_rejected "positional root" "$ARGUMENT_ROOT"
expect_build_arguments_rejected "split --root" --root "$ARGUMENT_ROOT"
expect_build_arguments_rejected "equals --root" --root="$ARGUMENT_ROOT"
expect_build_arguments_rejected "split --mode" --mode production
expect_build_arguments_rejected "arbitrary flag" --debug

# TMPDIR may select a valid external default when it is absolute.
EXTERNAL_TMP=$TMP_ROOT/external-tmp
: >"$TIMEOUT_LOG"
run_with_fake_timeout env SYMGOV_BUILD_OUT_DIR= TMPDIR="$EXTERNAL_TMP" \
  "$ROOT_DIR/scripts/build-frontend-isolated.sh"
expect_log "120s
npm
run
build
--
--outDir
$EXTERNAL_TMP/symgov-build"

# All three outer timeout knobs reject non-positive/non-integer values.
for script_and_var in \
  "test-frontend.sh SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS" \
  "test-langfuse-poc.sh SYMGOV_LANGFUSE_TEST_TIMEOUT_SECONDS" \
  "build-frontend-isolated.sh SYMGOV_FRONTEND_BUILD_TIMEOUT_SECONDS"
do
  set -- $script_and_var
  script=$1
  var=$2
  expect_exit 2 env "$var=0" "$ROOT_DIR/scripts/$script"
  expect_exit 2 env "$var=00" "$ROOT_DIR/scripts/$script"
  expect_exit 2 env "$var=abc" "$ROOT_DIR/scripts/$script"
done

# Configured timeout values and the wrapped command's status pass through unchanged.
: >"$TIMEOUT_LOG"
expect_exit 37 env PATH="$FAKE_BIN:$PATH" SYMGOV_TEST_TIMEOUT_LOG="$TIMEOUT_LOG" \
  SYMGOV_TEST_TIMEOUT_EXIT=37 SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS=9 \
  "$ROOT_DIR/scripts/test-frontend.sh" --test-name-pattern smoke
first_log_line=$(sed -n '1p' "$TIMEOUT_LOG")
[ "$first_log_line" = "9s" ] || fail "frontend timeout override was not forwarded"

: >"$BACKEND_TIMEOUT_LOG"
expect_exit 37 env PATH="$BACKEND_FAKE_BIN:$PATH" \
  SYMGOV_BACKEND_TIMEOUT_LOG="$BACKEND_TIMEOUT_LOG" \
  SYMGOV_BACKEND_TIMEOUT_EXIT=37 SYMGOV_BACKEND_TIMEOUT_SECONDS=13 \
  "$ROOT_DIR/scripts/test-backend.sh" -k synthetic
expect_count 1 13s "$BACKEND_TIMEOUT_LOG"

: >"$TIMEOUT_LOG"
run_with_fake_timeout env SYMGOV_LANGFUSE_TEST_TIMEOUT_SECONDS=11 \
  "$ROOT_DIR/scripts/test-langfuse-poc.sh" -k synthetic
first_log_line=$(sed -n '1p' "$TIMEOUT_LOG")
[ "$first_log_line" = "11s" ] || fail "Langfuse timeout override was not forwarded"

# Exercise the real timeout utility, including underlying and timeout exits.
EXIT_BIN=$TMP_ROOT/exit-bin
mkdir -p "$EXIT_BIN"
for command_and_exit in "node 37" "uv 38" "npm 39"
do
  set -- $command_and_exit
  cat >"$EXIT_BIN/$1" <<EOF
#!/bin/sh
exit $2
EOF
  chmod +x "$EXIT_BIN/$1"
done
expect_exit 37 env PATH="$EXIT_BIN:/usr/bin:/bin" \
  "$ROOT_DIR/scripts/test-frontend.sh"
expect_exit 38 env PATH="$EXIT_BIN:/usr/bin:/bin" \
  "$ROOT_DIR/scripts/test-langfuse-poc.sh"
expect_exit 38 env PATH="$EXIT_BIN:/usr/bin:/bin" \
  "$ROOT_DIR/scripts/test-backend.sh"
expect_exit 39 env PATH="$EXIT_BIN:/usr/bin:/bin" \
  SYMGOV_BUILD_OUT_DIR="$TMP_ROOT/exit-build" \
  "$ROOT_DIR/scripts/build-frontend-isolated.sh"

SLOW_BIN=$TMP_ROOT/slow-bin
mkdir -p "$SLOW_BIN"
cat >"$SLOW_BIN/node" <<'EOF'
#!/bin/sh
sleep 2
EOF
chmod +x "$SLOW_BIN/node"
cat >"$SLOW_BIN/uv" <<'EOF'
#!/bin/sh
sleep 2
EOF
chmod +x "$SLOW_BIN/uv"
expect_exit 124 env PATH="$SLOW_BIN:/usr/bin:/bin" \
  SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS=1 \
  "$ROOT_DIR/scripts/test-frontend.sh"
expect_exit 124 env PATH="$SLOW_BIN:/usr/bin:/bin" \
  SYMGOV_BACKEND_TIMEOUT_SECONDS=1 \
  "$ROOT_DIR/scripts/test-backend.sh"

echo "verification script contract probes passed"
