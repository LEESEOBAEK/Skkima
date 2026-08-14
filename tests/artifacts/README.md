# Test Artifacts

This folder stores generated artifacts from validation, smoke tests, synthetic runs, and manual test probes.

## Layout

```text
tests/artifacts/
  test_runs/      Automated test runner outputs and reports.
  workflows/      Workflow runs created for demos, synthetic cases, and manual validation.
  runs/           Layer-level temporary run outputs created during checks.
  experiments/    Experimental design and validation outputs.
  usage_probes/   Probe outputs used to inspect behavior during development.
```

`outputs/` should be reserved for current user-facing runtime outputs, active workflow runs, and backups.
