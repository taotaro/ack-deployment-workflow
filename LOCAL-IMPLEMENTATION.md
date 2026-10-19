# ACK local implementation

`deploy-ack.yaml` is the executable reusable workflow, with inline shell/Python and two approved profiles. It checks out caller `github.sha`; profile differences retain baseline build, resource and naming semantics. Services keeps its selector, ECS/OOS/E2E and dependencies in its own workflow.

## Local checks

Requires Python 3 with PyYAML, Bash, Git and Kustomize. Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_workflow.py
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

The remote central repository is empty because commit/push is not authorized. Render/Services caller references still carry the explicit zero full-SHA release placeholder; they cannot resolve a real remote workflow until an authorized central commit is published and both callers pinned to it. Do not replace this pin with `main` or claim end-to-end GitHub execution. The central body is not a candidate echo stub.

Org Secrets/Variables queries remain 403; private workflow access is `none`. Changing these settings, real workflow/cloud calls and deployment are separately authorized stages. Their absence cannot be treated as environment verification. Code review and independent fresh verification remain required before any `code_complete` decision.
