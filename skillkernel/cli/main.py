"""``skillkernel`` command-line entry point.

An adapter, and nothing more. It parses arguments, calls one library function,
renders the result and returns an exit code. Any rule it appears to enforce is
enforced in the library; this module only reports.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from skillkernel import __version__
from skillkernel.bundles.installer import InstallResult, install_bundle_by_id
from skillkernel.cli.exit_codes import (
    INTEGRITY_FAILURE,
    INTERNAL_ERROR,
    NOT_INITIALIZED,
    OK,
    USAGE_ERROR,
    help_epilog,
)
from skillkernel.core.errors import NotInitializedError, SkillKernelError
from skillkernel.core.paths import CONFIG_FILENAME, Layout
from skillkernel.project.bootstrap import initialize_with_report, is_initialized
from skillkernel.validation.doctor import DoctorReport, run_doctor

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillkernel",
        description="A project-agnostic engineering intelligence layer.",
        epilog=help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"skillkernel {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    init_parser = subparsers.add_parser(
        "init",
        help="initialize a SkillKernel workspace",
        description=(
            "Create a SkillKernel workspace. Refuses to overwrite an existing one, "
            "so re-running never modifies a workspace that is already initialized."
        ),
    )
    init_parser.add_argument("path", type=Path, help="directory to initialize")
    init_parser.add_argument(
        "--project-name",
        default=None,
        help="project name recorded in the profile (default: the directory name)",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check the integrity of a workspace",
        description=(
            "Read-only integrity check. Aggregates the kernel's existing validators "
            "and never modifies the workspace."
        ),
    )
    doctor_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    doctor_parser.add_argument(
        "--json", action="store_true", dest="as_json", help="emit a deterministic JSON report"
    )

    skill_parser = subparsers.add_parser(
        "skill",
        help="work with the skills in a workspace",
        description="Operations on a workspace's skills.",
    )
    # Required: "skillkernel skill" alone is a usage error, and argparse reports
    # it the same way it reports any unknown command, with the same exit code.
    skill_subparsers = skill_parser.add_subparsers(
        dest="skill_command", metavar="<subcommand>", required=True
    )

    install_parser = skill_subparsers.add_parser(
        "install",
        help="install a bundled skill definition into a workspace",
        description=(
            "Install a skill definition shipped with SkillKernel. The workspace "
            "allocates its own identifier, the skill starts at maturity 'observed' "
            "like any other, and no evidence, maturity or history is imported: those "
            "are earned locally. An existing skill is never overwritten, merged or "
            "renamed -- a collision is refused."
        ),
    )
    install_parser.add_argument("bundle", help="identifier of the bundled skill to install")
    install_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    return parser


def command_init(args: argparse.Namespace, out: TextIO) -> int:
    target = Path(args.path)
    project_name = args.project_name or target.resolve().name
    report = initialize_with_report(target, project_name=project_name)
    print(report.summary(), file=out)
    print(f"  created {CONFIG_FILENAME}", file=out)
    print(f"  next: skillkernel doctor {target}", file=out)
    return OK


def render_doctor(report: DoctorReport, path: Path, out: TextIO) -> None:
    for finding in report.sorted_findings():
        print(str(finding), file=out)
    for failure in report.internal_errors:
        print(f"INTERNAL ERROR: {failure.message}", file=out)

    counts = report.to_document()["counts"]
    print(f"{path}: {counts['error']} error(s), {counts['warning']} warning(s)", file=out)
    if not report.is_complete:
        print(
            "the report is INCOMPLETE: one or more checks failed to run, "
            "so the workspace's true state is unknown",
            file=out,
        )


def command_doctor(args: argparse.Namespace, out: TextIO) -> int:
    target = Path(args.path)
    report = run_doctor(require_workspace(target))

    if args.as_json:
        print(json.dumps(report.to_document(), indent=2, sort_keys=True), file=out)
    else:
        render_doctor(report, target, out)

    # An incomplete report is never "healthy": some checks never ran.
    if not report.is_complete:
        return INTERNAL_ERROR
    return INTEGRITY_FAILURE if report.has_errors else OK


def require_workspace(path: Path) -> Layout:
    """Resolve an initialized workspace, or refuse with the standard diagnostic."""
    if not is_initialized(path):
        raise NotInitializedError(
            f"{path} is not an initialized SkillKernel workspace "
            f"(no {CONFIG_FILENAME}); run 'skillkernel init {path}' first"
        )
    return Layout(root=path.resolve())


def render_install(result: InstallResult, out: TextIO) -> None:
    record = result.record
    print(
        f"installed {record.id} from bundle {result.bundle_id}@{result.bundle_version}",
        file=out,
    )
    print(f"  location   {result.relative_path}", file=out)
    print(f"  maturity   {record.maturity}", file=out)
    print(f"  source     {result.content_hash}", file=out)
    print(
        f"  evaluation {result.positive_cases} positive, {result.negative_cases} negative case(s)",
        file=out,
    )
    print(
        "  the definition is installed; its maturity and evidence are earned here",
        file=out,
    )


def command_skill_install(args: argparse.Namespace, out: TextIO) -> int:
    layout = require_workspace(Path(args.path))
    render_install(install_bundle_by_id(layout, str(args.bundle)), out)
    return OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return USAGE_ERROR

    handlers: dict[str, Callable[[argparse.Namespace, TextIO], int]] = {
        "init": command_init,
        "doctor": command_doctor,
        "skill install": command_skill_install,
    }
    key = str(args.command)
    if key == "skill":
        key = f"{key} {args.skill_command}"
    handler = handlers[key]

    try:
        return handler(args, sys.stdout)
    except NotInitializedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return NOT_INITIALIZED
    except SkillKernelError as exc:
        # An expected domain failure: the kernel worked, the request did not.
        print(f"error: {exc}", file=sys.stderr)
        return INTEGRITY_FAILURE
    except Exception as exc:  # noqa: BLE001 - deliberate boundary; see DEC-0010
        # BaseException is deliberately not caught: KeyboardInterrupt and
        # SystemExit must keep their normal semantics.
        print(
            f"internal error: {type(exc).__name__}: {exc}\n"
            "This is a fault in SkillKernel itself, not a problem with your "
            "workspace. Please report it.",
            file=sys.stderr,
        )
        return INTERNAL_ERROR


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    sys.exit(main())
