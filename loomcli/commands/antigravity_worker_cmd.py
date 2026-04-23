"""Antigravity Worker daemon for Powerloom.

This command acts as a daemon that polls the Powerloom API for threads
assigned to a registered Antigravity agent and dispatches them to the
local Antigravity IDE via its API.
"""

import typer

app = typer.Typer()

@app.callback(invoke_without_command=True)
def run_worker(
    agent_id: str = typer.Option(
        ...,
        "--agent-id",
        help="The Powerloom agent ID to poll tasks for.",
    ),
    poll_interval: int = typer.Option(
        5,
        "--poll-interval",
        help="Seconds to wait between polls.",
    ),
) -> None:
    """Run the Antigravity worker daemon."""
    typer.echo(f"Starting Antigravity worker for agent {agent_id}...")
    typer.echo("Polling Powerloom for assigned tasks...")
    # TODO: Implement the polling loop and interaction with the local Antigravity API
    typer.echo("Not fully implemented yet. Exiting.")
