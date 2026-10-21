# ACK local implementation

`deploy-ack.yaml` is the executable reusable workflow, with inline shell/Python and two approved profiles. It checks out caller `github.sha`; profile differences retain baseline build, resource and naming semantics. Services keeps its selector, ECS/OOS/E2E and dependencies in its own workflow.

## Local checks

Requires Python 3 with PyYAML, Bash, Git and Kustomize. Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_workflow.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_target_routing.py
bash tests/test_generation.sh
```

The test currently compares against the pinned sibling baseline checkouts in the authorized workspace. It uses synthetic values and stub Docker/kubectl only. No cloud, build push, notification or real credential tests are executed. Existing build-argument debug logging is retained by explicit scope decision; this is not a log-safety guarantee. Do not provide real credentials when running tests.

## New ACK application

Run `bash /path/to/init.sh --strategy ack --help`. ACK initialization requires explicit application identity, port, health path, branch/overlay/environment mapping, and a published full central SHA. It plans and validates in temporary storage, asks confirmation and only writes new files. `--dry-run` changes no target files, branch or index. Existing files and escaping paths are rejected. Service and HPA are opt-in. It never commits, pushes, opens a PR or configures GitHub/cloud resources. `init.sh --strategy vps-pull` continues to use the original implementation and its original lifecycle; it is not part of this ACK change.

Example (fixture SHA only; not a published release):

```sh
bash /path/to/init.sh --strategy ack --app fixture-product-app \
  --customer fixture --product product --module app --port 8000 --health /health \
  --environment-map '{"development":{"overlay":"development","environment":"dev"}}' \
  --central-sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --dry-run
```

## Release limitations

The original central revision `8e9d563eac561080c4ea9816b00a7e5da4559516` is published and immutable. TARGET-ROUTING-V1 central-only publication is authorized after independent review and fresh verification. Render/Services local examples retain their zero full-SHA placeholder and are not committed, pushed or bound automatically. See TARGET-CONFIG.md for configuration/migration. No real GitHub execution or deployment is implied.

Product reports the NONPROD Organization bundle configured with selected access only for `taotaro/lumio-services`; the two Secret values were filled separately and have not been read or validated here. PROD remains unconfigured. The earlier Org-query 403 is no longer a current limitation; private central workflow access is unchanged. Real Environment-only metadata timing, Secret precedence, cloud ownership and caller binding remain unverified. Changing settings or executing workflows/cloud/deployment requires separate authorization. Source publication cannot be treated as environment verification; genuine published full-SHA business caller binding remains required for whole-Feature `code_complete`.
