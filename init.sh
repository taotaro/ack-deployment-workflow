#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TEMPLATE_ROOT="$SCRIPT_DIR/templates"
TARGET_ROOT=$(pwd)

STRATEGY=""
APP_NAME=""
DOCKERFILE="Dockerfile"
APP_PORT=""
HEALTH_PATH=""
BRANCHES="development,sit,uat,main"
BASE_BRANCH="development"
RUNTIME_CONFIG="none"
CONFIG_MAP_NAME=""
NOTIFICATION_MODE="none"
VPS_HOST_PORT=""
VPS_CHANNEL="development"
OVERLAY_SOURCE=""
DRY_RUN=0
ASSUME_YES=0
CREATE_PR=1
FORCE=0

usage() {
  cat <<'USAGE'
Usage: init.sh [options]

Run from the root of an existing Git project that already has a Dockerfile.

Options:
  --strategy ack|vps-pull
  --app NAME
  --dockerfile PATH
  --port PORT
  --health PATH
  --branches CSV                Default: development,sit,uat,main
  --base BRANCH                 Default: development
  --runtime-config MODE         none|existing-configmap
  --config-map NAME             Required with existing-configmap
  --notification MODE           none|integration-webhook|lark-standard-bot
  --vps-host-port PORT          Required for vps-pull
  --vps-channel TAG             Default: development
  --overlay-source BRANCH       ACK: branch that receives the production patch (default: main when present)
  --dry-run                     Preview only; do not mutate the target repository
  --no-pr                       Commit generated files but do not create a PR
  --force                       Permit replacing generated deployment files
  --yes                         Accept the final summary without a prompt
  --help
USAGE
}

die() { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

require_command() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }

prompt() {
  local label=$1 default=${2:-} value=""
  if [[ -n "$default" ]]; then
    read -r -p "$label [$default]: " value
    printf '%s' "${value:-$default}"
  else
    read -r -p "$label: " value
    printf '%s' "$value"
  fi
}

confirm() {
  [[ "$ASSUME_YES" == "1" ]] && return 0
  local reply
  read -r -p "Continue? [y/N]: " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

validate_app_name() { [[ "$1" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] && [[ ${#1} -le 63 ]]; }
validate_port() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )); }
validate_health_path() { [[ "$1" =~ ^/[^[:space:]]*$ ]]; }

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --strategy) STRATEGY=$2; shift 2 ;;
      --app) APP_NAME=$2; shift 2 ;;
      --dockerfile) DOCKERFILE=$2; shift 2 ;;
      --port) APP_PORT=$2; shift 2 ;;
      --health) HEALTH_PATH=$2; shift 2 ;;
      --branches) BRANCHES=$2; shift 2 ;;
      --base) BASE_BRANCH=$2; shift 2 ;;
      --runtime-config) RUNTIME_CONFIG=$2; shift 2 ;;  # accepted for backward compatibility; ACK now always renders an optional ConfigMap ref
      --config-map) CONFIG_MAP_NAME=$2; shift 2 ;;  # accepted and ignored; see docs/deployment-secrets.md
      --notification) NOTIFICATION_MODE=$2; shift 2 ;;
      --vps-host-port) VPS_HOST_PORT=$2; shift 2 ;;
      --vps-channel) VPS_CHANNEL=$2; shift 2 ;;
      --overlay-source) OVERLAY_SOURCE=$2; shift 2 ;;
      --dry-run) DRY_RUN=1; shift ;;
      --no-pr) CREATE_PR=0; shift ;;
      --force) FORCE=1; shift ;;
      --yes) ASSUME_YES=1; shift ;;
      --help|-h) usage; exit 0 ;;
      *) die "unknown option: $1" ;;
    esac
  done
}

preflight() {
  require_command git
  require_command bash
  [[ -d "$TEMPLATE_ROOT" ]] || die "templates not found beside init.sh"
  git -C "$TARGET_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "run this script from a Git repository root"
  [[ -z "$(git -C "$TARGET_ROOT" status --porcelain)" ]] || die "target working tree is not clean"
  [[ -f "$TARGET_ROOT/$DOCKERFILE" ]] || die "Dockerfile not found: $DOCKERFILE"
  git -C "$TARGET_ROOT" remote get-url origin >/dev/null 2>&1 || die "origin remote is required"
  if [[ "$DRY_RUN" == "0" && "$CREATE_PR" == "1" ]]; then
    require_command gh
    gh auth status >/dev/null 2>&1 || die "gh is not authenticated"
  fi
}

