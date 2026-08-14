from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "agents" / "agent.md").exists() and (
            candidate / "engine" / "python" / "layers"
        ).exists():
            return candidate
    return start


PROJECT_ROOT = find_project_root(SCRIPT_DIR)
ENGINE_ROOT = PROJECT_ROOT / "engine" / "python"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

LAYER_DIRS = [
    ENGINE_ROOT / "layers" / "01_input_structuring",
    ENGINE_ROOT / "layers" / "02_router",
    ENGINE_ROOT / "layers" / "03_route_validation",
    ENGINE_ROOT / "layers" / "04_direction_lens",
    ENGINE_ROOT / "layers" / "05_situation_context",
    ENGINE_ROOT / "layers" / "06_human_readable_report",
]
for layer_dir in reversed(LAYER_DIRS):
    if layer_dir.exists() and str(layer_dir) not in sys.path:
        sys.path.insert(0, str(layer_dir))

import facet_router
import route_decision_validator
import schema_request_builder
import direction_lens_builder
import situation_context_builder
import human_readable_report_builder

from shared import artifacts as artifact_store
from shared import continuation as continuation_store
from shared import continuation_lifecycle
from shared import fulfillment
from shared import workspace_governance
from shared.run_identity import (
    MAX_RUN_NAME_SLUG_LENGTH,
    RUN_NAME_HASH_LENGTH,
    RUN_TIMESTAMP_FORMAT,
    build_run_id,
    format_run_timestamp,
    sanitize_run_name,
    shorten_run_name_slug,
    unique_run_dir as identity_unique_run_dir,
)


WORKFLOW_VERSION = "0.6.1"
TOOL_ROOT = PROJECT_ROOT

