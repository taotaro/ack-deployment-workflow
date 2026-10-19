#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FIXTURE="$ROOT/tests/fixtures/target-repo"

make_target() {
  local target
  target=$(mktemp -d)
  cp "$FIXTURE/Dockerfile" "$target/Dockerfile"
  git -C "$target" init -b main >/dev/null
  git -C "$target" config user.email test@example.invalid
  git -C "$target" config user.name test
  git -C "$target" add Dockerfile
  git -C "$target" commit -m fixture >/dev/null
  git -C "$target" remote add origin git@github.com:example/fixture.git
  printf '%s' "$target"
}

# This SHA is synthetic local test input, not a published central revision.
ack_args=(--strategy ack --app demo-admin-web --customer demo --product admin
  --module web --port 3001 --health /status
  --environment-map '{"development":{"overlay":"development","environment":"dev"},"main":{"overlay":"production","environment":"prod"}}'
  --central-sha 1111111111111111111111111111111111111111 --yes)

ack_target=$(make_target)
(
  cd "$ack_target"
  # ACK writes assets only, even with an existing staged change.
  printf 'staged fixture\n' > staged.txt
  git add staged.txt
  before_head=$(git rev-parse HEAD)
  before_branch=$(git branch --show-current)
  before_index=$(git write-tree)
  "$ROOT/init.sh" "${ack_args[@]}"
  test "$(git rev-parse HEAD)" = "$before_head"
  test "$(git branch --show-current)" = "$before_branch"
  test "$(git write-tree)" = "$before_index"
  test -f .github/workflows/deploy.yaml
  test -f deploy/base/deployment.yaml
  test -f deploy/overlays/development/kustomization.yaml
  test -f deploy/overlays/production/kustomization.yaml
  test -f docs/deployment.md
  test ! -e deploy/base/hpa.yaml
  test ! -e docs/deployment-secrets.md
  ! grep -R -E '__INIT_[A-Z0-9_]+__' deploy

  python3 - <<'PY'
from pathlib import Path
import yaml
caller = yaml.safe_load(Path(".github/workflows/deploy.yaml").read_text())
assert set(caller["on"]) == {"workflow_dispatch"}
job = caller["jobs"]["build"]
assert job["uses"] == "taotaro/ack-deployment-workflow/.github/workflows/deploy-ack.yaml@" + "1" * 40
assert "development" in job["if"] and "main" in job["if"]
deployment, = yaml.safe_load_all(Path("deploy/base/deployment.yaml").read_text())
assert deployment["kind"] == "Deployment"
pod = deployment["spec"]["template"]["spec"]
assert "envFrom" not in pod["containers"][0]
assert pod["containers"][0]["ports"][0]["containerPort"] == "__APP_PORT__"
assert pod["containers"][0]["readinessProbe"]["httpGet"]["path"] == "__HEALTHCHECK_PATH__"
base = yaml.safe_load(Path("deploy/base/kustomization.yaml").read_text())
assert base["resources"] == ["deployment.yaml"]
for overlay in ("development", "production"):
    doc = yaml.safe_load(Path(f"deploy/overlays/{overlay}/kustomization.yaml").read_text())
    assert doc["resources"] == ["../../base"]
docs = Path("docs/deployment.md").read_text()
assert "APP_PORT=3001" in docs and "HEALTHCHECK_PATH=/status" in docs
assert "| main | main-env | production | prod |" in docs
PY

  # Re-running must refuse existing assets without changing even one byte.
  snapshot() {
    find .github deploy docs -type f -exec shasum -a 256 {} \; | sort
  }
  before_files=$(snapshot)
  if "$ROOT/init.sh" "${ack_args[@]}" > overwrite.log 2>&1; then
    echo "expected overwrite refusal" >&2
    exit 1
  fi
  grep -q 'Refusing existing or escaping path:' overwrite.log
  test "$(snapshot)" = "$before_files"
  test "$(git rev-parse HEAD)" = "$before_head"
  test "$(git branch --show-current)" = "$before_branch"
  test "$(git write-tree)" = "$before_index"
)
rm -rf "$ack_target"

vps_target=$(make_target)
(
  cd "$vps_target"
  "$ROOT/init.sh" --strategy vps-pull --app demo-admin --port 3001 --health /status --branches development --base main --notification none --vps-host-port 8080 --vps-channel development --no-pr --yes
  test -f .github/workflows/build-and-push-to-acr.yaml
  test -f ops/vps/docker-compose.yaml
  test -f ops/vps/deploy-image.sh
  grep -q '8080:3001' ops/vps/docker-compose.yaml
  # VPS assets are concrete files, so no init token may remain.
  ! grep -R -E '__INIT_[A-Z0-9_]+__' ops
  ruby -e 'require "yaml"; YAML.load_file("ops/vps/docker-compose.yaml"); YAML.load_file(".github/workflows/build-and-push-to-acr.yaml")'
  grep -q 'flock -n' ops/vps/deploy-image.sh
  bash -n ops/vps/deploy-image.sh
)
rm -rf "$vps_target"

dry_target=$(make_target)
(
  cd "$dry_target"
  before_head=$(git rev-parse HEAD)
  before_index=$(git write-tree)
  before_status=$(git status --porcelain)
  "$ROOT/init.sh" --dry-run "${ack_args[@]}"
  test "$(git rev-parse HEAD)" = "$before_head"
  test "$(git write-tree)" = "$before_index"
  test "$(git status --porcelain)" = "$before_status"
  test "$(git branch --show-current)" = "main"
  test ! -e .github
  test ! -e deploy
  test ! -e docs
)
rm -rf "$dry_target"

echo "generation tests passed"