collect_common_inputs() {
  if [[ -z "$STRATEGY" ]]; then
    echo "Select deployment strategy:"
    echo "  1) ack"
    echo "  2) vps-pull"
    local choice; choice=$(prompt "Choice" "1")
    case "$choice" in 1|ack) STRATEGY=ack ;; 2|vps-pull) STRATEGY=vps-pull ;; *) die "strategy must be ack or vps-pull" ;; esac
  fi
  [[ "$STRATEGY" == "ack" || "$STRATEGY" == "vps-pull" ]] || die "strategy must be ack or vps-pull"
  [[ -n "$APP_NAME" ]] || APP_NAME=$(prompt "Application name")
  validate_app_name "$APP_NAME" || die "application name must be a DNS-1123 label"
  [[ -n "$APP_PORT" ]] || APP_PORT=$(prompt "Container port")
  validate_port "$APP_PORT" || die "container port must be between 1 and 65535"
  [[ -n "$HEALTH_PATH" ]] || HEALTH_PATH=$(prompt "Health check path" "/status")
  validate_health_path "$HEALTH_PATH" || die "health path must start with / and contain no whitespace"
  [[ -n "$BRANCHES" ]] || die "at least one branch is required"
  [[ -n "$BASE_BRANCH" ]] || BASE_BRANCH=$(prompt "PR base branch" "development")
  if [[ -z "$NOTIFICATION_MODE" || "$NOTIFICATION_MODE" == "none" && "$ASSUME_YES" == "0" ]]; then
    NOTIFICATION_MODE=$(prompt "Notification mode (none|integration-webhook|lark-standard-bot)" "none")
  fi
  [[ "$NOTIFICATION_MODE" =~ ^(none|integration-webhook|lark-standard-bot)$ ]] || die "invalid notification mode"
}

collect_strategy_inputs() {
  if [[ "$STRATEGY" == "vps-pull" ]]; then
    [[ -n "$VPS_HOST_PORT" ]] || VPS_HOST_PORT=$(prompt "VPS host port" "$APP_PORT")
    validate_port "$VPS_HOST_PORT" || die "VPS host port must be between 1 and 65535"
    [[ -n "$VPS_CHANNEL" ]] || VPS_CHANNEL=$(prompt "VPS image channel tag" "development")
    validate_app_name "$VPS_CHANNEL" || die "VPS channel must be a DNS-1123 label"
  fi
}

