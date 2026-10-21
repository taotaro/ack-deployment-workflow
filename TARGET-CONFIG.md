# Target configuration and migration — TARGET-ROUTING-V1

New generated callers set `target_routing: environment`. The central job first enters the **calling repository's** GitHub Environment, then resolves infrastructure. Caller jobs do not read Environment Variables in advance. All services in a Services repository/Environment share one target; no per-service routing.

## Complete bundles and precedence

Configure two complete Organization bundles (repository-level authorization): `LUMIO_PROD_*` and `LUMIO_NONPROD_*`. Each has these suffixes:

- Variables: `REGION_ID`, `ACR_REGISTRY`, `ACR_INSTANCE_ID`, `ACK_CLUSTER_ID`.
- Secrets: `ALICLOUD_ACCESS_KEY_ID`, `ALICLOUD_ACCESS_KEY_SECRET`.

For example, production uses `LUMIO_PROD_REGION_ID` and `LUMIO_PROD_ALICLOUD_ACCESS_KEY_ID`. Production and nonproduction may use different accounts, registries and clusters. Only the selected bundle must be complete; the unused bundle can be empty.

An independent application repository can configure **all six** `LUMIO_TARGET_*` members in its GitHub Environment to replace the entire selected default bundle. Any nonempty override member activates the override; incomplete overrides fail before build/cloud. Never fill missing override members from defaults or override individual PROD/NONPROD members for another account. Reserve TARGET for full Environment overrides; keep it absent at Organization/repository scope. GitHub merges same-name values per key, not atomically per bundle. Local code cannot infer their original scope or prove cloud ownership.

All six routed cloud Secrets are optional in schema and explicitly transmitted by name in thin callers, even when caller-context expressions resolve empty because the values exist only in the actual Environment. No `secrets: inherit` or central-repository Secrets. Namespace, image repository, overlay, resources and application configuration remain existing application Environment parameters. The selected credentials/registry/region/instance/cluster are validated together and used consistently by ACR and ACK. Credentials are never newly printed by the resolver.

## Fail-closed mapping

- Services: `dev/dev-env`, `sit/sit-env`, `uat/uat-env` use NONPROD; `production/production-env` uses PROD. Existing prepare maps development to dev/dev-env. E2E gates remain unchanged.
- Single app/Render: actual `ENVIRONMENT`, falling back to `DEPLOY_OVERLAY` only when empty. PROD aliases: prod/prd/production. NONPROD aliases: dev/development/sit/uat/preview. Unknown identities fail. Custom overlay paths require an explicit known ENVIRONMENT. Environment name remains branch-env. Dispatch input is only the existing consistency check, never an account selector. Render prod/prd/preview naming stays unchanged.

Metadata identifiers use letters/digits/dot/underscore/hyphen; registry is a host with optional port1–65535, no URL/path. Credentials must be nonempty single-line values without whitespace. The caller Environment still owns overlay/path validation. Application parameters are not infrastructure override members.

## Compatibility and migration

Published `8e9d563eac561080c4ea9816b00a7e5da4559516` remains immutable with its original explicit-call interface. The new revision defaults `target_routing` to `explicit`, still requiring the complete four infrastructure inputs and explicitly transmitted legacy ALICLOUD credentials at runtime. No routed fallback in explicit mode. Routed calls reject nonempty legacy bindings.

To migrate, prepare complete defaults/override under separate platform authorization, replace four legacy infrastructure inputs and two legacy cloud Secret mappings with `target_routing: environment` and six named routed Secrets, then review/pin the newly published **full SHA**. Do not point callers at main or auto-bind business repositories. Until migration, old pins do not gain routing behavior.

Target selection is **not credential isolation**: Org authorization remains repository-level. Same-named Environments in separate application repositories can use different complete TARGET bundles. Synthetic local proof cannot establish real Secret/Environment precedence or account ownership. Real GitHub execution, settings/Secrets, cloud/E2E/notification and deployment need separate authorization. Central source publication is not deployment or environment verification.
