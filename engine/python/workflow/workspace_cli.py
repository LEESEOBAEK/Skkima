from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any, Callable

from shared import workspace_governance
from shared import workspace_migration


WorkflowBuilder = Callable[..., dict[str, Any]]
TextReader = Callable[[str | None, str | None], str]


def to_json(data: Any) -> str:
    return workspace_governance.to_json(data)


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
    tool_root: Path,
    workflow_version: str,
    entrypoint: Path,
    build_workflow: WorkflowBuilder,
    project_name: str | None = None,
    session_cwd: Path | None = None,
) -> dict[str, Any]:
    workspace = workspace_governance.bootstrap_workspace(
        project_root=project_root,
        session_cwd=session_cwd or Path.cwd(),
        tool_root=tool_root,
        project_name=project_name,
        run_name=run_name,
        input_text=text,
    )
    workspace_governance.validate_canonical_output(workspace, output)
    engine = workspace_governance.engine_identity(
        tool_root,
        version=workflow_version,
        entrypoint=entrypoint,
    )

    def builder(reserved_run_id: str) -> dict[str, Any]:
        return build_workflow(
            text=text,
            output_dir=workspace.runs_root,
            run_name=run_name,
            source_files=source_files,
            run_id=reserved_run_id,
            persist_manifest=False,
        )

    manifest, metadata = workspace_governance.initialize_operation_run(
        workspace,
        text=text,
        operation_id=operation_id,
        run_name=run_name,
        session_reference=session_reference,
        relation_type=relation_type,
        parent_run_id=parent_run_id,
        engine=engine,
        builder=builder,
    )
    return {
        **manifest,
        "operation": metadata["operation"],
        "idempotent_reuse": metadata["idempotent_reuse"],
    }


def command_init(args: argparse.Namespace) -> int:
    text = args._read_text_argument(args.text, args.input_file)
    result = initialize_governed_workflow(
        text=text,
        project_root=args.project_root,
        project_name=args.project_name,
        output=args.output,
        operation_id=args.operation_id,
        run_name=args.run_name,
        source_files=args.source_file or [],
        session_reference=args.session_reference,
        relation_type=args.relation_type,
        parent_run_id=args.parent_run_id,
        tool_root=args._tool_root,
        workflow_version=args._workflow_version,
        entrypoint=args._entrypoint,
        build_workflow=args._build_workflow,
    )
    print(to_json(result))
    return 0


def _workspace(args: argparse.Namespace) -> workspace_governance.Workspace:
    return workspace_governance.resolve_workspace(
        project_root=args.project_root,
        session_cwd=Path.cwd(),
    )


def command_workspace_init(args: argparse.Namespace) -> int:
    workspace = workspace_governance.bootstrap_workspace(
        project_root=args.project_root,
        session_cwd=Path.cwd(),
        tool_root=args._tool_root,
        project_name=args.project_name,
    )
    print(
        to_json(
            {
                "workspace_id": workspace.workspace_id,
                "project_root": str(workspace.project_root),
                "canonical_runs_root": str(workspace.runs_root),
                "config_path": str(workspace.config_path),
                "engine_policy": workspace.config["engine_policy"],
            }
        )
    )
    return 0


def command_workspace_inspect(args: argparse.Namespace) -> int:
    report = workspace_governance.write_workspace_inspection(
        _workspace(args),
        tool_root=args._tool_root,
        json_output=Path(args.json_output) if args.json_output else None,
        markdown_output=Path(args.markdown_output) if args.markdown_output else None,
    )
    print(to_json(report))
    return 0


def command_registry_rebuild(args: argparse.Namespace) -> int:
    registry = workspace_governance.rebuild_registry(_workspace(args))
    print(to_json(registry))
    return 0


def command_continue_run(args: argparse.Namespace) -> int:
    supplemental_input = args.supplemental_input
    supplemental_source: dict[str, Any] | None = None
    if args.supplemental_input_sha256 and not args.supplemental_input_file:
        raise SystemExit("--supplemental-input-sha256 requires --supplemental-input-file.")
    if args.supplemental_input_file:
        source_path = Path(args.supplemental_input_file).resolve(strict=True)
        source_bytes = source_path.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        if args.supplemental_input_sha256 and source_sha256.casefold() != args.supplemental_input_sha256.casefold():
            raise SystemExit("REQUEST_INTEGRITY_FAILED: supplemental input file SHA-256 does not match.")
        try:
            supplemental_input = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SystemExit("Supplemental input file must be valid UTF-8.") from exc
        supplemental_source = {
            "path": str(source_path),
            "sha256": source_sha256,
            "byte_count": len(source_bytes),
            "character_count": len(supplemental_input),
        }
    result = workspace_governance.record_continuation_operation(
        _workspace(args),
        run_id=args.run_id,
        run_dir=args.run_dir,
        operation_id=args.operation_id,
        session_reference=args.session_reference,
        note=args.note,
        supplemental_input=supplemental_input,
        supplemental_input_source=supplemental_source,
        delivery_policy="internal_only" if args.allow_internal_only else "required",
    )
    print(to_json(result))
    return 0


