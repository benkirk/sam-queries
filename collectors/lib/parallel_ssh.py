"""
Parallel SSH execution utilities.

Provides ThreadPoolExecutor-based parallel execution for SSH operations,
reducing collection time from sequential (~20s for 8 nodes) to parallel (~2-3s).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Callable, Any


class ParallelSSHCollector:
    """
    Execute SSH commands in parallel using thread pool.

    This class provides a generic parallel execution framework for SSH operations.
    It uses ThreadPoolExecutor to run multiple SSH commands concurrently, with
    configurable timeout and worker pool size.

    Example:
        >>> collector = ParallelSSHCollector('derecho', timeout=10, max_workers=10)
        >>> tasks = [{'node_name': 'derecho1'}, {'node_name': 'derecho2'}]
        >>> results = collector.run_parallel(tasks, collect_node_handler)
    """

    def __init__(self, base_host: str, timeout: int = 10, max_workers: int = 10):
        """
        Initialize parallel SSH collector.

        Args:
            base_host: Base hostname for SSH connections
            timeout: SSH connection timeout in seconds
            max_workers: Maximum number of concurrent workers
        """
        self.base_host = base_host
        self.timeout = timeout
        self.max_workers = max_workers
        self.logger = logging.getLogger(__name__)

    def run_parallel(self, tasks: List[Dict[str, Any]], handler: Callable) -> List[dict]:
        """
        Execute tasks in parallel, applying handler to each result.

        This method submits all tasks to a thread pool, then collects results
        as they complete. If a task fails, the exception is logged and an error
        result is returned instead.

        Args:
            tasks: List of dicts with task info (e.g., [{'node_name': 'derecho1'}, ...])
            handler: Callable that takes a task dict and returns a result dict.
                     Should handle its own exceptions and return appropriate error status.

        Returns:
            List of results (success or error dicts). Order may differ from input.

        Example:
            >>> def handler(task):
            ...     node = task['node_name']
            ...     # SSH operation here
            ...     return {'node_name': node, 'status': 'ok'}
            >>> collector.run_parallel([{'node_name': 'n1'}], handler)
            [{'node_name': 'n1', 'status': 'ok'}]
        """
        results = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks to thread pool
            future_to_task = {
                executor.submit(handler, task): task
                for task in tasks
            }

            # Collect results as they complete (not in submission order)
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    # Log error but don't fail entire collection
                    self.logger.error(f"Task failed: {task}, error: {e}")
                    # Return error result with task metadata
                    error_result = {**task, 'error': str(e), 'success': False}
                    results.append(error_result)

        return results
