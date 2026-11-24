"""Operating-system operations and capabilities. Imports nothing from Roadie."""

from roadie.system.command import (
    CannotRun,
    cannot_run_message,
    prepare_command,
    run_query,
)
from roadie.system.lock import lock, unlock
from roadie.system.path import executable_suffix
from roadie.system.process import (
    interrupt_process,
    process_group_session,
    signal_process_tree,
    spawn_in_own_group,
    stop_process_tree,
)
from roadie.system.trace import parse_trace, traced_command, tracing_error

__all__ = [
    "CannotRun",
    "cannot_run_message",
    "executable_suffix",
    "interrupt_process",
    "lock",
    "parse_trace",
    "prepare_command",
    "process_group_session",
    "run_query",
    "signal_process_tree",
    "spawn_in_own_group",
    "stop_process_tree",
    "traced_command",
    "tracing_error",
    "unlock",
]
