#!/usr/bin/env bats

setup() {
  export PATH="$BATS_TEST_DIRNAME/../bin:$PATH"
  export CCX_INSTANCE_ID_FILE="$BATS_TMPDIR/instance_id"
  echo "i-deadbeef" > "$CCX_INSTANCE_ID_FILE"
}

@test "help prints usage" {
  run ccxctl --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"ccxctl — control the ccx coding station"* ]]
}

@test "no args prints usage" {
  run ccxctl
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: ccxctl"* ]]
}

@test "unknown subcommand exits 2" {
  run ccxctl bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage: ccxctl"* ]]
}

@test "missing instance id file exits non-zero" {
  export CCX_INSTANCE_ID_FILE="$BATS_TMPDIR/nope"
  run ccxctl status
  [ "$status" -ne 0 ]
  [[ "$output" == *"instance id file missing"* ]]
}

@test "help lists refresh-dns subcommand" {
  run ccxctl --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"refresh-dns"* ]]
}