build_branches_yaml() {
  local branch
  BRANCHES_YAML=""
  IFS=',' read -r -a branch_list <<< "$BRANCHES"
  for branch in "${branch_list[@]}"; do
    [[ "$branch" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid branch name: $branch"
    BRANCHES_YAML+="      - $branch"$'\n'
  done
  BRANCHES_YAML=${BRANCHES_YAML%$'\n'}
}

render_template() {
  local source=$1 destination=$2 line
  [[ -f "$source" ]] || die "template missing: $source"
  if [[ -e "$destination" && "$FORCE" != "1" ]]; then
    die "refusing to overwrite existing file: ${destination#$TARGET_ROOT/}; use --force after review"
  fi
  mkdir -p "$(dirname "$destination")"
  : > "$destination"
  # Only __INIT_*__ tokens are resolved at initialization time. All other
  # __QT__UPPER_CASE__QT__ tokens are runtime placeholders rendered by the deploy workflow.
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line//__INIT_APP_NAME__/$APP_NAME}
    line=${line//__INIT_APP_PORT__/$APP_PORT}
    line=${line//__INIT_HEALTH_PATH__/$HEALTH_PATH}
    line=${line//__INIT_NOTIFICATION_MODE__/$NOTIFICATION_MODE}
    line=${line//__INIT_VPS_HOST_PORT__/$VPS_HOST_PORT}
    line=${line//__INIT_VPS_CHANNEL__/$VPS_CHANNEL}
    line=${line//__INIT_BRANCHES_YAML__/$BRANCHES_YAML}
    printf '%s\n' "$line" >> "$destination"
  done < "$source"
}

resolve_overlay_source() {
  local branch found=0
  IFS=',' read -r -a branch_list <<< "$BRANCHES"
  if [[ -z "$OVERLAY_SOURCE" ]]; then
    for branch in "${branch_list[@]}"; do
      [[ "$branch" == "main" ]] && OVERLAY_SOURCE="main"
    done
    [[ -z "$OVERLAY_SOURCE" ]] && OVERLAY_SOURCE=${branch_list[0]}
  fi
  for branch in "${branch_list[@]}"; do
    [[ "$branch" == "$OVERLAY_SOURCE" ]] && found=1
  done
  [[ "$found" == "1" ]] || die "overlay source branch is not in --branches: $OVERLAY_SOURCE"
}

generate_ack() {
  resolve_overlay_source
  render_template "$TEMPLATE_ROOT/ack/workflow.yaml.tmpl" "$TARGET_ROOT/.github/workflows/build-image-to-acr-and-deploy-to-ack.yaml"
  render_template "$TEMPLATE_ROOT/ack/deploy/base/deployment.yaml.tmpl" "$TARGET_ROOT/deploy/base/deployment.yaml"
  render_template "$TEMPLATE_ROOT/ack/deploy/base/hpa.yaml.tmpl" "$TARGET_ROOT/deploy/base/hpa.yaml"
  render_template "$TEMPLATE_ROOT/ack/deploy/base/kustomization.yaml.tmpl" "$TARGET_ROOT/deploy/base/kustomization.yaml"
  local branch template
  IFS=',' read -r -a branch_list <<< "$BRANCHES"
  for branch in "${branch_list[@]}"; do
    template="$TEMPLATE_ROOT/ack/deploy/overlays/generic/kustomization.yaml.tmpl"
    if [[ "$branch" == "$OVERLAY_SOURCE" ]]; then
      template="$TEMPLATE_ROOT/ack/deploy/overlays/main/kustomization.yaml.tmpl"
    fi
    render_template "$template" "$TARGET_ROOT/deploy/overlays/$branch/kustomization.yaml"
  done
  render_template "$TEMPLATE_ROOT/common/deployment.md.tmpl" "$TARGET_ROOT/docs/deployment.md"
}

generate_vps_pull() {
  render_template "$TEMPLATE_ROOT/vps-pull/workflow.yaml.tmpl" "$TARGET_ROOT/.github/workflows/build-and-push-to-acr.yaml"
  render_template "$TEMPLATE_ROOT/vps-pull/ops/vps/docker-compose.yaml.tmpl" "$TARGET_ROOT/ops/vps/docker-compose.yaml"
  render_template "$TEMPLATE_ROOT/vps-pull/ops/vps/deploy-image.sh.tmpl" "$TARGET_ROOT/ops/vps/deploy-image.sh"
  render_template "$TEMPLATE_ROOT/vps-pull/ops/vps/project-deploy.service.tmpl" "$TARGET_ROOT/ops/vps/$APP_NAME-deploy.service"
  render_template "$TEMPLATE_ROOT/vps-pull/ops/vps/project-deploy.timer.tmpl" "$TARGET_ROOT/ops/vps/$APP_NAME-deploy.timer"
  render_template "$TEMPLATE_ROOT/vps-pull/ops/vps/project-deploy.cron.tmpl" "$TARGET_ROOT/ops/vps/$APP_NAME-deploy.cron.example"
  render_template "$TEMPLATE_ROOT/vps-pull/ops/vps/app.env.example.tmpl" "$TARGET_ROOT/ops/vps/app.env.example"
  render_template "$TEMPLATE_ROOT/common/vps-deployment.md.tmpl" "$TARGET_ROOT/docs/vps-deployment.md"
  chmod +x "$TARGET_ROOT/ops/vps/deploy-image.sh"
}

print_summary() {
  local branch_name="ci/init-$STRATEGY-$APP_NAME"
  cat <<SUMMARY

Deployment initializer summary
  strategy: $STRATEGY
  application: $APP_NAME
  dockerfile: $DOCKERFILE
  container port: $APP_PORT
  health path: $HEALTH_PATH
  branches: $BRANCHES
  notification: $NOTIFICATION_MODE
  target branch: $branch_name
  PR base: $BASE_BRANCH
  dry run: $DRY_RUN
SUMMARY
  if [[ "$STRATEGY" == "vps-pull" ]]; then
    echo "  VPS host port: $VPS_HOST_PORT, channel: $VPS_CHANNEL"
  fi
}

print_file_plan() {
  echo "Files to generate:"
  if [[ "$STRATEGY" == "ack" ]]; then
    resolve_overlay_source
    echo "  .github/workflows/build-image-to-acr-and-deploy-to-ack.yaml"
    echo "  deploy/base/{deployment.yaml,hpa.yaml,kustomization.yaml}"
    IFS=',' read -r -a branch_list <<< "$BRANCHES"
    for branch in "${branch_list[@]}"; do
      if [[ "$branch" == "$OVERLAY_SOURCE" ]]; then
        echo "  deploy/overlays/$branch/kustomization.yaml (production patch)"
      else
        echo "  deploy/overlays/$branch/kustomization.yaml"
      fi
    done
  else
    echo "  .github/workflows/build-and-push-to-acr.yaml"
    echo "  ops/vps/{docker-compose.yaml,deploy-image.sh,app.env.example}"
    echo "  ops/vps/$APP_NAME-deploy.{service,timer,cron.example}"
    echo "  docs/vps-deployment.md"
  fi
}

generated_paths() {
  GENERATED_PATHS=(.github docs)
  if [[ "$STRATEGY" == "ack" ]]; then
    GENERATED_PATHS+=(deploy)
  else
    GENERATED_PATHS+=(ops)
  fi
}

validate_generated() {
  generated_paths
  git -C "$TARGET_ROOT" add -N "${GENERATED_PATHS[@]}"
  git -C "$TARGET_ROOT" diff --check
  # The generated manifests keep __PLACEHOLDER__ tokens on purpose; they are
  # rendered by the deploy workflow. Confirm no init-time token leaked through.
  local scan_root
  if [[ "$STRATEGY" == "ack" ]]; then scan_root="$TARGET_ROOT/deploy"; else scan_root="$TARGET_ROOT/ops"; fi
  if grep -R --line-number -E '__INIT_[A-Z0-9_]+__' "$scan_root" 2>/dev/null; then
    die "unresolved initializer tokens found"
  fi
  if [[ "$STRATEGY" == "vps-pull" ]]; then
    bash -n "$TARGET_ROOT/ops/vps/deploy-image.sh"
  fi
  generate_docs
}

generate_docs() {
  local dest="$TARGET_ROOT/docs/deployment-secrets.md"
  if [[ -e "$dest" && "$FORCE" != "1" ]]; then
    info "docs/deployment-secrets.md already exists; leaving it untouched"
    return 0
  fi
  mkdir -p "$TARGET_ROOT/docs"
  {
    cat <<HEAD
# GitHub Environment configuration

This repository deploys to ACK with the shared Lumio ACR/ACK workflow. All values
live in GitHub Environment Secrets and Variables; the Kustomize templates in
\`deploy/\` keep \`__PLACEHOLDER__\` tokens and are rendered by the workflow at deploy time.

Application: \`$APP_NAME\`

## Environment mapping

| Git branch | GitHub Environment | \`DEPLOY_OVERLAY\` | \`ENVIRONMENT\` |
|---|---|---|---|
HEAD
    local branch env_name
    IFS=',' read -r -a branch_list <<< "$BRANCHES"
    for branch in "${branch_list[@]}"; do
      case "$branch" in
        main|production|prod) env_name="prod" ;;
        development|dev) env_name="dev" ;;
        *) env_name="$branch" ;;
      esac
      if [[ "$branch" == "$OVERLAY_SOURCE" ]]; then
        echo "| \`$branch\` | \`$branch-env\` | \`production\` | \`$env_name\` |"
      else
        echo "| \`$branch\` | \`$branch-env\` | \`$branch\` | \`$env_name\` |"
      fi
    done
    cat <<'TAIL'

The Deployment name is composed at runtime: `${CUSTOMER_NAME}-${PRODUCT_NAME}-${MODULE_NAME}-${ENVIRONMENT}`.

## Non-sensitive Variables

Set these separately in each Environment. Blank cells inherit the workflow default or the derived name.

| Variable | Purpose |
|---|---|
| `CUSTOMER_NAME`, `PRODUCT_NAME`, `MODULE_NAME` | Deployment name, namespace, ServiceAccount and ConfigMap derivation |
| `DEPLOY_OVERLAY` | Overlay directory under `deploy/overlays/` |
| `ENVIRONMENT` | Runtime environment suffix used in the Deployment name |
| `ACR_NAMESPACE`, `ACR_REPOSITORY` | Image repository path |
| `K8S_NAMESPACE` | Target namespace (defaults to `${CUSTOMER_NAME}-${ENVIRONMENT}`) |
| `APP_PORT`, `SERVICE_PORT`, `HEALTHCHECK_PATH` | Container port, Service port and health endpoint |
| `SERVICE_ACCOUNT_NAME`, `CONFIG_MAP_NAME` | Optional overrides; otherwise derived |
| `REQUEST_CPU`, `REQUEST_MEMORY`, `LIMIT_CPU`, `LIMIT_MEMORY` | Default resources |
| `HPA_MAX_REPLICAS`, `HPA_CPU_UTILIZATION`, `HPA_MEMORY_UTILIZATION`, `HPA_SCALE_DOWN_STABILIZATION` | Default HPA |
| `PRODUCTION_*` | Production overlay resource and replica overrides |
| `KUSTOMIZE_IMAGE_NAME` | Kustomize image key (default `app-image`) |
| `BUILD_ARGS` | Public Dockerfile build ARGs as JSON; never put credentials here |
| `PROJECT_DISPLAY_NAME` | Lark notification project label |

## Required Secrets

Set these encrypted Secrets in every Environment. Do not record their values in Git.

| Secret name | Purpose |
|---|---|
| `ACR_REGISTRY` | Public ACR hostname, without `https://` or a path; `-vpc` endpoints cannot be used from GitHub-hosted runners |
| `ACR_NAMESPACE`, `ACR_REPOSITORY` | ACR path (Variables or Secrets) |
| `ALICLOUD_ACCESS_KEY_ID`, `ALICLOUD_ACCESS_KEY_SECRET` | Alibaba Cloud identity used by the ACR and ACK actions |
| `ALICLOUD_REGION_ID`, `ACR_INSTANCE_ID` | ACR enterprise instance identifiers |
| `ACK_CLUSTER_ID` | Target ACK cluster |
| `K8S_NAMESPACE` | Target namespace (Secret or Variable) |
| `LARK_BOT_WEBHOOK` | Lark incoming webhook for run notifications |

Service, Ingress, DNS, namespace and ServiceAccount remain external DevOps responsibilities; the workflow applies only Deployment and HPA.
TAIL
  } > "$dest"
  info "wrote docs/deployment-secrets.md"
}

apply() {
  local branch_name="ci/init-$STRATEGY-$APP_NAME"
  if [[ "$DRY_RUN" == "1" ]]; then
    info "dry-run complete; no target files, branches, commits, or PRs were created"
    return 0
  fi
  git -C "$TARGET_ROOT" show-ref --verify --quiet "refs/heads/$branch_name" && die "branch already exists: $branch_name"
  git -C "$TARGET_ROOT" switch -c "$branch_name"
  if [[ "$STRATEGY" == "ack" ]]; then generate_ack; else generate_vps_pull; fi
  validate_generated
  git -C "$TARGET_ROOT" add "${GENERATED_PATHS[@]}"
  git -C "$TARGET_ROOT" commit -m "ci: initialize $STRATEGY deployment"
  if [[ "$CREATE_PR" == "1" ]]; then
    git -C "$TARGET_ROOT" push -u origin "$branch_name"
    gh -R "$(git -C "$TARGET_ROOT" remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')" pr create --base "$BASE_BRANCH" --head "$branch_name" --title "ci: initialize $STRATEGY deployment" --body "Generated by project-deployment-init. Review platform Secrets, Environment Variables, and external infrastructure prerequisites before merge."
  fi
}

main() {
  # ACK has a separate plan/validate/confirm/write-only lifecycle. VPS-pull
  # continues through the original implementation without behavior changes.
  local selected_strategy="" previous="" arg
  for arg in "$@"; do
    if [[ "$previous" == "--strategy" ]]; then selected_strategy="$arg"; fi
    previous="$arg"
  done
  if [[ -z "$selected_strategy" && " ${*} " != *" --help "* && " ${*} " != *" -h "* ]]; then
    local choice
    choice=$(prompt "Strategy (ack|vps-pull)" "ack")
    selected_strategy="$choice"
    if [[ "$choice" == "vps-pull" ]]; then set -- --strategy vps-pull "$@"; fi
  fi
  if [[ "$selected_strategy" == "ack" ]]; then
    exec python3 "$SCRIPT_DIR/init-ack.py" "$@"
  fi
  parse_args "$@"
  preflight
  collect_common_inputs
  collect_strategy_inputs
  build_branches_yaml
  if [[ "$STRATEGY" == "ack" ]]; then resolve_overlay_source; fi
  print_summary
  print_file_plan
  if [[ "$DRY_RUN" == "0" ]]; then confirm || die "cancelled by user"; fi
  apply
}

main "$@"
