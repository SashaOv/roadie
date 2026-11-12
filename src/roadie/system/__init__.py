"""Operating-system operations and capabilities. Imports nothing from Roadie."""

from roadie.system.command import (
    CannotRun,
    batch_command_line,
    cannot_run_message,
    prepare_command,
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

__all__ = [
    "CannotRun",
    "batch_command_line",
    "cannot_run_message",
    "executable_suffix",
    "interrupt_process",
    "lock",
    "prepare_command",
    "process_group_session",
    "signal_process_tree",
    "spawn_in_own_group",
    "stop_process_tree",
    "unlock",
]
