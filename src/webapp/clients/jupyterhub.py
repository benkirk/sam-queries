"""
JupyterHub API Client

Provides a client for querying the JupyterHub Hub API to retrieve real-time
statistics about active users, sessions, and job breakdowns.

This client is independent of the existing SSH-based collector and provides
real-time data with caching support.
"""

import os
import socket
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from functools import wraps

import requests
import urllib3

# Disable SSL warnings (matches jhstat script behavior)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class JupyterHubAPIError(Exception):
    """Base exception for JupyterHub API errors."""
    pass


class JupyterHubAuthError(JupyterHubAPIError):
    """Authentication error with JupyterHub API."""
    pass


class JupyterHubConnectionError(JupyterHubAPIError):
    """Connection error with JupyterHub API."""
    pass


def retry_on_error(max_attempts=3, backoff_seconds=1):
    """
    Decorator to retry function calls on transient errors.

    Args:
        max_attempts: Maximum number of retry attempts
        backoff_seconds: Initial backoff time in seconds (exponential)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError) as e:
                    if attempt == max_attempts:
                        raise JupyterHubConnectionError(
                            f"Failed after {max_attempts} attempts: {str(e)}"
                        )
                    wait_time = backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        f"Attempt {attempt} failed, retrying in {wait_time}s: {str(e)}"
                    )
                    import time
                    time.sleep(wait_time)
            return None
        return wrapper
    return decorator


class JupyterHubClient:
    """
    Client for JupyterHub Hub API with caching and error handling.

    This client queries the JupyterHub Hub API to retrieve real-time statistics
    about active users and sessions. All statistics are calculated by parsing
    the API response from /hub/api/users?state=active.

    Features:
    - Flexible token resolution (parameter, env var, token file)
    - In-memory caching with configurable TTL
    - Retry logic for transient errors
    - Comprehensive error handling
    - Statistics calculation matching jhstat script

    Example:
        >>> client = JupyterHubClient(
        ...     base_url='https://jupyterhub.hpc.ucar.edu',
        ...     instance='stable'
        ... )
        >>> stats = client.get_statistics()
        >>> print(f"Active users: {stats['active_users']}")
    """

    def __init__(
        self,
        base_url: str = 'https://jupyterhub.hpc.ucar.edu',
        instance: str = 'stable',
        api_token: Optional[str] = None,
        cache_ttl: int = 300,
        timeout: int = 30
    ):
        """
        Initialize JupyterHub API client.

        Args:
            base_url: Base URL for JupyterHub (without trailing slash)
            instance: JupyterHub instance ('stable' or 'dev')
            api_token: API token (optional, falls back to env var or file)
            cache_ttl: Cache time-to-live in seconds (default: 300 = 5 minutes)
            timeout: Request timeout in seconds (default: 30)

        Raises:
            JupyterHubAuthError: If no valid API token can be found
        """
        self.base_url = base_url.rstrip('/')
        self.instance = instance
        self.cache_ttl = cache_ttl
        self.timeout = timeout

        # Resolve API token from multiple sources
        self.api_token = self._resolve_token(api_token)

        # Initialize cache: {endpoint: (data, expiry_time)}
        self._cache: Dict[str, tuple[Any, datetime]] = {}

        logger.info(
            f"Initialized JupyterHubClient for {instance} instance "
            f"(cache_ttl={cache_ttl}s)"
        )

    def _resolve_token(self, token: Optional[str]) -> str:
        """
        Resolve API token from multiple sources with priority:
        1. Explicit parameter
        2. JUPYTERHUB_API_TOKEN environment variable
        3. Token file: /ncar/usr/jupyterhub.hpc.ucar.edu/.{instance}_metrics_api_token

        Args:
            token: Explicit token (highest priority)

        Returns:
            Resolved API token

        Raises:
            JupyterHubAuthError: If no valid token found
        """
        # Priority 1: Explicit parameter
        if token:
            logger.debug("Using explicit API token")
            return token

        # Priority 2: Environment variable
        env_token = os.getenv('JUPYTERHUB_API_TOKEN')
        if env_token:
            logger.debug("Using API token from environment variable")
            return env_token

        # Priority 3: Token file
        token_file = f'/ncar/usr/jupyterhub.hpc.ucar.edu/.{self.instance}_metrics_api_token'
        if os.path.exists(token_file):
            try:
                with open(token_file, 'r') as f:
                    file_token = f.read().strip()
                    if file_token:
                        logger.debug(f"Using API token from file: {token_file}")
                        return file_token
            except (IOError, OSError) as e:
                logger.warning(f"Failed to read token file {token_file}: {str(e)}")

        # No token found
        raise JupyterHubAuthError(
            f"No API token found. Set JUPYTERHUB_API_TOKEN environment variable "
            f"or provide token file at {token_file}"
        )

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """
        Get data from cache if available and not expired.

        Args:
            key: Cache key

        Returns:
            Cached data or None if not found/expired
        """
        if key not in self._cache:
            return None

        data, expiry_time = self._cache[key]
        if datetime.now() > expiry_time:
            logger.debug(f"Cache expired for key: {key}")
            del self._cache[key]
            return None

        logger.debug(f"Cache hit for key: {key}")
        return data

    def _set_in_cache(self, key: str, data: Any) -> None:
        """
        Store data in cache with TTL.

        Args:
            key: Cache key
            data: Data to cache
        """
        expiry_time = datetime.now() + timedelta(seconds=self.cache_ttl)
        self._cache[key] = (data, expiry_time)
        logger.debug(f"Cached data for key: {key} (expires in {self.cache_ttl}s)")

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        logger.info("Cache cleared")

    @retry_on_error(max_attempts=3, backoff_seconds=1)
    def _make_request(self, endpoint: str) -> Any:
        """
        Make HTTP request to JupyterHub API.

        Args:
            endpoint: API endpoint (e.g., 'users?state=active')

        Returns:
            Parsed JSON response

        Raises:
            JupyterHubAuthError: On authentication failure (401, 403)
            JupyterHubConnectionError: On connection failure
            JupyterHubAPIError: On other API errors
        """
        url = f'{self.base_url}/{self.instance}/hub/api/{endpoint}'

        headers = {
            'Authorization': f'token {self.api_token}'
        }

        try:
            logger.debug(f"Making request to: {url}")
            response = requests.get(
                url,
                headers=headers,
                verify=False,  # Matches jhstat script behavior
                timeout=self.timeout
            )

            # Handle HTTP errors
            if response.status_code == 401:
                raise JupyterHubAuthError("Invalid API token (401 Unauthorized)")
            elif response.status_code == 403:
                raise JupyterHubAuthError("Access forbidden (403 Forbidden)")
            elif response.status_code >= 400:
                raise JupyterHubAPIError(
                    f"API error: {response.status_code} - {response.text}"
                )

            return response.json()

        except requests.exceptions.Timeout as e:
            raise JupyterHubConnectionError(f"Request timeout: {str(e)}")
        except requests.exceptions.ConnectionError as e:
            raise JupyterHubConnectionError(f"Connection failed: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise JupyterHubAPIError(f"Request failed: {str(e)}")

    def get_active_users(self, use_cache: bool = True) -> List[Dict]:
        """
        Get list of active users from JupyterHub API.

        This calls GET /hub/api/users?state=active and returns the raw response.

        Args:
            use_cache: Use cached data if available (default: True)

        Returns:
            List of user dicts with server information

        Example response structure:
            [
                {
                    "name": "username",
                    "servers": {
                        "": {  # Default server
                            "state": {"resource": "cr-login", ...},
                            "started": "2025-12-16T10:00:00.000000Z",
                            "last_activity": "2025-12-16T12:00:00.000000Z",
                            ...
                        }
                    }
                }
            ]
        """
        cache_key = f'{self.instance}:users'

        # Check cache
        if use_cache:
            cached_data = self._get_from_cache(cache_key)
            if cached_data is not None:
                return cached_data

        # Fetch from API
        users = self._make_request('users?state=active')

        # Cache result
        if use_cache:
            self._set_in_cache(cache_key, users)

        return users

    def get_proxy_routes(self, use_cache: bool = True) -> Dict:
        """
        Get proxy routing information from JupyterHub API.

        This calls GET /hub/api/proxy and returns the raw response.

        Args:
            use_cache: Use cached data if available (default: True)

        Returns:
            Dict mapping URLs to route information
        """
        cache_key = f'{self.instance}:proxy'

        # Check cache
        if use_cache:
            cached_data = self._get_from_cache(cache_key)
            if cached_data is not None:
                return cached_data

        # Fetch from API
        routes = self._make_request('proxy')

        # Cache result
        if use_cache:
            self._set_in_cache(cache_key, routes)

        return routes

    def _calculate_statistics(self, users: List[Dict]) -> Dict[str, Any]:
        """
        Calculate statistics from JupyterHub API users response.

        This parses the response from GET /hub/api/users?state=active to calculate:
        - active_users: Count of unique usernames
        - active_sessions: Total count of server sessions
        - casper_login_jobs: Sessions with resource='cr-login'
        - casper_batch_jobs: Sessions with resource='cr-batch'
        - derecho_batch_jobs: Sessions with resource='de-batch'
        - broken_jobs: Sessions missing required child_state fields

        Args:
            users: List of user dicts from JupyterHub API

        Returns:
            Dict with calculated statistics
        """
        unique_users = set()
        job_counts = {
            'casper_login': 0,
            'casper_batch': 0,
            'derecho_batch': 0,
            'broken': 0
        }
        total_sessions = 0

        # Parse each user from API response
        for user in users:
            username = user.get('name')
            if username:
                unique_users.add(username)

            # Each user can have multiple servers (default, named servers)
            servers = user.get('servers', {})
            for server_name, server in servers.items():
                total_sessions += 1

                # Get resource type from server state
                try:
                    resource = server['state']['resource']
                except (KeyError, TypeError):
                    logger.warning(f"Missing resource for user {username}, server {server_name}")
                    job_counts['broken'] += 1
                    continue

                # Classify by resource type (from API field)
                if resource == 'cr-login':
                    job_counts['casper_login'] += 1
                elif resource == 'cr-batch':
                    job_counts['casper_batch'] += 1
                elif resource == 'de-batch':
                    job_counts['derecho_batch'] += 1

                # Detect broken jobs (missing required fields in child_state)
                try:
                    child_state = server['state'].get('child_state', {})
                except (KeyError, TypeError, AttributeError):
                    child_state = {}

                if resource in ('cr-login', 'cr-batch', 'de-batch'):
                    # PBS jobs require job_id
                    if 'job_id' not in child_state:
                        job_counts['broken'] += 1
                else:
                    # Other jobs require remote_ip and pid
                    if 'remote_ip' not in child_state or 'pid' not in child_state:
                        job_counts['broken'] += 1

        return {
            'active_users': len(unique_users),
            'active_sessions': total_sessions,
            'casper_login_jobs': job_counts['casper_login'],
            'casper_batch_jobs': job_counts['casper_batch'],
            'derecho_batch_jobs': job_counts['derecho_batch'],
            'broken_jobs': job_counts['broken'],
            'instance': self.instance,
            'timestamp': datetime.utcnow().isoformat(),
            'cached': False
        }

    def get_statistics(self, use_cache: bool = True) -> Dict[str, Any]:
        """
        Get comprehensive JupyterHub statistics.

        This is the main method for retrieving statistics. It fetches active users
        from the API and calculates all metrics.

        Args:
            use_cache: Use cached data if available (default: True)

        Returns:
            Dict with statistics:
                - active_users (int): Count of unique usernames
                - active_sessions (int): Total active sessions
                - casper_login_jobs (int): Casper login sessions
                - casper_batch_jobs (int): Casper batch sessions
                - derecho_batch_jobs (int): Derecho batch sessions
                - broken_jobs (int): Broken/invalid sessions
                - instance (str): JupyterHub instance name
                - timestamp (str): ISO format timestamp
                - cached (bool): Whether data came from cache

        Example:
            >>> stats = client.get_statistics()
            >>> print(f"Active users: {stats['active_users']}")
            >>> print(f"Casper login jobs: {stats['casper_login_jobs']}")
        """
        cache_key = f'{self.instance}:statistics'

        # Check cache
        if use_cache:
            cached_data = self._get_from_cache(cache_key)
            if cached_data is not None:
                # Mark as cached
                cached_data['cached'] = True
                return cached_data

        # Fetch users and calculate statistics
        users = self.get_active_users(use_cache=False)
        stats = self._calculate_statistics(users)

        # Cache result
        if use_cache:
            self._set_in_cache(cache_key, stats)

        return stats

    @staticmethod
    def _get_hostname(ip: str) -> str:
        """
        Resolve hostname from IP address.

        Args:
            ip: IP address string

        Returns:
            Hostname (short form) or IP if resolution fails
        """
        try:
            name = socket.gethostbyaddr(ip)[0].split('.')[0]
            return name
        except (socket.herror, socket.gaierror):
            return ip

    @staticmethod
    def _parse_duration(started_str: str, now: Optional[datetime] = None) -> int:
        """
        Calculate duration in minutes from start time.

        Args:
            started_str: ISO format start time (e.g., '2025-12-16T10:00:00.000000Z')
            now: Current time (default: utcnow())

        Returns:
            Duration in minutes
        """
        if now is None:
            now = datetime.utcnow()

        try:
            started = datetime.strptime(started_str, '%Y-%m-%dT%H:%M:%S.%fZ')
            duration = (now - started).total_seconds() / 60
            return int(duration)
        except (ValueError, AttributeError):
            return 0

    def get_detailed_sessions(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """
        Get detailed information about all active sessions.

        This parses the users API response to extract detailed session information
        including username, server name, resource type, location, and durations.

        Args:
            use_cache: Use cached data if available (default: True)

        Returns:
            List of session dicts with fields:
                - username (str): User's username
                - server_name (str): Server name ('default' or named)
                - resource_type (str): Resource type ('cr-login', 'cr-batch', 'de-batch', etc.)
                - location (str): Job location (PBS job ID or host:pid)
                - duration_minutes (int): Session duration in minutes
                - idle_minutes (int): Time since last activity in minutes
                - started (str): ISO format start time
                - last_activity (str): ISO format last activity time

        Example:
            >>> sessions = client.get_detailed_sessions()
            >>> for session in sessions:
            ...     print(f"{session['username']}: {session['resource_type']} "
            ...           f"for {session['duration_minutes']} minutes")
        """
        cache_key = f'{self.instance}:sessions'

        # Check cache
        if use_cache:
            cached_data = self._get_from_cache(cache_key)
            if cached_data is not None:
                return cached_data

        # Fetch users
        users = self.get_active_users(use_cache=False)
        now = datetime.utcnow()

        sessions = []

        for user in users:
            username = user.get('name', 'unknown')

            for server_key, server in user.get('servers', {}).items():
                # Server name: empty key = 'default', otherwise use key
                server_name = 'default' if server_key == '' else server_key

                try:
                    resource = server['state']['resource']
                    child_state = server['state'].get('child_state', {})

                    # Determine location
                    if resource in ('cr-login', 'cr-batch', 'de-batch'):
                        # PBS jobs: use job_id
                        location = child_state.get('job_id', 'unknown')
                    else:
                        # Other jobs: use host:pid
                        remote_ip = child_state.get('remote_ip', 'unknown')
                        pid = child_state.get('pid', 0)
                        hostname = self._get_hostname(remote_ip)
                        location = f'{hostname}:{pid}'

                    # Calculate durations
                    started_str = server.get('started', '')
                    last_activity_str = server.get('last_activity', '')

                    duration_minutes = self._parse_duration(started_str, now)
                    idle_minutes = self._parse_duration(last_activity_str, now)

                    sessions.append({
                        'username': username,
                        'server_name': server_name,
                        'resource_type': resource,
                        'location': location,
                        'duration_minutes': duration_minutes,
                        'idle_minutes': idle_minutes,
                        'started': started_str,
                        'last_activity': last_activity_str
                    })

                except (KeyError, TypeError) as e:
                    logger.warning(
                        f"Failed to parse session for user {username}, "
                        f"server {server_name}: {str(e)}"
                    )
                    continue

        # Cache result
        if use_cache:
            self._set_in_cache(cache_key, sessions)

        return sessions

    def __repr__(self) -> str:
        """String representation of client."""
        return (
            f"JupyterHubClient(instance='{self.instance}', "
            f"base_url='{self.base_url}', cache_ttl={self.cache_ttl})"
        )
