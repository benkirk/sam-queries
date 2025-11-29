#!/usr/bin/env python3
"""
System Status CLI Dashboard

A command-line tool for displaying the current status of HPC systems.
"""

import argparse
import sys
from typing import Optional, List
from sqlalchemy.orm import Session

# Rich for formatted output
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.text import Text

# Make sure system_status package is in path
python_dir = __import__('pathlib').Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(python_dir))

from system_status import (
    create_status_engine,
    DerechoStatus,
    CasperStatus,
    JupyterHubStatus,
    ResourceReservation,
)


class SystemStatusCLI:
    """Main CLI application class for system status."""

    def __init__(self):
        """Initialize the CLI."""
        self.engine, self.SessionLocal = create_status_engine()
        self.session = self.SessionLocal()
        self.parser = self._create_parser()
        self.args = None
        self.console = Console()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup session."""
        self.session.close()

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create and configure the argument parser."""
        parser = argparse.ArgumentParser(
            description='Display system status dashboards from the command line.',
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

        parser.add_argument(
            'system_name',
            choices=['derecho', 'casper', 'jupyterhub'],
            help='The name of the system to display.'
        )

        # Add more arguments later, e.g., --watch
        return parser

    def run(self, argv: Optional[List[str]] = None) -> int:
        """Run the CLI application."""
        try:
            self.args = self.parser.parse_args(argv)

            if self.args.system_name == 'derecho':
                return self._display_derecho()
            elif self.args.system_name == 'casper':
                return self._display_casper()
            elif self.args.system_name == 'jupyterhub':
                return self._display_jupyterhub()

            self.console.print(f"[bold red]Unknown system: {self.args.system_name}[/bold red]")
            return 1

        except KeyboardInterrupt:
            self.console.print("\n\n[yellow]⚠️ Interrupted by user.[/yellow]")
            return 130
        except Exception as e:
            self.console.print(f"[bold red]❌ Fatal error: {e}[/bold red]")
            import traceback
            traceback.print_exc()
            return 2

    # ========================================================================
    # Display Functions
    # ========================================================================

    def _get_latest_status(self, model):
        """Generic function to get the latest status for a given model."""
        return self.session.query(model).order_by(model.timestamp.desc()).first()

    def _display_derecho(self):
        """Fetch and display the Derecho status dashboard."""
        status = self._get_latest_status(DerechoStatus)
        if not status:
            self.console.print("[bold yellow]No Derecho status data available.[/bold yellow]")
            return 1
        
        self._display_header("Derecho", status.timestamp)
        self._display_login_nodes(status.login_nodes)
        self._display_derecho_compute_nodes(status)
        self._display_utilization(status, show_gpus=True)
        self._display_job_stats(status)
        self._display_queues(status.queues, show_gpus=True)
        self._display_filesystems(status.filesystems)
        self._display_reservations('derecho')
        
        return 0

    def _display_casper(self):
        """Fetch and display the Casper status dashboard."""
        status = self._get_latest_status(CasperStatus)
        if not status:
            self.console.print("[bold yellow]No Casper status data available.[/bold yellow]")
            return 1
        
        self._display_header("Casper", status.timestamp)
        self._display_login_nodes(status.login_nodes)
        self._display_casper_compute_nodes(status)
        self._display_utilization(status, show_gpus=True, show_viz=True)
        self._display_job_stats(status)
        self._display_casper_node_types(status.node_types)
        self._display_queues(status.queues, show_gpus=True)
        self._display_filesystems(status.filesystems)
        self._display_reservations('casper')
        
        return 0

    def _display_jupyterhub(self):
        """Fetch and display the JupyterHub status dashboard."""
        status = self._get_latest_status(JupyterHubStatus)
        if not status:
            self.console.print("[bold yellow]No JupyterHub status data available.[/bold yellow]")
            return 1

        self._display_header("JupyterHub", status.timestamp)

        status_text = Text("● Online", style="green") if status.available else Text("● Offline", style="red")
        
        table = Table(title="JupyterHub Service", title_style="bold magenta", show_header=False)
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_row("Service Status", status_text)
        table.add_row("Active Users", str(status.active_users))
        table.add_row("Active Sessions", str(status.active_sessions))
        self.console.print(table)

        self._display_utilization(status, show_gpus=False)
        return 0

    # ========================================================================
    # Rich Display Helpers
    # ========================================================================

    def _display_header(self, system_name, timestamp):
        """Display the main dashboard header."""
        header = f"[bold green]{system_name.capitalize()} System Status[/bold green]\nLast updated: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        self.console.print(Panel(header, expand=False))

    def _display_login_nodes(self, login_nodes):
        """Display a table of login nodes."""
        if not login_nodes:
            return

        table = Table(title="Login Nodes", title_style="bold magenta")
        table.add_column("Node", style="cyan", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Users", justify="right")
        table.add_column("Load (1m)", justify="right")

        for node in sorted(login_nodes, key=lambda n: n.node_name):
            status_text = Text("● Online", style="green") if node.available else Text("● Offline", style="red")
            if node.degraded:
                status_text.append(" (degraded)", style="yellow")
            
            table.add_row(
                node.node_name,
                status_text,
                str(node.user_count) if node.user_count is not None else "N/A",
                f"{node.load_1min:.2f}" if node.load_1min is not None else "N/A"
            )
        self.console.print(table)

    def _display_derecho_compute_nodes(self, status: DerechoStatus):
        """Display Derecho compute node status."""
        table = Table(title="Compute Nodes", title_style="bold magenta")
        table.add_column("Partition")
        table.add_column("Total", justify="right")
        table.add_column("Available", justify="right", style="green")
        table.add_column("Down", justify="right", style="red")
        table.add_column("Reserved", justify="right", style="yellow")

        table.add_row(
            "CPU",
            str(status.cpu_nodes_total),
            str(status.cpu_nodes_available),
            str(status.cpu_nodes_down),
            str(status.cpu_nodes_reserved)
        )
        table.add_row(
            "GPU",
            str(status.gpu_nodes_total),
            str(status.gpu_nodes_available),
            str(status.gpu_nodes_down),
            str(status.gpu_nodes_reserved)
        )
        self.console.print(table)
        
    def _display_casper_compute_nodes(self, status: CasperStatus):
        """Display Casper compute node status."""
        table = Table(title="Compute Nodes", title_style="bold magenta")
        table.add_column("Partition")
        table.add_column("Total", justify="right")
        table.add_column("Available", justify="right", style="green")
        table.add_column("Down", justify="right", style="red")
        table.add_column("Reserved", justify="right", style="yellow")

        table.add_row("CPU", str(status.cpu_nodes_total), str(status.cpu_nodes_available), str(status.cpu_nodes_down), str(status.cpu_nodes_reserved))
        table.add_row("GPU", str(status.gpu_nodes_total), str(status.gpu_nodes_available), str(status.gpu_nodes_down), str(status.gpu_nodes_reserved))
        table.add_row("VIZ", str(status.viz_nodes_total), str(status.viz_nodes_available), str(status.viz_nodes_down), str(status.viz_nodes_reserved))
        self.console.print(table)

    def _display_utilization(self, status, show_gpus=False, show_viz=False):
        """Display resource utilization bars."""
        self.console.print("\n[bold magenta]Resource Utilization[/bold magenta]")
        
        cpu_bar = ProgressBar(total=100, completed=status.cpu_utilization_percent or 0)
        self.console.print(f" CPU: {status.cpu_cores_allocated or 0}/{status.cpu_cores_total or 0} Cores")
        self.console.print(cpu_bar)

        if show_gpus and hasattr(status, 'gpu_utilization_percent'):
            gpu_bar = ProgressBar(total=100, completed=status.gpu_utilization_percent or 0)
            self.console.print(f" GPU: {status.gpu_count_allocated or 0}/{status.gpu_count_total or 0} GPUs")
            self.console.print(gpu_bar)
            
        if show_viz and hasattr(status, 'viz_utilization_percent'):
            viz_bar = ProgressBar(total=100, completed=status.viz_utilization_percent or 0)
            self.console.print(f" VIZ: {status.viz_count_allocated or 0}/{status.viz_count_total or 0} VIZ GPUs")
            self.console.print(viz_bar)

        if hasattr(status, 'memory_utilization_percent'):
            mem_bar = ProgressBar(total=100, completed=status.memory_utilization_percent or 0)
            self.console.print(f" Mem: {status.memory_allocated_gb or 0:,.0f}/{status.memory_total_gb or 0:,.0f} GB")
            self.console.print(mem_bar)

    def _display_job_stats(self, status):
        """Display job statistics."""
        table = Table(title="Job Statistics", title_style="bold magenta", show_header=True)
        table.add_column("Running", justify="center")
        table.add_column("Pending", justify="center")
        table.add_column("Held", justify="center")
        table.add_column("Active Users", justify="center")
        
        table.add_row(
            f"[bold green]{status.running_jobs}[/bold green]",
            f"[bold yellow]{status.pending_jobs}[/bold yellow]",
            f"[bold red]{status.held_jobs}[/bold red]",
            f"[bold cyan]{status.active_users}[/bold cyan]"
        )
        self.console.print(table)
        
    def _display_casper_node_types(self, node_types):
        """Display a table of Casper's node types."""
        if not node_types:
            return
            
        table = Table(title="Node Type Status", title_style="bold magenta")
        table.add_column("Type", style="cyan")
        table.add_column("Total", justify="right")
        table.add_column("Available", justify="right")
        table.add_column("Util %", justify="right")

        for nt in sorted(node_types, key=lambda n: n.node_type):
            util_str = f"{nt.utilization_percent:.1f}%" if nt.utilization_percent is not None else "N/A"
            table.add_row(nt.node_type, str(nt.nodes_total), str(nt.nodes_available), util_str)
        self.console.print(table)
        
    def _display_queues(self, queues, show_gpus=False):
        """Display a table of queue statuses."""
        if not queues:
            return

        table = Table(title="Queue Status", title_style="bold magenta")
        table.add_column("Queue", style="cyan")
        table.add_column("Running Jobs", justify="right")
        table.add_column("Pending Jobs", justify="right")
        table.add_column("Cores Alloc", justify="right")
        if show_gpus:
            table.add_column("GPUs Alloc", justify="right")

        for q in sorted(queues, key=lambda q: q.queue_name):
            row = [
                q.queue_name,
                str(q.running_jobs),
                str(q.pending_jobs),
                f"{q.cores_allocated:,}"
            ]
            if show_gpus:
                row.append(str(q.gpus_allocated))
            table.add_row(*row)
        self.console.print(table)

    def _display_filesystems(self, filesystems):
        """Display a table of filesystem statuses."""
        if not filesystems:
            return

        table = Table(title="Filesystem Status", title_style="bold magenta")
        table.add_column("Filesystem", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Capacity (TB)", justify="right")
        table.add_column("Used (TB)", justify="right")
        table.add_column("Utilization", justify="center", width=30)
        
        for fs in sorted(filesystems, key=lambda f: f.filesystem_name):
            status_text = Text("● Online", style="green") if fs.available else Text("● Offline", style="red")
            if fs.degraded:
                status_text.append(" (degraded)", style="yellow")
            
            util_bar = ProgressBar(total=100, completed=fs.utilization_percent or 0)
            
            table.add_row(
                fs.filesystem_name,
                status_text,
                f"{fs.capacity_tb:,.1f}" if fs.capacity_tb is not None else "N/A",
                f"{fs.used_tb:,.1f}" if fs.used_tb is not None else "N/A",
                util_bar
            )
        self.console.print(table)

    def _display_reservations(self, system_name):
        """Display active or upcoming reservations."""
        from datetime import datetime
        reservations = self.session.query(ResourceReservation).filter(
            ResourceReservation.system_name == system_name,
            ResourceReservation.end_time >= datetime.now()
        ).order_by(ResourceReservation.start_time).all()

        if not reservations:
            return

        self.console.print("\n[bold magenta]Upcoming Reservations[/bold magenta]")
        for res in reservations:
            res_panel = Panel(
                f"[bold]{res.description or 'No description'}[/bold]\n"
                f"Time: {res.start_time.strftime('%Y-%m-%d %H:%M')} to {res.end_time.strftime('%Y-%m-%d %H:%M')}\n"
                f"Nodes: {res.node_count or 'N/A'} | Partition: {res.partition or 'N/A'}",
                title=f"[yellow]{res.reservation_name}[/yellow]",
                border_style="yellow"
            )
            self.console.print(res_panel)


# ========================================================================
# Main Program
# ========================================================================
def main():
    """Main entry point for the CLI."""
    try:
        with SystemStatusCLI() as cli:
            exit_code = cli.run()
            sys.exit(exit_code)
    except Exception as e:
        Console().print(f"[bold red]❌ Fatal error during CLI initialization: {e}[/bold red]")
        sys.exit(2)


if __name__ == '__main__':
    main()