def command_abort_continuation(args: argparse.Namespace) -> int:
    result = workspace_governance.abort_continuation_operation(
        _workspace(args),
        run_id=args.run_id,
        run_dir=args.run_dir,
        operation_id=args.operation_id,
        reason=args.reason,
        approved=args.approved,
    )
    print(to_json(result))
    return 0


def command_register_deliverable(args: argparse.Namespace) -> int:
    entry = workspace_governance.register_deliverable(
        _workspace(args),
        run_id=args.run_id,
        path=args.path,
        role=args.role,
    )
    print(to_json(entry))
    return 0


def command_workspace_inventory(args: argparse.Namespace) -> int:
    workspace_migration.validate_output_paths_outside_source(
        Path(args.source_root),
        [Path(args.json_output) if args.json_output else None],
    )
    inventory = workspace_migration.inventory_workspace(args.source_root)
    if args.json_output:
        workspace_migration.write_inventory(Path(args.json_output), inventory)
    print(to_json(inventory))
    return 0


def command_migration_dry_run(args: argparse.Namespace) -> int:
    workspace_migration.validate_output_paths_outside_source(
        Path(args.source_root),
        [
            Path(args.inventory_output) if args.inventory_output else None,
            Path(args.json_output) if args.json_output else None,
            Path(args.markdown_output),
        ],
    )
    inventory = workspace_migration.inventory_workspace(args.source_root)
    report = workspace_migration.migration_dry_run(inventory, project_root=args.project_root)
    if args.inventory_output:
        workspace_migration.write_inventory(Path(args.inventory_output), inventory)
    workspace_migration.write_migration_report(
        json_path=Path(args.json_output) if args.json_output else None,
        markdown_path=Path(args.markdown_output),
        report=report,
    )
    print(to_json(report))
    return 0