import workspace_cli


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def configure_cli_output() -> None:
    """Keep CLI diagnostics and JSON output Unicode-safe on Windows shells."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            # Embedded callers and already-closed streams may not support reconfiguration.
            continue


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unique_run_dir(base_dir: Path, run_name: str | None, created_at: datetime) -> Path:
    return identity_unique_run_dir(
        base_dir,
        run_name,
        created_at=created_at,
        include_timestamp_for_named=True,
    )


def read_text_argument(text: str | None, input_file: str | None) -> str:
    if text and input_file:
        raise SystemExit("Use either --text or --input-file, not both.")
    if input_file:
        return Path(input_file).read_text(encoding="utf-8-sig").strip()
    if text:
        return text.strip()
    raise SystemExit("Either --text or --input-file is required.")


def ensure_stage_dirs(run_dir: Path, stage_name: str) -> tuple[Path, Path]:
    stage_dir = run_dir / stage_name
    data_dir = stage_dir / "data"
    outputs_dir = stage_dir / "outputs"
    data_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, outputs_dir


def placeholder_json(kind: str, note: str) -> dict[str, Any]:
    return {
        "workflow_placeholder": True,
        "kind": kind,
        "status": "agent_fill_required",
        "note": note,
    }


def is_placeholder_file(path: Path) -> bool:
    if not path.exists() or path.suffix != ".json":
        return False
    try:
        data = load_json(path)
    except Exception:
        return False
    return bool(isinstance(data, dict) and data.get("workflow_placeholder"))


def update_layer_status(manifest: dict[str, Any], layer_id: str, status: str) -> None:
    for layer in manifest.get("layers", []):
        if layer.get("id") == layer_id:
            layer["status"] = status
            return


def load_manifest(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = run_dir / "workflow_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Workflow manifest not found: {manifest_path}")
    return manifest_path, load_json(manifest_path)


def save_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    write_json(manifest_path, manifest)


def build_validation_report(title: str, report: dict[str, Any]) -> str:
    violations = report.get("violations", [])
    rows = [
        "| Code | Severity | Path | Message |",
        "|---|---|---|---|",
    ]
    if violations:
        for item in violations:
            rows.append(
                "| {code} | {severity} | {path} | {message} |".format(
                    code=item.get("code", ""),
                    severity=item.get("severity", ""),
                    path=item.get("path", ""),
                    message=str(item.get("message", "")).replace("|", "\\|"),
                )
            )
    else:
        rows.append("| none | pass | none | No violations |")
    return "\n".join(
        [
            f"# {title}",
            "",
            "## Summary",
            "",
            f"- Valid: `{report.get('valid')}`",
            f"- Severity: `{report.get('severity')}`",
            f"- Fail count: `{report.get('summary', {}).get('fail_count', 0)}`",
            f"- Warn count: `{report.get('summary', {}).get('warn_count', 0)}`",
            "",
            "## Violations",
            "",
            *rows,
            "",
        ]
    )


def build_agent_todo(manifest: dict[str, Any]) -> str:
    paths = manifest["paths"]
    c_activation = manifest["summary"]["c_activation"]
    return "\n".join(
        [
            "# Workflow Agent Todo",
            "",
            "## Purpose",
            "",
            "This workflow folder bundles one user input across the current layer sequence.",
            "Python creates structure and validation contracts. The agent fills the reasoning fields.",
            "",
            "## Stage Order",
            "",
            "1. Fill input structuring request.",
            "2. Fill facet router request and choose one route_decision.",
            "3. Validate the filled router output with route_decision_validator.py.",
            "4. Build and fill direction lens request after the router output is valid.",
            "5. Build, fill, and validate Situation Context Map before handing off to later frameworks.",
            "6. Build the human-readable quality gate report after context validation passes.",
            "7. Build and fill the fulfillment contract from the original requested output.",
            "8. Produce the requested result and register every result artifact.",
            "9. Fill fulfillment evidence and validate it against the contract.",
            "10. Rebuild the human-readable report so it includes final fulfillment evidence.",
            "",
            "## Files To Fill",
            "",
            f"- Input analysis request: `{paths['input_request']}`",
            f"- Input analysis filled target: `{paths['input_filled']}`",
            f"- Router request: `{paths['router_request']}`",
            f"- Router filled target: `{paths['router_filled']}`",
            f"- Direction lens request target: `{paths['direction_request']}`",
            f"- Direction lens filled target: `{paths['direction_filled']}`",
            f"- Situation context request target: `{paths['context_request']}`",
            f"- Situation context filled target: `{paths['context_filled']}`",
            f"- Human-readable report target: `{paths['human_report']}`",
            f"- Human-readable summary target: `{paths['report_summary']}`",
            f"- Fulfillment request target: `{paths['fulfillment_request']}`",
            f"- Fulfillment contract target: `{paths['fulfillment_contract']}`",
            f"- Fulfillment evidence target: `{paths['fulfillment_evidence']}`",
            f"- Fulfillment validation target: `{paths['fulfillment_validation']}`",
            f"- Artifact manifest: `{paths['artifacts_manifest']}`",
            "",
            "## C Activation Summary",
            "",
            f"- Enabled: `{c_activation['enabled']}`",
            f"- Triggered by: `{', '.join(c_activation['triggered_by']) or 'none'}`",
            f"- Activated facets: `{', '.join(c_activation['activated_facets']) or 'none'}`",
            f"- Inactive facets: `{', '.join(c_activation['inactive_facets']) or 'none'}`",
            "",
            "## Validation Commands",
            "",
            "```powershell",
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py validate-route "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py build-direction "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py validate-direction "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py build-context "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py validate-context "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py build-report "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py build-fulfillment "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py validate-fulfillment "
                f"--run-dir \"{manifest['run_dir']}\""
            ),
            "```",
            "",
            "## Artifact Binding",
            "",
            "Any meaningful generated or referenced file must be copied into this run folder or registered in artifacts_manifest.json.",
            "",
            "```powershell",
            (
                "python .\\engine\\python\\workflow\\workflow_runner.py register-artifact "
                f"--run-dir \"{manifest['run_dir']}\" "
                "--artifact-id \"first_character_image\" "
                "--type image "
                "--role generated_output "
                "--path \"<external image path>\" "
                "--source-step \"image_generation\""
            ),
            "```",
            "",
            "## Rules",
            "",
            "- Do not answer the original user request directly inside request JSON files.",
            "- Do not invent missing evidence. Record unresolved or missing basis when needed.",
            "- Fill input analysis before router classification; do not skip the analysis evidence gate.",
            "- Select exactly one route_decision, or mark the route as unresolved.",
            "- High-risk input must not proceed directly to solution.",
            "- Situation Context Map records domain, situation, problem type, missing context, and next focus.",
            "- The human-readable report is a quality gate report, not a new inference layer.",
            "- Do not claim a generated image, prompt, or external file as a workflow artifact unless it is registered in artifacts_manifest.json.",
            "- Completion means the original requested output passed its fulfillment contract; analysis alone is not completion.",
            "- Do not require code, an app, or deployment unless the original request requires it.",
            "",
        ]
    )


def build_workflow(
    text: str,
    output_dir: Path,
    run_name: str | None,
    source_files: list[str],
    *,
    run_id: str | None = None,
    persist_manifest: bool = True,
) -> dict[str, Any]:
    created_at_dt = datetime.now()
    if run_id is not None:
        if Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("run_id must be one safe folder name.")
        output_dir.mkdir(parents=True, exist_ok=True)
        run_dir = output_dir / run_id
    else:
        run_dir = unique_run_dir(output_dir, run_name, created_at_dt)
    run_dir.mkdir(parents=True, exist_ok=False)
    created_at = created_at_dt.isoformat(timespec="seconds")

    source_data_dir, source_outputs_dir = ensure_stage_dirs(run_dir, "00_source")
    input_data_dir, input_outputs_dir = ensure_stage_dirs(run_dir, "01_input_structuring")
    router_data_dir, router_outputs_dir = ensure_stage_dirs(run_dir, "02_router")
    route_validation_data_dir, route_validation_outputs_dir = ensure_stage_dirs(run_dir, "03_route_validation")
    direction_data_dir, direction_outputs_dir = ensure_stage_dirs(run_dir, "04_direction_lens")
    context_data_dir, context_outputs_dir = ensure_stage_dirs(run_dir, "05_situation_context")
    report_stage_dir = run_dir / "06_human_readable_report"
    report_data_dir = report_stage_dir / "data"
    report_reports_dir = report_stage_dir / "reports"
    report_data_dir.mkdir(parents=True, exist_ok=True)
    report_reports_dir.mkdir(parents=True, exist_ok=True)
    fulfillment_data_dir, fulfillment_outputs_dir = ensure_stage_dirs(run_dir, "07_fulfillment")
    artifact_manifest_path, _ = artifact_store.ensure_artifact_store(run_dir, created_at=created_at)

    (source_data_dir / "input.txt").write_text(text + "\n", encoding="utf-8")
    write_json(source_data_dir / "source_files.json", {"source_files": source_files})

    input_request = schema_request_builder.build_request(text, created_at=created_at)
    input_request_path = input_data_dir / "user_input_analysis_request.json"
    input_filled_path = input_data_dir / "user_input_analysis_filled.json"
    write_json(input_request_path, input_request)
    write_json(
        input_filled_path,
        placeholder_json(
            "user_input_analysis_filled",
            "Agent fills this from user_input_analysis_request.json.",
        ),
    )
    (input_outputs_dir / "input_structuring_report.md").write_text(
        schema_request_builder.build_run_report(text, [input_request]),
        encoding="utf-8",
    )

    router_request = facet_router.build_router_request(
        raw_text=text,
        input_analysis_path=str(input_filled_path),
        input_analysis=None,
        source_files=source_files,
        created_at=created_at,
    )
    router_request_path = router_data_dir / "facet_router_request.json"
    router_filled_path = router_data_dir / "facet_router_filled.json"
    write_json(router_request_path, router_request)
    write_json(
        router_filled_path,
        placeholder_json(
            "facet_router_filled",
            "Agent fills facet_classification and route_decision from facet_router_request.json.",
        ),
    )
    (router_outputs_dir / "facet_router_report.md").write_text(
        facet_router.build_report(router_request),
        encoding="utf-8",
    )

    write_json(
        route_validation_data_dir / "route_decision_validation.json",
        placeholder_json(
            "route_decision_validation",
            "Run route_decision_validator.py after facet_router_filled.json is agent-filled.",
        ),
    )
    (route_validation_outputs_dir / "route_validation_status.md").write_text(
        "# Route Validation Status\n\nPending until `facet_router_filled.json` is completed.\n",
        encoding="utf-8",
    )

    direction_request_path = direction_data_dir / "direction_lens_request.json"
    direction_filled_path = direction_data_dir / "direction_lens_filled.json"
    write_json(
        direction_request_path,
        placeholder_json(
            "direction_lens_request",
            "Build this from a validated facet_router_filled.json.",
        ),
    )
    write_json(
        direction_filled_path,
        placeholder_json(
            "direction_lens_filled",
            "Agent fills this after direction_lens_request.json is built.",
        ),
    )
    (direction_outputs_dir / "direction_lens_status.md").write_text(
        "# Direction Lens Status\n\nPending until route decision validation passes.\n",
        encoding="utf-8",
    )

    context_request_path = context_data_dir / "situation_context_request.json"
    context_filled_path = context_data_dir / "situation_context_filled.json"
    context_validation_path = context_data_dir / "situation_context_validation.json"
    write_json(
        context_request_path,
        placeholder_json(
            "situation_context_request",
            "Build this from validated direction lens and router output.",
        ),
    )
    write_json(
        context_filled_path,
        placeholder_json(
            "situation_context_filled",
            "Agent fills this after situation_context_request.json is built.",
        ),
    )
    write_json(
        context_validation_path,
        placeholder_json(
            "situation_context_validation",
            "Run situation_context_builder.py after situation_context_filled.json is agent-filled.",
        ),
    )
    (context_outputs_dir / "situation_context_status.md").write_text(
        "# Situation Context Status\n\nPending until direction lens validation passes.\n",
        encoding="utf-8",
    )

    report_summary_path = report_data_dir / "report_summary.json"
    human_report_path = report_reports_dir / "human_readable_report.md"
    write_json(
        report_summary_path,
        placeholder_json(
            "human_readable_report_summary",
            "Build this after situation context validation passes.",
        ),
    )
    human_report_path.write_text(
        "# Human-Readable Report Status\n\nPending until situation context validation passes.\n",
        encoding="utf-8",
    )

    fulfillment_request_path = fulfillment_data_dir / "contract_request.json"
    fulfillment_contract_path = fulfillment_data_dir / "contract_filled.json"
    fulfillment_evidence_path = fulfillment_data_dir / "evidence_filled.json"
    fulfillment_validation_path = fulfillment_data_dir / "validation.json"
    write_json(
        fulfillment_request_path,
        placeholder_json(
            "fulfillment_contract_request",
            "Build this after the human-readable analysis report is ready.",
        ),
    )
    write_json(
        fulfillment_contract_path,
        placeholder_json(
            "fulfillment_contract_filled",
            "Agent defines the original requested output and observable acceptance criteria.",
        ),
    )
    write_json(
        fulfillment_evidence_path,
        placeholder_json(
            "fulfillment_evidence_filled",
            "Agent records registered output artifacts and criterion evidence after producing the result.",
        ),
    )
    write_json(
        fulfillment_validation_path,
        placeholder_json(
            "fulfillment_validation",
            "Run validate-fulfillment after the contract and evidence are filled.",
        ),
    )
    (fulfillment_outputs_dir / "fulfillment_status.md").write_text(
        "# Fulfillment Status\n\nPending until the analysis report is ready.\n",
        encoding="utf-8",
    )

    manifest = {
        "workflow_version": WORKFLOW_VERSION,
        "run_id": run_dir.name,
        "created_at": created_at,
        "run_dir": str(run_dir),
        "trace": {
            "run_id": run_dir.name,
            "created_at": created_at,
            "created_date": created_at_dt.strftime("%Y-%m-%d"),
            "created_time": created_at_dt.strftime("%H:%M:%S"),
            "run_name": sanitize_run_name(run_name) if run_name else None,
            "run_name_slug": shorten_run_name_slug(run_name) if run_name else None,
            "original_run_name": run_name if run_name else None,
        },
        "source": {
            "raw_text": text,
            "source_files": source_files,
        },
        "layers": [
            {
                "id": "01_input_structuring",
                "status": "agent_fill_required",
                "request_file": str(input_request_path),
                "filled_file": str(input_filled_path),
            },
            {
                "id": "02_router",
                "status": "agent_fill_required",
                "request_file": str(router_request_path),
                "filled_file": str(router_filled_path),
            },
            {
                "id": "03_route_validation",
                "status": "pending_router_filled",
                "validation_file": str(route_validation_data_dir / "route_decision_validation.json"),
            },
            {
                "id": "04_direction_lens",
                "status": "pending_validated_route",
                "request_file": str(direction_request_path),
                "filled_file": str(direction_filled_path),
            },
            {
                "id": "05_situation_context",
                "status": "pending_validated_direction",
                "request_file": str(context_request_path),
                "filled_file": str(context_filled_path),
                "validation_file": str(context_validation_path),
            },
            {
                "id": "06_human_readable_report",
                "status": "pending_validated_context",
                "report_file": str(human_report_path),
                "summary_file": str(report_summary_path),
            },
            {
                "id": "07_fulfillment",
                "status": "pending_human_report",
                "request_file": str(fulfillment_request_path),
                "contract_file": str(fulfillment_contract_path),
                "evidence_file": str(fulfillment_evidence_path),
                "validation_file": str(fulfillment_validation_path),
            },
        ],
        "paths": {
            "input_request": str(input_request_path),
            "input_filled": str(input_filled_path),
            "router_request": str(router_request_path),
            "router_filled": str(router_filled_path),
            "route_validation": str(route_validation_data_dir / "route_decision_validation.json"),
            "direction_request": str(direction_request_path),
            "direction_filled": str(direction_filled_path),
            "context_request": str(context_request_path),
            "context_filled": str(context_filled_path),
            "context_validation": str(context_validation_path),
            "human_report": str(human_report_path),
            "report_summary": str(report_summary_path),
            "fulfillment_request": str(fulfillment_request_path),
            "fulfillment_contract": str(fulfillment_contract_path),
            "fulfillment_evidence": str(fulfillment_evidence_path),
            "fulfillment_validation": str(fulfillment_validation_path),
            "assets_root": str(run_dir / "assets"),
            "artifacts_manifest": str(artifact_manifest_path),
        },
        "summary": {
            "c_activation": router_request["c_activation"],
            "next_required_action": "Agent must fill input and router JSON files before validation.",
            "artifact_binding": {
                "assets_root": str(run_dir / "assets"),
                "artifacts_manifest": str(artifact_manifest_path),
                "rule": "Official workflow artifacts must be registered in artifacts_manifest.json.",
            },
        },
    }

    if persist_manifest:
        write_json(run_dir / "workflow_manifest.json", manifest)
    (run_dir / "agent_todo.md").write_text(build_agent_todo(manifest), encoding="utf-8")
    write_json(source_outputs_dir / "workflow_source_manifest.json", manifest["source"])
    return manifest


def inspect_workflow(run_dir: Path) -> dict[str, Any]:
    _, manifest = load_manifest(run_dir)
    file_status = {}
    for key, path in manifest.get("paths", {}).items():
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = workflow_path(manifest, key)
        file_status[key] = {
            "path": str(candidate),
            "exists": candidate.exists(),
            "placeholder": False,
        }
        if candidate.exists() and candidate.suffix == ".json":
            try:
                data = load_json(candidate)
            except Exception:
                data = None
            file_status[key]["placeholder"] = bool(
                isinstance(data, dict) and data.get("workflow_placeholder")
            )
        elif candidate.exists() and candidate.suffix.casefold() in {".md", ".txt"}:
            try:
                content = candidate.read_text(encoding="utf-8-sig")
            except Exception:
                content = ""
            file_status[key]["placeholder"] = "pending until" in content.casefold()
    return {
        "run_dir": str(run_dir),
        "file_status": file_status,
        "layers": manifest.get("layers", []),
        "summary": manifest.get("summary", {}),
    }


def workflow_path(manifest: dict[str, Any], key: str) -> Path:
    path = Path(manifest["paths"][key])
    if path.is_absolute():
        return path
    declared_root = manifest.get("project_root_absolute")
    if declared_root:
        return Path(str(declared_root)) / path
    manifest_run_dir = manifest.get("run_dir")
    if manifest_run_dir:
        run_dir = Path(str(manifest_run_dir))
        if run_dir.is_absolute():
            if run_dir.name in path.parts:
                run_id_index = len(path.parts) - 1 - list(reversed(path.parts)).index(run_dir.name)
                return run_dir.joinpath(*path.parts[run_id_index + 1 :])
            for candidate_root in run_dir.parents:
                candidate = candidate_root / path
                if candidate.exists():
                    return candidate
    return PROJECT_ROOT / path


def validation_codes(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return []
    codes: list[str] = []
    for item in report.get("violations", []):
        if isinstance(item, dict) and item.get("code"):
            codes.append(str(item["code"]))
    return codes


def attach_validation_error_surface(
    manifest: dict[str, Any],
    report: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any]:
    """Add a versioned diagnostic classification while preserving the raw report."""
    summary = manifest.setdefault("summary", {})
    if report.get("valid") is False:
        error_surface = workspace_governance.classify_error_surface(
            stage=stage,
            validation_result=report,
        )
        report["error_surface"] = error_surface
        summary["error_surface"] = error_surface
    else:
        summary.pop("error_surface", None)
    return report


def validation_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "placeholder": False,
            "valid": None,
            "severity": "missing",
            "validation_codes": [],
        }
    if is_placeholder_file(path):
        return {
            "exists": True,
            "placeholder": True,
            "valid": None,
            "severity": "placeholder",
            "validation_codes": [],
        }
    try:
        report = load_json(path)
    except Exception as exc:
        return {
            "exists": True,
            "placeholder": False,
            "valid": None,
            "severity": "unreadable",
            "error": str(exc),
            "validation_codes": [],
        }
    return {
        "exists": True,
        "placeholder": False,
        "valid": bool(report.get("valid")),
        "severity": report.get("severity", ""),
        "summary": report.get("summary", {}),
        "validation_codes": validation_codes(report),
    }


def command_text(command: str, run_dir: Path) -> str:
    return f'python .\\engine\\python\\workflow\\workflow_runner.py {command} --run-dir "{run_dir}"'


def next_action(
    action_type: str,
    stage: str,
    reason: str,
    *,
    target_file: Path | None = None,
    then_run: str | None = None,
    validation_codes_: list[str] | None = None,
    source_file: Path | None = None,
    direction_next_action: Any | None = None,
    context_next_action: Any | None = None,
    context_map_summary: Any | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": action_type,
        "stage": stage,
        "reason": reason,
    }
    if target_file is not None:
        action["target_file"] = str(target_file)
    if then_run is not None:
        action["then_run"] = then_run
    if validation_codes_:
        action["validation_codes"] = validation_codes_
    if source_file is not None:
        action["source_file"] = str(source_file)
    if direction_next_action is not None:
        action["direction_next_action"] = direction_next_action
    if context_next_action is not None:
        action["context_next_action"] = context_next_action
    if context_map_summary is not None:
        action["context_map_summary"] = context_map_summary
    return action


def build_workflow_status(run_dir: Path) -> dict[str, Any]:
    _, manifest = load_manifest(run_dir)
    inspection = inspect_workflow(run_dir)
    files = inspection["file_status"]
    warnings: list[dict[str, Any]] = []
    artifact_status = artifact_store.inspect_artifacts(run_dir)
    continuation_path = continuation_store.continuation_state_path(run_dir)
    continuation_linked = "continuation_state" in manifest.get("paths", {})
    continuation_exists = continuation_path.exists()
    if continuation_linked or continuation_exists:
        continuation_status = inspect_continuation_for_workflow(run_dir)
    else:
        continuation_status = {
            "state_file": str(continuation_path),
            "exists": False,
            "valid": None,
            "errors": [],
            "state": None,
        }

    if "artifacts_manifest" in manifest.get("paths", {}) and not artifact_status["manifest_exists"]:
        warnings.append(
            {
                "code": "ARTIFACT_MANIFEST_MISSING",
                "severity": "warn",
                "path": artifact_status["manifest_file"],
                "message": "artifacts_manifest.json is missing, so external generated files cannot be traced.",
            }
        )
    if artifact_status["missing_count"] > 0:
        warnings.append(
            {
                "code": "ARTIFACT_FILE_MISSING",
                "severity": "warn",
                "path": artifact_status["manifest_file"],
                "message": "One or more registered artifacts do not resolve to existing files.",
            }
        )
    if continuation_exists and not continuation_linked:
        warnings.append(
            {
                "code": "CONTINUATION_STATE_UNLINKED",
                "severity": "warn",
                "path": str(continuation_path),
                "message": "continuation_state.json exists but workflow_manifest.json does not link to it.",
            }
        )
    if (continuation_linked or continuation_exists) and continuation_status.get("valid") is not True:
        warnings.append(
            {
                "code": "CONTINUATION_STATE_INVALID",
                "severity": "warn",
                "path": continuation_status["state_file"],
                "message": "Continuation state is missing or invalid; inspect continuation_status.errors.",
            }
        )

    input_filled_path = workflow_path(manifest, "input_filled")
    router_filled_path = workflow_path(manifest, "router_filled")
    route_validation_path = workflow_path(manifest, "route_validation")
    direction_request_path = workflow_path(manifest, "direction_request")
    direction_filled_path = workflow_path(manifest, "direction_filled")
    direction_validation_path = run_dir / "04_direction_lens" / "data" / "direction_lens_validation.json"
    has_context_layer = "context_request" in manifest.get("paths", {})
    context_request_path = workflow_path(manifest, "context_request") if has_context_layer else None
    context_filled_path = workflow_path(manifest, "context_filled") if has_context_layer else None
    context_validation_path = workflow_path(manifest, "context_validation") if has_context_layer else None
    has_report_layer = "report_summary" in manifest.get("paths", {})
    report_summary_path = workflow_path(manifest, "report_summary") if has_report_layer else None
    human_report_path = workflow_path(manifest, "human_report") if has_report_layer else None
    has_fulfillment_layer = "fulfillment_contract" in manifest.get("paths", {})
    fulfillment_request_path = workflow_path(manifest, "fulfillment_request") if has_fulfillment_layer else None
    fulfillment_contract_path = workflow_path(manifest, "fulfillment_contract") if has_fulfillment_layer else None
    fulfillment_evidence_path = workflow_path(manifest, "fulfillment_evidence") if has_fulfillment_layer else None
    fulfillment_validation_path = (
        workflow_path(manifest, "fulfillment_validation")
        if has_fulfillment_layer
        else None
    )

    if files.get("input_filled", {}).get("placeholder", True):
        warnings.append(
            {
                "code": "INPUT_ANALYSIS_NOT_FILLED",
                "severity": "warn",
                "path": str(input_filled_path),
                "message": (
                    "user_input_analysis_filled.json is still a placeholder. "
                    "Fill it before router classification so the evidence chain remains complete."
                ),
            }
        )

    route_validation = validation_summary(route_validation_path)
    direction_validation = validation_summary(direction_validation_path)
    context_validation = (
        validation_summary(context_validation_path)
        if context_validation_path is not None
        else {
            "exists": False,
            "placeholder": False,
            "valid": None,
            "severity": "not_configured",
            "validation_codes": [],
        }
    )

    fulfillment_validation = (
        validation_summary(fulfillment_validation_path)
        if fulfillment_validation_path is not None
        else {
            "exists": False,
            "placeholder": False,
            "valid": None,
            "severity": "not_configured",
            "validation_codes": [],
        }
    )
    fulfillment_contract: dict[str, Any] = {}
    if (
        fulfillment_contract_path is not None
        and fulfillment_contract_path.exists()
        and not is_placeholder_file(fulfillment_contract_path)
    ):
        try:
            fulfillment_contract = load_json(fulfillment_contract_path)
        except Exception:
            fulfillment_contract = {}
    contract_status = str(fulfillment_contract.get("contract_status") or "")
    needs_user_input = fulfillment_contract.get("needs_user_input")
    if not isinstance(needs_user_input, dict):
        needs_user_input = {}

    if files.get("input_filled", {}).get("placeholder", True):
        state = "input_analysis_required"
        current_stage = "01_input_structuring"
        action = next_action(
            "fill_input_analysis",
            current_stage,
            "Input analysis is still a placeholder, so evidence-backed routing cannot start.",
            target_file=input_filled_path,
        )
    elif files.get("router_filled", {}).get("placeholder", True):
        state = "router_filled_required"
        current_stage = "02_router"
        action = next_action(
            "fill_router",
            current_stage,
            "facet_router_filled.json is still a placeholder, so route validation cannot run yet.",
            target_file=router_filled_path,
            then_run=command_text("validate-route", run_dir),
        )
    elif not route_validation["exists"] or route_validation["placeholder"]:
        state = "route_validation_required"
        current_stage = "03_route_validation"
        action = next_action(
            "validate_route",
            current_stage,
            "Router filled JSON exists, but route validation has not been run.",
            target_file=route_validation_path,
            then_run=command_text("validate-route", run_dir),
        )
    elif route_validation["valid"] is not True:
        state = "route_validation_failed"
        current_stage = "02_router"
        action = next_action(
            "fix_router_filled",
            current_stage,
            "Route validation failed. Fix the router filled JSON using validation violations.",
            target_file=router_filled_path,
            then_run=command_text("validate-route", run_dir),
            validation_codes_=route_validation.get("validation_codes", []),
        )
    elif files.get("direction_request", {}).get("placeholder", True):
        state = "direction_request_required"
        current_stage = "04_direction_lens"
        action = next_action(
            "build_direction",
            current_stage,
            "Route validation passed, but direction_lens_request.json is still a placeholder.",
            target_file=direction_request_path,
            then_run=command_text("build-direction", run_dir),
        )
    elif files.get("direction_filled", {}).get("placeholder", True):
        state = "direction_filled_required"
        current_stage = "04_direction_lens"
        action = next_action(
            "fill_direction",
            current_stage,
            "direction_lens_filled.json is still a placeholder, so direction validation cannot run yet.",
            target_file=direction_filled_path,
            then_run=command_text("validate-direction", run_dir),
        )
    elif not direction_validation["exists"] or direction_validation["placeholder"]:
        state = "direction_validation_required"
        current_stage = "04_direction_lens"
        action = next_action(
            "validate_direction",
            current_stage,
            "Direction filled JSON exists, but direction validation has not been run.",
            target_file=direction_validation_path,
            then_run=command_text("validate-direction", run_dir),
        )
    elif direction_validation["valid"] is not True:
        state = "direction_validation_failed"
        current_stage = "04_direction_lens"
        action = next_action(
            "fix_direction_filled",
            current_stage,
            "Direction validation failed. Fix the direction lens filled JSON using validation violations.",
            target_file=direction_filled_path,
            then_run=command_text("validate-direction", run_dir),
            validation_codes_=direction_validation.get("validation_codes", []),
        )
    elif has_context_layer and files.get("context_request", {}).get("placeholder", True):
        state = "context_request_required"
        current_stage = "05_situation_context"
        action = next_action(
            "build_context",
            current_stage,
            "Direction validation passed, but situation_context_request.json is still a placeholder.",
            target_file=context_request_path,
            then_run=command_text("build-context", run_dir),
        )
    elif has_context_layer and files.get("context_filled", {}).get("placeholder", True):
        state = "context_filled_required"
        current_stage = "05_situation_context"
        action = next_action(
            "fill_context",
            current_stage,
            "situation_context_filled.json is still a placeholder, so context validation cannot run yet.",
            target_file=context_filled_path,
            then_run=command_text("validate-context", run_dir),
        )
    elif has_context_layer and (not context_validation["exists"] or context_validation["placeholder"]):
        state = "context_validation_required"
        current_stage = "05_situation_context"
        action = next_action(
            "validate_context",
            current_stage,
            "Situation context filled JSON exists, but context validation has not been run.",
            target_file=context_validation_path,
            then_run=command_text("validate-context", run_dir),
        )
    elif has_context_layer and context_validation["valid"] is not True:
        state = "context_validation_failed"
        current_stage = "05_situation_context"
        action = next_action(
            "fix_context_filled",
            current_stage,
            "Situation context validation failed. Fix the context filled JSON using validation violations.",
            target_file=context_filled_path,
            then_run=command_text("validate-context", run_dir),
            validation_codes_=context_validation.get("validation_codes", []),
        )
    elif has_report_layer and (
        files.get("report_summary", {}).get("placeholder", True)
        or human_report_path is None
        or not human_report_path.exists()
    ):
        state = "human_report_required"
        current_stage = "06_human_readable_report"
        action = next_action(
            "build_human_report",
            current_stage,
            "Situation context validation passed. Build the human-readable quality gate report.",
            target_file=human_report_path,
            then_run=command_text("build-report", run_dir),
        )
    elif has_fulfillment_layer and files.get("fulfillment_request", {}).get("placeholder", True):
        state = "fulfillment_request_required"
        current_stage = "07_fulfillment"
        action = next_action(
            "build_fulfillment",
            current_stage,
            "Analysis is ready. Build the contract request for the original requested output.",
            target_file=fulfillment_request_path,
            then_run=command_text("build-fulfillment", run_dir),
        )
    elif has_fulfillment_layer and files.get("fulfillment_contract", {}).get("placeholder", True):
        state = "fulfillment_contract_required"
        current_stage = "07_fulfillment"
        action = next_action(
            "fill_fulfillment_contract",
            current_stage,
            "Define the concrete requested output, acceptance criteria, and artifact policy.",
            target_file=fulfillment_contract_path,
        )
    elif has_fulfillment_layer and (
        contract_status in {"waiting_user", "blocked"}
        or needs_user_input.get("required") is True
    ):
        state = "waiting_user"
        current_stage = "07_fulfillment"
        action = next_action(
            "collect_required_user_input",
            current_stage,
            "The fulfillment contract records a material decision that must be supplied before output generation.",
            target_file=fulfillment_contract_path,
        )
    elif (
        has_fulfillment_layer
        and contract_status == "ready"
        and files.get("fulfillment_evidence", {}).get("placeholder", True)
    ):
        state = "fulfillment_evidence_required"
        current_stage = "07_fulfillment"
        action = next_action(
            "produce_and_register_requested_output",
            current_stage,
            "Produce the original requested result, register its artifacts, then fill fulfillment evidence.",
            target_file=fulfillment_evidence_path,
            then_run=command_text("validate-fulfillment", run_dir),
        )
    elif has_fulfillment_layer and (
        not fulfillment_validation["exists"] or fulfillment_validation["placeholder"]
    ):
        state = "fulfillment_validation_required"
        current_stage = "07_fulfillment"
        action = next_action(
            "validate_fulfillment",
            current_stage,
            "Validate the declared result and registered evidence against the original request contract.",
            target_file=fulfillment_validation_path,
            then_run=command_text("validate-fulfillment", run_dir),
        )
    elif has_fulfillment_layer and fulfillment_validation["valid"] is not True:
        state = "fulfillment_validation_failed"
        current_stage = "07_fulfillment"
        action = next_action(
            "fix_fulfillment_contract_or_evidence",
            current_stage,
            "Fulfillment validation failed. Fix the contract, result artifacts, or evidence without weakening the original request.",
            target_file=fulfillment_contract_path,
            then_run=command_text("validate-fulfillment", run_dir),
            validation_codes_=fulfillment_validation.get("validation_codes", []),
        )
    elif has_fulfillment_layer:
        state = "request_completed"
        current_stage = "completed"
        action = next_action(
            "none",
            current_stage,
            "The original requested output passed its fulfillment contract.",
            source_file=human_report_path,
        )
    else:
        if has_context_layer and context_filled_path is not None:
            context_filled = load_json(context_filled_path)
            context_map = context_filled.get("situation_context_map", {})
            source_file = human_report_path if human_report_path is not None and human_report_path.exists() else context_filled_path
            context_next = context_filled.get("next_action")
            context_summary = {
                "central_problem": context_map.get("central_problem"),
                "domain_area": context_map.get("domain_area"),
                "problem_type": context_map.get("problem_type"),
                "situation_phase": context_map.get("situation_phase"),
                "recommended_next_focus": context_map.get("recommended_next_focus", []),
            }
            handoff_reason = "Quality gate report is ready. Follow situation_context_filled.next_action."
        else:
            direction_filled = load_json(direction_filled_path)
            source_file = direction_filled_path
            context_next = None
            context_summary = None
            handoff_reason = "Direction lens validation passed. Follow direction_lens_filled.next_action."
        state = "ready_for_next_action"
        current_stage = "handoff"
        action = next_action(
            "handoff_to_next_action",
            current_stage,
            handoff_reason,
            source_file=source_file,
            context_next_action=context_next,
            context_map_summary=context_summary,
        )

    continuation_projection: dict[str, Any] | None = None
    if continuation_status.get("valid") is True and isinstance(continuation_status.get("state"), dict):
        projection_state = continuation_status["state"]
        if projection_state.get("continuation_state_version") == continuation_lifecycle.LEGACY_VERSION:
            projection_state = continuation_lifecycle.upgrade_legacy_state(projection_state, run_dir)
        continuation_projection = continuation_lifecycle.workflow_projection(
            projection_state,
            continuation_status["state_file"],
        )
        state = continuation_projection["workflow_state"]
        current_stage = continuation_projection["current_stage"]
        action = continuation_projection["next_action"]

    dynamic_summary = dict(manifest.get("summary") or {})
    next_action_type = str(action.get("type") or "unknown")
    dynamic_summary["workflow_state"] = state
    dynamic_summary["next_required_action"] = (
        "none" if next_action_type == "none" else str(action.get("reason") or next_action_type)
    )
    dynamic_summary["fulfillment_status"] = {
        "configured": has_fulfillment_layer,
        "contract_status": contract_status or ("pending" if has_fulfillment_layer else "legacy_untracked"),
    }

    status = {
        "status_version": WORKFLOW_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "workflow_state": state,
        "current_stage": current_stage,
        "next_action": action,
        "analysis_state": (
            "required" if files.get("input_filled", {}).get("placeholder", True) else "complete"
        ),
        "fulfillment_status": {
            "configured": has_fulfillment_layer,
            "contract_status": contract_status or ("pending" if has_fulfillment_layer else "legacy_untracked"),
        },
        "warnings": warnings,
        "file_status": files,
        "artifact_status": artifact_status,
        "continuation_status": continuation_status,
        "continuation_projection": continuation_projection,
        "validation_status": {
            "route_validation": route_validation,
            "direction_validation": direction_validation,
            "context_validation": context_validation,
            "fulfillment_validation": fulfillment_validation,
        },
        "layers": manifest.get("layers", []),
        "summary": dynamic_summary,
    }
    return status


def write_workflow_status_files(run_dir: Path, status: dict[str, Any]) -> tuple[Path, Path]:
    status_path = run_dir / "workflow_status.json"
    next_path = run_dir / "workflow_next.json"
    write_json(status_path, status)
    write_json(
        next_path,
        {
            "status_version": status["status_version"],
            "generated_at": status["generated_at"],
            "run_dir": status["run_dir"],
            "workflow_state": status["workflow_state"],
            "current_stage": status["current_stage"],
            "next_action": status["next_action"],
            "analysis_state": status.get("analysis_state"),
            "fulfillment_status": status.get("fulfillment_status", {}),
            "warnings": status["warnings"],
            "artifact_status": status.get("artifact_status", {}),
            "continuation_status": status.get("continuation_status", {}),
            "continuation_projection": status.get("continuation_projection"),
        },
    )
    return status_path, next_path


def command_status(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    status = build_workflow_status(run_dir)
    write_workflow_status_files(run_dir, status)
    print(to_json(status))
    return 0


def command_next(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    status = build_workflow_status(run_dir)
    write_workflow_status_files(run_dir, status)
    print(
        to_json(
            {
                "status_version": status["status_version"],
                "generated_at": status["generated_at"],
                "run_dir": status["run_dir"],
                "workflow_state": status["workflow_state"],
                "current_stage": status["current_stage"],
                "next_action": status["next_action"],
                "analysis_state": status.get("analysis_state"),
                "fulfillment_status": status.get("fulfillment_status", {}),
                "warnings": status["warnings"],
                "artifact_status": status.get("artifact_status", {}),
                "continuation_status": status.get("continuation_status", {}),
            }
        )
    )
    return 0


def command_validate_route(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path, manifest = load_manifest(run_dir)
    router_request_path = Path(manifest["paths"]["router_request"])
    router_filled_path = Path(manifest["paths"]["router_filled"])
    if is_placeholder_file(router_filled_path):
        raise SystemExit(f"Router filled file is still a placeholder: {router_filled_path}")

    router_request = load_json(router_request_path)
    router_filled = load_json(router_filled_path)
    report = route_decision_validator.validate_route_decision(router_request, router_filled)
    attach_validation_error_surface(manifest, report, stage="route_validation")

    validation_path = Path(manifest["paths"]["route_validation"])
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(validation_path, report)
    report_path = run_dir / "03_route_validation" / "outputs" / "route_decision_validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_validation_report("Route Decision Validation Report", report), encoding="utf-8")

    update_layer_status(manifest, "03_route_validation", "valid" if report["valid"] else "invalid")
    manifest["summary"]["route_validation_valid"] = report["valid"]
    manifest["summary"]["route_validation_file"] = str(validation_path)
    manifest["summary"]["route_validation_report"] = str(report_path)
    save_manifest(manifest_path, manifest)
    print(to_json({"validation_file": str(validation_path), "report_file": str(report_path), "result": report}))
    return 0 if report["valid"] else 1


def command_build_direction(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path, manifest = load_manifest(run_dir)
    validation_path = Path(manifest["paths"]["route_validation"])
    if not args.allow_invalid:
        if is_placeholder_file(validation_path) or not validation_path.exists():
            raise SystemExit("Route validation must be run before building direction lens request.")
        validation = load_json(validation_path)
        if not validation.get("valid"):
            raise SystemExit("Route validation is not valid. Use --allow-invalid only for debugging.")

    router_filled_path = Path(manifest["paths"]["router_filled"])
    if is_placeholder_file(router_filled_path):
        raise SystemExit(f"Router filled file is still a placeholder: {router_filled_path}")

    router_filled = load_json(router_filled_path)
    direction_request = direction_lens_builder.build_direction_request(router_filled)
    direction_request_path = Path(manifest["paths"]["direction_request"])
    direction_request_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(direction_request_path, direction_request)

    direction_outputs_dir = run_dir / "04_direction_lens" / "outputs"
    direction_outputs_dir.mkdir(parents=True, exist_ok=True)
    (direction_outputs_dir / "direction_lens_request_status.md").write_text(
        "# Direction Lens Request\n\nDirection lens request was built from validated router output.\n",
        encoding="utf-8",
    )

    update_layer_status(manifest, "04_direction_lens", "agent_fill_required")
    manifest["summary"]["direction_request_file"] = str(direction_request_path)
    save_manifest(manifest_path, manifest)
    print(to_json({"direction_request_file": str(direction_request_path), "status": "agent_fill_required"}))
    return 0


def command_validate_direction(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path, manifest = load_manifest(run_dir)
    direction_request_path = Path(manifest["paths"]["direction_request"])
    direction_filled_path = Path(manifest["paths"]["direction_filled"])
    if is_placeholder_file(direction_request_path):
        raise SystemExit(f"Direction request file is still a placeholder: {direction_request_path}")
    if is_placeholder_file(direction_filled_path):
        raise SystemExit(f"Direction filled file is still a placeholder: {direction_filled_path}")

    direction_request = load_json(direction_request_path)
    direction_filled = load_json(direction_filled_path)
    report = direction_lens_builder.validate_direction_lens(direction_request, direction_filled)
    attach_validation_error_surface(manifest, report, stage="direction_validation")

    validation_path = run_dir / "04_direction_lens" / "data" / "direction_lens_validation.json"
    write_json(validation_path, report)
    report_path = run_dir / "04_direction_lens" / "outputs" / "direction_lens_validation_report.md"
    report_path.write_text(build_validation_report("Direction Lens Validation Report", report), encoding="utf-8")

    update_layer_status(manifest, "04_direction_lens", "valid" if report["valid"] else "invalid")
    manifest["summary"]["direction_validation_valid"] = report["valid"]
    manifest["summary"]["direction_validation_file"] = str(validation_path)
    manifest["summary"]["direction_validation_report"] = str(report_path)
    save_manifest(manifest_path, manifest)
    print(to_json({"validation_file": str(validation_path), "report_file": str(report_path), "result": report}))
    return 0 if report["valid"] else 1


def command_build_context(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path, manifest = load_manifest(run_dir)
    if "context_request" not in manifest.get("paths", {}):
        raise SystemExit("This workflow run does not include the situation context layer.")

    direction_validation_path = run_dir / "04_direction_lens" / "data" / "direction_lens_validation.json"
    if not args.allow_invalid:
        if is_placeholder_file(direction_validation_path) or not direction_validation_path.exists():
            raise SystemExit("Direction validation must be run before building situation context request.")
        validation = load_json(direction_validation_path)
        if not validation.get("valid"):
            raise SystemExit("Direction validation is not valid. Use --allow-invalid only for debugging.")

    router_filled_path = Path(manifest["paths"]["router_filled"])
    direction_filled_path = Path(manifest["paths"]["direction_filled"])
    if is_placeholder_file(router_filled_path):
        raise SystemExit(f"Router filled file is still a placeholder: {router_filled_path}")
    if is_placeholder_file(direction_filled_path):
        raise SystemExit(f"Direction filled file is still a placeholder: {direction_filled_path}")

    router_filled = load_json(router_filled_path)
    direction_filled = load_json(direction_filled_path)
    context_request = situation_context_builder.build_context_request(router_filled, direction_filled)
    context_request_path = Path(manifest["paths"]["context_request"])
    context_request_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(context_request_path, context_request)

    context_outputs_dir = run_dir / "05_situation_context" / "outputs"
    context_outputs_dir.mkdir(parents=True, exist_ok=True)
    (context_outputs_dir / "situation_context_request_status.md").write_text(
        "# Situation Context Request\n\nSituation Context Map request was built from validated router and direction output.\n",
        encoding="utf-8",
    )

    update_layer_status(manifest, "05_situation_context", "agent_fill_required")
    manifest["summary"]["context_request_file"] = str(context_request_path)
    save_manifest(manifest_path, manifest)
    print(to_json({"context_request_file": str(context_request_path), "status": "agent_fill_required"}))
    return 0


def command_validate_context(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path, manifest = load_manifest(run_dir)
    if "context_request" not in manifest.get("paths", {}):
        raise SystemExit("This workflow run does not include the situation context layer.")

    context_request_path = Path(manifest["paths"]["context_request"])
    context_filled_path = Path(manifest["paths"]["context_filled"])
    if is_placeholder_file(context_request_path):
        raise SystemExit(f"Situation context request file is still a placeholder: {context_request_path}")
    if is_placeholder_file(context_filled_path):
        raise SystemExit(f"Situation context filled file is still a placeholder: {context_filled_path}")

    context_request = load_json(context_request_path)
    context_filled = load_json(context_filled_path)
    report = situation_context_builder.validate_situation_context(context_request, context_filled)
    attach_validation_error_surface(manifest, report, stage="context_validation")

    validation_path = Path(manifest["paths"]["context_validation"])
    write_json(validation_path, report)
    report_path = run_dir / "05_situation_context" / "outputs" / "situation_context_validation_report.md"
    report_path.write_text(build_validation_report("Situation Context Validation Report", report), encoding="utf-8")

    update_layer_status(manifest, "05_situation_context", "valid" if report["valid"] else "invalid")
    manifest["summary"]["context_validation_valid"] = report["valid"]
    manifest["summary"]["context_validation_file"] = str(validation_path)
    manifest["summary"]["context_validation_report"] = str(report_path)
    save_manifest(manifest_path, manifest)
    print(to_json({"validation_file": str(validation_path), "report_file": str(report_path), "result": report}))
    return 0 if report["valid"] else 1


def build_human_report_status(status: dict[str, Any]) -> dict[str, Any]:
    report_status = dict(status)
    context_status = report_status.get("validation_status", {}).get("context_validation", {})
    if context_status.get("valid") is True and not report_status.get("continuation_projection"):
        configured = report_status.get("fulfillment_status", {}).get("configured") is True
        if configured and report_status.get("workflow_state") == "human_report_required":
            report_status["workflow_state"] = "analysis_ready"
            report_status["current_stage"] = "07_fulfillment"
        elif not configured:
            report_status["workflow_state"] = "ready_for_next_action"
            report_status["current_stage"] = "handoff"
    return report_status


def build_human_report_for_workflow(run_dir: Path, *, allow_incomplete: bool = False) -> dict[str, Any]:
    manifest_path, manifest = load_manifest(run_dir)
    status = build_workflow_status(run_dir)
    context_status = status.get("validation_status", {}).get("context_validation", {})
    if not allow_incomplete and context_status.get("valid") is not True:
        raise SystemExit("Situation context validation must pass before building the human-readable report.")

    report_status = build_human_report_status(status)
    report_manifest = human_readable_report_builder.build_report_files(run_dir, status=report_status)
    update_layer_status(manifest, "06_human_readable_report", "ready")
    manifest["summary"]["human_report_file"] = report_manifest["report_file"]
    manifest["summary"]["human_report_summary_file"] = report_manifest["summary_file"]
    manifest["summary"]["human_report_quality_gate"] = report_manifest["quality_gate"]
    save_manifest(manifest_path, manifest)
    return report_manifest


def command_build_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    report_manifest = build_human_report_for_workflow(run_dir, allow_incomplete=args.allow_incomplete)
    print(to_json(report_manifest))
    return 0


def fulfillment_request_scope(manifest: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    supplemental_inputs = [
        item
        for item in manifest.get("supplemental_inputs", [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if supplemental_inputs:
        latest = supplemental_inputs[-1]
        input_hash = latest.get("input_hash") if isinstance(latest.get("input_hash"), dict) else {}
        binding: dict[str, Any] = {
            "scope": "continuation",
            "operation_id": str(latest.get("operation_id") or ""),
            "supplemental_input_id": str(latest.get("supplemental_input_id") or ""),
            "input_hash": str(input_hash.get("value") or ""),
        }
        request_source = latest.get("request_source")
        if isinstance(request_source, dict):
            binding["request_source"] = dict(request_source)
        return str(latest["text"]).strip(), binding

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    input_hash = manifest.get("input_hash") if isinstance(manifest.get("input_hash"), dict) else {}
    return str(source.get("raw_text") or "").strip(), {
        "scope": "initial",
        "operation_id": str(manifest.get("operation_id") or ""),
        "supplemental_input_id": "",
        "input_hash": str(input_hash.get("value") or ""),
    }



def command_build_fulfillment(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path, manifest = load_manifest(run_dir)
    if "fulfillment_request" not in manifest.get("paths", {}):
        raise SystemExit("This workflow run does not include the fulfillment layer.")
    report_summary_path = workflow_path(manifest, "report_summary")
    if is_placeholder_file(report_summary_path) or not report_summary_path.exists():
        raise SystemExit("Build the human-readable analysis report before the fulfillment contract request.")

    router_filled = load_json(workflow_path(manifest, "router_filled"))
    needed_output_slot = router_filled.get("facet_classification", {}).get("needed_output", {})
    needed_output = (
        str(needed_output_slot.get("value") or "").strip()
        if isinstance(needed_output_slot, dict)
        else str(needed_output_slot or "").strip()
    )
    context_next_action: dict[str, Any] = {}
    context_path = workflow_path(manifest, "context_filled")
    if context_path.exists() and not is_placeholder_file(context_path):
        context_data = load_json(context_path)
        value = context_data.get("next_action")
        if isinstance(value, dict):
            context_next_action = value
    active_request, request_binding = fulfillment_request_scope(manifest)
    request = fulfillment.build_contract_request(
        raw_text=active_request,
        needed_output=needed_output,
        context_next_action=context_next_action,
        request_binding=request_binding,
    )
    request_path = workflow_path(manifest, "fulfillment_request")
    previous_request = load_json(request_path) if request_path.exists() and not is_placeholder_file(request_path) else {}
    request_changed = (
        previous_request.get("request_binding") != request_binding
        or (previous_request.get("source") or {}).get("original_request") != active_request
    )
    write_json(request_path, request)
    if request_changed:
        write_json(
            workflow_path(manifest, "fulfillment_contract"),
            placeholder_json(
                "fulfillment_contract_filled",
                "The active request changed. Rebuild the contract and copy request_binding exactly.",
            ),
        )
        write_json(
            workflow_path(manifest, "fulfillment_evidence"),
            placeholder_json(
                "fulfillment_evidence_filled",
                "The active request changed. Rebind evidence to the new request_binding.",
            ),
        )
        write_json(
            workflow_path(manifest, "fulfillment_validation"),
            placeholder_json(
                "fulfillment_validation",
                "The active request changed. Validate the rebuilt contract and evidence.",
            ),
        )
    update_layer_status(manifest, "07_fulfillment", "agent_fill_required")
    manifest.setdefault("summary", {})["fulfillment"] = {
        "request_file": str(request_path),
        "request_binding": request_binding,
        "rule": "The latest bound request must pass this contract before completion.",
    }
    save_manifest(manifest_path, manifest)
    print(to_json({"fulfillment_request_file": str(request_path), "status": "agent_fill_required"}))
    return 0


def command_validate_fulfillment(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path, manifest = load_manifest(run_dir)
    if "fulfillment_contract" not in manifest.get("paths", {}):
        raise SystemExit("This workflow run does not include the fulfillment layer.")
    request_path = workflow_path(manifest, "fulfillment_request")
    if is_placeholder_file(request_path):
        raise SystemExit("Build the fulfillment contract request before validation.")

    contract_path = workflow_path(manifest, "fulfillment_contract")
    evidence_path = workflow_path(manifest, "fulfillment_evidence")
    contract = load_json(contract_path) if contract_path.exists() else {}
    evidence = load_json(evidence_path) if evidence_path.exists() else {}
    request = load_json(request_path)
    report = fulfillment.validate_fulfillment(
        run_dir,
        manifest,
        contract,
        evidence,
        request=request,
    )
    attach_validation_error_surface(manifest, report, stage="fulfillment_validation")

    validation_path = workflow_path(manifest, "fulfillment_validation")
    write_json(validation_path, report)
    report_path = run_dir / "07_fulfillment" / "outputs" / "fulfillment_validation_report.md"
    report_path.write_text(
        build_validation_report("Requested Output Fulfillment Validation", report),
        encoding="utf-8",
    )
    update_layer_status(
        manifest,
        "07_fulfillment",
        "valid" if report["valid"] else "invalid",
    )
    manifest.setdefault("summary", {})["fulfillment_validation_valid"] = report["valid"]
    manifest["summary"]["fulfillment_validation_file"] = str(validation_path)
    manifest["summary"]["fulfillment_validation_report"] = str(report_path)
    save_manifest(manifest_path, manifest)

    final_report = None
    if report["valid"]:
        final_report = build_human_report_for_workflow(run_dir)
    print(
        to_json(
            {
                "validation_file": str(validation_path),
                "report_file": str(report_path),
                "result": report,
                "final_human_report": final_report,
            }
        )
    )
    return 0 if report["valid"] else 1


def synchronize_workflow_outputs(run_dir: Path) -> dict[str, Any]:
    """Refresh user-facing projections after a lifecycle state transition."""
    status = build_workflow_status(run_dir)
    context_status = status.get("validation_status", {}).get("context_validation", {})
    _, manifest = load_manifest(run_dir)
    report_path = workflow_path(manifest, "human_report") if "human_report" in manifest.get("paths", {}) else None
    if (
        context_status.get("valid") is True
        and report_path is not None
        and report_path.exists()
        and not is_placeholder_file(report_path)
    ):
        build_human_report_for_workflow(run_dir)
        status = build_workflow_status(run_dir)
    write_workflow_status_files(run_dir, status)
    return status


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_final_deliverable(
    manifest: dict[str, Any],
    run_dir: Path,
    source_path: str,
) -> tuple[Path, Path]:
    project_root_value = manifest.get("project_root_absolute")
    if not project_root_value:
        raise SystemExit("--final requires a governed run with project_root_absolute.")
    project_root = Path(str(project_root_value)).resolve(strict=False)
    source = Path(source_path)
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve(strict=False)
    else:
        source = source.resolve(strict=False)
    if not source.exists() or not source.is_file():
        raise SystemExit(f"Final artifact source file does not exist: {source}")
    if not workspace_governance.is_within(project_root, source):
        raise SystemExit("--final source must stay inside ProjectRoot.")
    if workspace_governance.is_within(run_dir, source):
        raise SystemExit("--final source must be project-owned and separate from RunDir.")

    deliverables_root = project_root / "deliverables"
    deliverables_root.mkdir(parents=True, exist_ok=True)
    if workspace_governance.is_within(deliverables_root, source):
        return source, source

    target = deliverables_root / source.name
    if target.exists() and _file_sha256(target) != _file_sha256(source):
        suffix = str(manifest.get("run_id") or run_dir.name)[-8:]
        target = deliverables_root / f"{source.stem}__{suffix}{source.suffix}"
        collision = 2
        while target.exists() and _file_sha256(target) != _file_sha256(source):
            target = deliverables_root / f"{source.stem}__{suffix}_{collision:02d}{source.suffix}"
            collision += 1
    if not target.exists():
        shutil.copy2(source, target)
    return source, target.resolve(strict=False)


def command_register_artifact(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve(strict=False)
    manifest_path, manifest = load_manifest(run_dir)
    automatic_final = str(args.role).strip().lower() in {"requested_output", "final_output"}
    effective_final = bool(args.final or automatic_final)
    if (args.snapshot or effective_final) and args.no_copy:
        raise SystemExit("--snapshot/--final and --no-copy cannot be used together.")

    source_path = args.path
    deliverable_entry: dict[str, Any] | None = None
    working_source: str | None = None
    if effective_final:
        project_root_value = manifest.get("project_root_absolute")
        if not project_root_value:
            raise SystemExit("Final output registration requires a governed run with project_root_absolute.")
        workspace = workspace_governance.resolve_workspace(
            project_root=str(project_root_value),
            session_cwd=Path(str(project_root_value)),
        )
        with workspace_governance.deliverables_lock(workspace):
            working_path, final_path = _managed_final_deliverable(manifest, run_dir, args.path)
        working_source = str(working_path)
        source_path = str(final_path)
        _, deliverable_entry = workspace_governance.build_deliverable_entry(
            workspace,
            run_id=str(manifest["run_id"]),
            path=final_path,
            role="requested_output",
        )

    project_root: Path | None = None
    project_root_value = manifest.get("project_root_absolute")
    if project_root_value:
        project_root = Path(str(project_root_value)).resolve(strict=False)
    resolved_source = Path(source_path)
    if not resolved_source.is_absolute():
        resolved_source = (Path.cwd() / resolved_source).resolve(strict=False)
    automatic_custody = artifact_store.requires_external_output_custody(
        source=resolved_source,
        role=args.role,
        project_root=project_root,
    )
    if automatic_custody and args.no_copy:
        raise SystemExit(
            "External generated outputs must be copied into the workflow RunDir; "
            "--no-copy is allowed only for project-owned files or reference inputs."
        )

    result = artifact_store.register_artifact(
        run_dir,
        artifact_id=args.artifact_id,
        artifact_type=args.type,
        role=args.role,
        path=source_path,
        source_step=args.source_step,
        prompt_file=args.prompt_file,
        description=args.description,
        target_path=args.target_path,
        copy_into_run=bool(args.snapshot or effective_final),
        working_source=working_source,
        project_root=project_root,
    )
    if automatic_custody:
        result["custody_policy_applied"] = "external_generated_output_copied_into_run"
    if automatic_final:
        result["delivery_policy_applied"] = "final_output_promoted_to_project_deliverables"
    if deliverable_entry is not None:
        workspace_governance.upsert_deliverable_entry(manifest, deliverable_entry)
        manifest.setdefault("summary", {})["final_deliverable"] = {
            "path": deliverable_entry["path_relative"],
            "artifact_id": result["artifact"]["id"],
            "storage_mode": "milestone_snapshot",
        }
        save_manifest(manifest_path, manifest)
        result["deliverable"] = deliverable_entry
        result["finalization_mode"] = "managed_deliverable"

    status = build_workflow_status(run_dir)
    write_workflow_status_files(run_dir, status)
    print(to_json(result))
    return 0


def initialize_continuation_for_workflow(
    run_dir: Path,
    *,
    current_phase: str,
    active_artifact_ids: list[str],
    candidate_artifact_id: str | None = None,
    candidate_count: int | None = None,
    index_order: str = "left_to_right",
    candidate_labels: list[str] | None = None,
    next_action_types: list[str] | None = None,
    decision_note: str | None = None,
    working_root: str | None = None,
    completion_gate: str = "approved",
    risk_level: str = "medium",
    deployment_target: str | None = None,
) -> dict[str, Any]:
    manifest_path, manifest = load_manifest(run_dir)
    state_path = continuation_store.continuation_state_path(run_dir)
    linked_path = manifest.get("paths", {}).get("continuation_state")
    if state_path.exists():
        if linked_path:
            raise FileExistsError(f"Continuation state is already linked: {state_path}")
        inspection = continuation_store.inspect_continuation_state(run_dir)
        if inspection.get("valid") is not True:
            raise ValueError(f"Cannot recover invalid unlinked continuation state: {inspection.get('errors', [])}")
        state = inspection["state"]
        if state.get("continuation_id") != manifest.get("run_id"):
            raise ValueError("Unlinked continuation_id does not match the workflow run_id.")
    else:
        state = continuation_store.initialize_continuation(
            run_dir,
            run_id=manifest["run_id"],
            current_phase=current_phase,
            active_artifact_ids=active_artifact_ids,
            candidate_artifact_id=candidate_artifact_id,
            candidate_count=candidate_count,
            index_order=index_order,
            candidate_labels=candidate_labels,
            next_action_types=next_action_types,
            decision_note=decision_note,
            working_root=working_root,
            completion_gate=completion_gate,
            risk_level=risk_level,
            deployment_target=deployment_target,
        )
    manifest.setdefault("paths", {})["continuation_state"] = str(state_path)
    manifest.setdefault("summary", {})["continuation"] = {
        "continuation_id": state["continuation_id"],
        "state_file": str(state_path),
        "rule": "Continuation state references registered artifact ids; it is not an artifact manifest.",
        "workspace_mode": "project_first",
        "snapshot_policy": "milestone_only",
    }
    save_manifest(manifest_path, manifest)
    return inspect_continuation_for_workflow(run_dir)


def inspect_continuation_for_workflow(run_dir: Path) -> dict[str, Any]:
    _, manifest = load_manifest(run_dir)
    result = continuation_store.inspect_continuation_state(run_dir)
    state_path = continuation_store.continuation_state_path(run_dir)
    linked_path = manifest.get("paths", {}).get("continuation_state")
    linked = False
    if isinstance(linked_path, str) and linked_path:
        try:
            linked = Path(linked_path).resolve() == state_path.resolve()
        except (OSError, RuntimeError):
            linked = False
    if result.get("exists") and not linked:
        result = dict(result)
        result["errors"] = [
            *result.get("errors", []),
            {
                "code": "CONTINUATION_STATE_UNLINKED",
                "path": "workflow_manifest.paths.continuation_state",
                "message": "workflow_manifest.json must link to continuation_state.json.",
            },
        ]
        result["valid"] = False
    result["linked"] = linked
    return result


def command_init_continuation(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    result = initialize_continuation_for_workflow(
        run_dir,
        current_phase=args.current_phase,
        active_artifact_ids=args.active_artifact_id or [],
        candidate_artifact_id=args.candidate_artifact_id,
        candidate_count=args.candidate_count,
        index_order=args.index_order,
        candidate_labels=args.candidate_label,
        next_action_types=args.next_action,
        decision_note=args.decision_note,
        working_root=args.working_root,
        completion_gate=args.completion_gate,
        risk_level=args.risk_level,
        deployment_target=args.deployment_target,
    )
    synchronize_workflow_outputs(run_dir)
    print(to_json(result))
    return 0


def command_inspect_continuation(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    result = inspect_continuation_for_workflow(run_dir)
    print(to_json(result))
    return 0 if result.get("valid") is True else 1


def command_select_candidate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    current = inspect_continuation_for_workflow(run_dir)
    if current.get("valid") is not True:
        raise SystemExit(f"Continuation state must be linked and valid before selection: {current.get('errors', [])}")
    continuation_store.select_candidate(
        run_dir,
        selector=args.candidate,
        action_type=args.action,
        note=args.note,
    )
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def command_migrate_continuation(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    continuation_store.migrate_continuation(
        run_dir,
        working_root=args.working_root,
        completion_gate=args.completion_gate,
        risk_level=args.risk_level,
        deployment_target=args.deployment_target,
    )
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def command_set_continuation_workspace(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    continuation_store.set_workspace_context(
        run_dir,
        working_root=args.working_root,
        note=args.note,
    )
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def command_record_continuation_result(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    continuation_store.record_result(
        run_dir,
        artifact_ids=args.artifact_id,
        action_type=args.action,
        note=args.note,
    )
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def command_approve_continuation(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    continuation_store.approve_result(run_dir, note=args.note)
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def command_start_deployment(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    continuation_store.start_deployment(
        run_dir,
        target=args.target,
        confirmed=args.confirmed,
        note=args.note,
    )
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def command_record_deployment(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    continuation_store.record_deployment(run_dir, note=args.note)
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def continuation_delivery_status(run_dir: Path) -> dict[str, Any]:
    return continuation_store.delivery_status(run_dir)


def command_complete_continuation(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    current = inspect_continuation_for_workflow(run_dir)
    if current.get("valid") is not True:
        raise SystemExit(f"Continuation state must be linked and valid before completion: {current.get('errors', [])}")
    delivery = continuation_delivery_status(run_dir)
    if delivery.get("valid") is not True:
        artifact_ids = ", ".join(delivery.get("undelivered_artifact_ids", []))
        raise SystemExit(
            "CONTINUATION_DELIVERABLE_REQUIRED: active output artifacts are not linked to "
            f"ProjectRoot deliverables: {artifact_ids}. Re-register each user-facing file with "
            "--role final_output (or --final), then retry complete-continuation."
        )
    continuation_store.complete_continuation(run_dir, note=args.note)
    synchronize_workflow_outputs(run_dir)
    print(to_json(inspect_continuation_for_workflow(run_dir)))
    return 0


def initialize_governed_workflow(
    *,
    text: str,
    project_root: str | None,
    output: str | None,
    operation_id: str | None,
    run_name: str | None,
    source_files: list[str],
    session_reference: str | None,
    relation_type: str,
    parent_run_id: str | None,
    project_name: str | None = None,
    session_cwd: Path | None = None,
) -> dict[str, Any]:
    return workspace_cli.initialize_governed_workflow(
        text=text,
        project_root=project_root,
        project_name=project_name,
        output=output,
        operation_id=operation_id,
        run_name=run_name,
        source_files=source_files,
        session_reference=session_reference,
        relation_type=relation_type,
        parent_run_id=parent_run_id,
        tool_root=TOOL_ROOT,
        workflow_version=WORKFLOW_VERSION,
        entrypoint=Path(__file__),
        build_workflow=build_workflow,
        session_cwd=session_cwd,
    )




def command_inspect(args: argparse.Namespace) -> int:
    report = inspect_workflow(Path(args.run_dir))
    print(to_json(report))
    return 0




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and inspect workflow folders across current layers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    workspace_cli.configure_workspace_parsers(
        subparsers,
        tool_root=TOOL_ROOT,
        workflow_version=WORKFLOW_VERSION,
        entrypoint=Path(__file__),
        build_workflow=build_workflow,
        read_text_argument=read_text_argument,
    )


    inspect_parser = subparsers.add_parser("inspect", help="Inspect an existing workflow run folder.")
    inspect_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    inspect_parser.set_defaults(func=command_inspect)

    status_parser = subparsers.add_parser("status", help="Write and print workflow status JSON.")
    status_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    status_parser.set_defaults(func=command_status)

    next_parser = subparsers.add_parser("next", help="Write and print the next workflow action JSON.")
    next_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    next_parser.set_defaults(func=command_next)

    validate_route_parser = subparsers.add_parser("validate-route", help="Validate workflow router filled JSON.")
    validate_route_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    validate_route_parser.set_defaults(func=command_validate_route)

    build_direction_parser = subparsers.add_parser("build-direction", help="Build direction lens request inside workflow.")
    build_direction_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    build_direction_parser.add_argument("--allow-invalid", action="store_true", help="Allow building after invalid route validation for debugging.")
    build_direction_parser.set_defaults(func=command_build_direction)

    validate_direction_parser = subparsers.add_parser("validate-direction", help="Validate workflow direction lens filled JSON.")
    validate_direction_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    validate_direction_parser.set_defaults(func=command_validate_direction)

    build_context_parser = subparsers.add_parser("build-context", help="Build situation context request inside workflow.")
    build_context_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    build_context_parser.add_argument("--allow-invalid", action="store_true", help="Allow building after invalid direction validation for debugging.")
    build_context_parser.set_defaults(func=command_build_context)

    validate_context_parser = subparsers.add_parser("validate-context", help="Validate workflow situation context filled JSON.")
    validate_context_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    validate_context_parser.set_defaults(func=command_validate_context)

    build_report_parser = subparsers.add_parser("build-report", help="Build human-readable quality gate report.")
    build_report_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    build_report_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Allow report generation before context validation passes for debugging.",
    )
    build_report_parser.set_defaults(func=command_build_report)


    build_fulfillment_parser = subparsers.add_parser(
        "build-fulfillment",
        help="Build the requested-output fulfillment contract request.",
    )
    build_fulfillment_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    build_fulfillment_parser.set_defaults(func=command_build_fulfillment)

    validate_fulfillment_parser = subparsers.add_parser(
        "validate-fulfillment",
        help="Validate registered result evidence against the fulfillment contract.",
    )
    validate_fulfillment_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    validate_fulfillment_parser.set_defaults(func=command_validate_fulfillment)

    register_artifact_parser = subparsers.add_parser(
        "register-artifact",
        help="Register a project file or copy an external generated output into the workflow RunDir.",
    )
    register_artifact_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    register_artifact_parser.add_argument("--artifact-id", required=True, help="Stable artifact id.")
    register_artifact_parser.add_argument("--type", required=True, help="Artifact type, such as image, prompt, document, json, or other.")
    register_artifact_parser.add_argument("--role", default="generated_output", help="Artifact role, such as generated_output or reference_input.")
    register_artifact_parser.add_argument("--path", required=True, help="Source file path to copy or register.")
    register_artifact_parser.add_argument("--source-step", default="external_artifact", help="Step or tool that produced the artifact.")
    register_artifact_parser.add_argument("--prompt-file", help="Optional prompt file linked to this artifact.")
    register_artifact_parser.add_argument("--description", help="Optional artifact description.")
    register_artifact_parser.add_argument("--target-path", help="Optional target path inside the workflow run directory.")
    register_artifact_parser.add_argument(
        "--final",
        action="store_true",
        help="Copy a standalone final file into ProjectRoot/deliverables and create a milestone snapshot.",
    )
    register_artifact_parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Copy an approved or final milestone into the workflow run. Default is project reference only.",
    )
    register_artifact_parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Keep a project-owned file as a reference. External generated outputs cannot use this flag.",
    )
    register_artifact_parser.set_defaults(func=command_register_artifact)

    init_continuation_parser = subparsers.add_parser(
        "init-continuation",
        help="Create run-local continuation state linked to registered workflow artifacts.",
    )
    init_continuation_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    init_continuation_parser.add_argument("--current-phase", required=True, help="Current continuation phase.")
    init_continuation_parser.add_argument(
        "--active-artifact-id",
        action="append",
        help="Registered artifact id used by the continuation. Can be repeated.",
    )
    init_continuation_parser.add_argument("--candidate-artifact-id", help="Registered candidate sheet artifact id.")
    init_continuation_parser.add_argument("--candidate-count", type=int, help="Number of logical candidates in the sheet.")
    init_continuation_parser.add_argument("--index-order", default="left_to_right", help="Candidate ordering rule.")
    init_continuation_parser.add_argument(
        "--candidate-label",
        action="append",
        help="Candidate label in order. Can be repeated; defaults to A, B, C, and so on.",
    )
    init_continuation_parser.add_argument(
        "--next-action",
        action="append",
        help="Available continuation action type. Can be repeated.",
    )
    init_continuation_parser.add_argument("--decision-note", help="Optional initialization decision note.")
    init_continuation_parser.add_argument("--working-root", help="Project workspace where editable artifacts are produced.")
    init_continuation_parser.add_argument(
        "--completion-gate",
        choices=sorted(continuation_lifecycle.COMPLETION_GATES),
        default="approved",
        help="Requested scope gate required before completion.",
    )
    init_continuation_parser.add_argument(
        "--risk-level",
        choices=sorted(continuation_lifecycle.RISK_LEVELS),
        default="medium",
        help="Risk level used by the deployment approval policy.",
    )
    init_continuation_parser.add_argument("--deployment-target", help="Optional deployment or installation target.")
    init_continuation_parser.set_defaults(func=command_init_continuation)

    inspect_continuation_parser = subparsers.add_parser(
        "inspect-continuation",
        help="Read and validate run-local continuation state.",
    )
    inspect_continuation_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    inspect_continuation_parser.set_defaults(func=command_inspect_continuation)

    select_candidate_parser = subparsers.add_parser(
        "select-candidate",
        help="Resolve a numeric or label candidate reference and record the selected next action.",
    )
    select_candidate_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    select_candidate_parser.add_argument("--candidate", required=True, help="Candidate reference such as 3, 3번, or B.")
    select_candidate_parser.add_argument("--action", required=True, help="One action type already listed in next_actions.")
    select_candidate_parser.add_argument("--note", help="Optional decision note.")
    select_candidate_parser.set_defaults(func=command_select_candidate)

    migrate_continuation_parser = subparsers.add_parser(
        "migrate-continuation",
        help="Upgrade a linked continuation state from v0.1 to v0.2.",
    )
    migrate_continuation_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    migrate_continuation_parser.add_argument("--working-root", help="Project workspace for editable artifacts.")
    migrate_continuation_parser.add_argument(
        "--completion-gate",
        choices=sorted(continuation_lifecycle.COMPLETION_GATES),
        help="Override the inferred completion gate during migration.",
    )
    migrate_continuation_parser.add_argument(
        "--risk-level",
        choices=sorted(continuation_lifecycle.RISK_LEVELS),
        default="medium",
    )
    migrate_continuation_parser.add_argument("--deployment-target")
    migrate_continuation_parser.set_defaults(func=command_migrate_continuation)

    set_workspace_parser = subparsers.add_parser(
        "set-continuation-workspace",
        help="Validate and update the project workspace linked to a continuation.",
    )
    set_workspace_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    set_workspace_parser.add_argument(
        "--working-root",
        required=True,
        help="Existing project directory where editable artifacts are produced.",
    )
    set_workspace_parser.add_argument("--note", help="Optional reason for correcting the workspace.")
    set_workspace_parser.set_defaults(func=command_set_continuation_workspace)

    record_result_parser = subparsers.add_parser(
        "record-continuation-result",
        help="Bind registered result artifacts and move the continuation to review.",
    )
    record_result_parser.add_argument("--run-dir", required=True)
    record_result_parser.add_argument("--artifact-id", action="append", required=True)
    record_result_parser.add_argument("--action", help="Selected continuation action type.")
    record_result_parser.add_argument("--note")
    record_result_parser.set_defaults(func=command_record_continuation_result)

    approve_continuation_parser = subparsers.add_parser(
        "approve-continuation",
        help="Record user approval and complete or prepare deployment according to scope.",
    )
    approve_continuation_parser.add_argument("--run-dir", required=True)
    approve_continuation_parser.add_argument("--note")
    approve_continuation_parser.set_defaults(func=command_approve_continuation)

    start_deployment_parser = subparsers.add_parser(
        "start-deployment",
        help="Start a scope-approved deployment and begin deployment timing.",
    )
    start_deployment_parser.add_argument("--run-dir", required=True)
    start_deployment_parser.add_argument("--target")
    start_deployment_parser.add_argument("--confirmed", action="store_true")
    start_deployment_parser.add_argument("--note")
    start_deployment_parser.set_defaults(func=command_start_deployment)

    record_deployment_parser = subparsers.add_parser(
        "record-deployment",
        help="Record successful deployment and complete a deployment-scoped continuation.",
    )
    record_deployment_parser.add_argument("--run-dir", required=True)
    record_deployment_parser.add_argument("--note")
    record_deployment_parser.set_defaults(func=command_record_deployment)

    complete_continuation_parser = subparsers.add_parser(
        "complete-continuation",
        help="Mark continuation work complete and calculate elapsed_seconds.",
    )
    complete_continuation_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    complete_continuation_parser.add_argument("--note", help="Optional completion note.")
    complete_continuation_parser.set_defaults(func=command_complete_continuation)

    return parser


RUN_WRITER_COMMANDS = {
    "status",
    "next",
    "validate-route",
    "build-direction",
    "validate-direction",
    "build-context",
    "validate-context",
    "build-report",
    "build-fulfillment",
    "validate-fulfillment",
    "register-artifact",
    "init-continuation",
    "select-candidate",
    "migrate-continuation",
    "set-continuation-workspace",
    "record-continuation-result",
    "approve-continuation",
    "start-deployment",
    "record-deployment",
    "complete-continuation",
}


def dispatch_command(args: argparse.Namespace) -> int:
    if args.command not in RUN_WRITER_COMMANDS or not getattr(args, "run_dir", None):
        return int(args.func(args))
    run_dir = Path(args.run_dir).resolve(strict=False)
    workspace: workspace_governance.Workspace | None = None
    try:
        with workspace_governance.governed_writer_context(run_dir) as workspace:
            result = int(args.func(args))
            if workspace is not None:
                status = build_workflow_status(run_dir)
                write_workflow_status_files(run_dir, status)
                workspace_governance.refresh_governed_run(
                    workspace,
                    run_dir,
                    command=args.command,
                    workflow_state=status.get("workflow_state"),
                )
            return result
    except (Exception, SystemExit) as exc:
        if workspace is not None:
            try:
                workspace_governance.record_governed_run_failure(
                    workspace,
                    run_dir,
                    command=args.command,
                    error=exc,
                )
            except Exception:
                pass
        raise


def main() -> int:
    configure_cli_output()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return dispatch_command(args)
    except workspace_governance.WorkspaceGovernanceError as exc:
        print(to_json(exc.as_dict()), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