def configure_workspace_parsers(
    subparsers: Any,
    *,
    tool_root: Path,
    workflow_version: str,
    entrypoint: Path,
    build_workflow: WorkflowBuilder,
    read_text_argument: TextReader,
) -> None:
    shared_defaults = {
        "_tool_root": tool_root,
        "_workflow_version": workflow_version,
        "_entrypoint": entrypoint,
        "_build_workflow": build_workflow,
        "_read_text_argument": read_text_argument,
    }

    init_parser = subparsers.add_parser("init", help="Create a workflow run folder.")
    input_group = init_parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text", help="Raw user input text.")
    input_group.add_argument("--input-file", help="UTF-8 text file containing raw user input.")
    init_parser.add_argument("--source-file", action="append", help="Optional source file path. Can be repeated.")
    init_parser.add_argument(
        "--project-root",
        help="Explicit ProjectRoot. Otherwise discover an ancestor workspace, initialize a safe empty folder, or derive a child project.",
    )
    init_parser.add_argument(
        "--project-name",
        help="Preferred child project folder name when SessionCwd is a generic parent; explicit --project-root wins.",
    )
    init_parser.add_argument(
        "--output",
        help="Compatibility option. When supplied it must resolve to ProjectRoot/outputs/workflows.",
    )
    init_parser.add_argument("--operation-id", help="OperationId idempotency key. A new value is generated when omitted.")
    init_parser.add_argument("--session-reference", help="Optional externally visible CLI/session reference.")
    init_parser.add_argument("--run-name", help="Optional workflow run folder name.")
    init_parser.add_argument(
        "--relation-type",
        choices=sorted(workspace_governance.RELATION_TYPES - {"continuation"}),
        default="independent",
        help="Relationship of the new run. A parent with independent defaults to branch.",
    )
    init_parser.add_argument(
        "--parent-run-id",
        help="Create a new run based on an existing canonical run and record parent_run_id.",
    )
    init_parser.set_defaults(func=command_init, **shared_defaults)

    workspace_init_parser = subparsers.add_parser(
        "workspace-init",
        aliases=["workspace-bootstrap"],
        help="Initialize or load ProjectRoot and its CanonicalRunsRoot before any run is created.",
    )
    workspace_init_parser.add_argument(
        "--project-root",
        help="Explicit project directory; otherwise use a configured/safe SessionCwd or a named child project.",
    )
    workspace_init_parser.add_argument(
        "--project-name",
        help="Child project folder name when initializing from a generic parent such as Desktop.",
    )
    workspace_init_parser.set_defaults(func=command_workspace_init, **shared_defaults)

    workspace_inspect_parser = subparsers.add_parser(
        "workspace-inspect",
        help="Rebuild registry data and write machine- and human-readable completeness reports.",
    )
    workspace_inspect_parser.add_argument("--project-root", help="ProjectRoot; otherwise discover from SessionCwd.")
    workspace_inspect_parser.add_argument("--json-output", help="Optional JSON report path.")
    workspace_inspect_parser.add_argument("--markdown-output", help="Optional Markdown report path.")
    workspace_inspect_parser.set_defaults(func=command_workspace_inspect, **shared_defaults)

    registry_parser = subparsers.add_parser(
        "registry-rebuild",
        help="Rebuild the derived workspace registry from operation reservations and run manifests.",
    )
    registry_parser.add_argument("--project-root", help="ProjectRoot; otherwise discover from SessionCwd.")
    registry_parser.set_defaults(func=command_registry_rebuild, **shared_defaults)

    continue_parser = subparsers.add_parser(
        "continue-run",
        aliases=["continue"],
        help="Explicitly reserve a continuation operation for exactly one existing canonical run.",
    )
    continue_parser.add_argument("--project-root", help="ProjectRoot; otherwise discover from SessionCwd.")
    continue_selector = continue_parser.add_mutually_exclusive_group(required=True)
    continue_selector.add_argument("--run-id", help="Existing RunId in this ProjectRoot.")
    continue_selector.add_argument("--run-dir", help="Existing canonical RunDir in this ProjectRoot.")
    continue_parser.add_argument("--operation-id", help="Continuation OperationId; generated when omitted.")
    continue_parser.add_argument("--session-reference", help="Optional externally visible CLI/session reference.")
    continue_parser.add_argument("--note", help="Externally stated continuation note.")
    supplemental_group = continue_parser.add_mutually_exclusive_group()
    supplemental_group.add_argument(
        "--supplemental-input",
        help="New user input to append to the canonical run history for this continuation.",
    )
    supplemental_group.add_argument(
        "--supplemental-input-file",
        help="UTF-8 file containing the complete user request to append without shell truncation.",
    )
    continue_parser.add_argument(
        "--supplemental-input-sha256",
        help="Optional expected SHA-256 for --supplemental-input-file.",
    )
    continue_parser.add_argument(
        "--allow-internal-only",
        action="store_true",
        help="Allow completion without a user-facing DeliverablePath. The default requires delivery for active outputs.",
    )
    continue_parser.set_defaults(func=command_continue_run, **shared_defaults)

    abort_parser = subparsers.add_parser(
        "abort-continuation",
        help="Abort the exact active continuation owner and release its run lock after explicit approval.",
    )
    abort_parser.add_argument("--project-root", help="ProjectRoot; otherwise discover from SessionCwd.")
    abort_selector = abort_parser.add_mutually_exclusive_group(required=True)
    abort_selector.add_argument("--run-id", help="Existing RunId in this ProjectRoot.")
    abort_selector.add_argument("--run-dir", help="Existing canonical RunDir in this ProjectRoot.")
    abort_parser.add_argument("--operation-id", required=True, help="Exact active continuation OperationId.")
    abort_parser.add_argument("--reason", required=True, help="Auditable reason for aborting the operation.")
    abort_parser.add_argument("--approved", action="store_true", help="Confirm the ownership release.")
    abort_parser.set_defaults(func=command_abort_continuation, **shared_defaults)

    deliverable_parser = subparsers.add_parser(
        "register-deliverable",
        help="Record a project-owned DeliverablePath and its SHA-256 relationship in a run manifest.",
    )
    deliverable_parser.add_argument("--project-root", help="ProjectRoot; otherwise discover from SessionCwd.")
    deliverable_parser.add_argument("--run-id", required=True, help="Canonical RunId that references the deliverable.")
    deliverable_parser.add_argument("--path", required=True, help="Project-owned file or directory path.")
    deliverable_parser.add_argument("--role", default="project_deliverable", help="Deliverable relationship role.")
    deliverable_parser.set_defaults(func=command_register_deliverable, **shared_defaults)

    inventory_parser = subparsers.add_parser(
        "workspace-inventory",
        aliases=["inventory"],
        help="Read workflow manifests and content fingerprints without changing the source workspace.",
    )
    inventory_parser.add_argument("--source-root", required=True, help="Workspace tree to inventory read-only.")
    inventory_parser.add_argument("--json-output", help="Optional JSON output path outside the source workspace.")
    inventory_parser.set_defaults(func=command_workspace_inventory, **shared_defaults)

    migration_parser = subparsers.add_parser(
        "migration-dry-run",
        help="Plan collision-safe canonical migration without copy, move, delete, or overwrite.",
    )
    migration_parser.add_argument("--source-root", required=True, help="Existing workspace tree to inspect read-only.")
    migration_parser.add_argument(
        "--project-root",
        help="Planned ProjectRoot. Defaults to source-root and is never initialized by this command.",
    )
    migration_parser.add_argument("--inventory-output", help="Optional inventory JSON output path.")
    migration_parser.add_argument("--json-output", help="Optional migration plan JSON output path.")
    migration_parser.add_argument("--markdown-output", required=True, help="Required Markdown plan output path.")
    migration_parser.set_defaults(func=command_migration_dry_run, **shared_defaults)
