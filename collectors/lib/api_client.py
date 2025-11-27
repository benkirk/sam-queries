"""
SAM Status Dashboard API client.
"""

import json
import logging
import time
import requests

try:
    from .exceptions import APIError, APIAuthError, APIValidationError
except ImportError:
    from exceptions import APIError, APIAuthError, APIValidationError


class SAMAPIClient:
    """Client for SAM Status Dashboard API."""

    def __init__(self, base_url, username, password, timeout=30):
        self.base_url = base_url.rstrip('/')
        self.auth = (username, password)
        self.timeout = timeout
        self.session = requests.Session()
        self.logger = logging.getLogger(__name__)

    def post_status(self, system, data, max_retries=3, dry_run=False):
        """
        Post status data to API with retry logic.

        Args:
            system: 'derecho' or 'casper'
            data: Status data dict
            max_retries: Number of retry attempts
            dry_run: If True, log data but don't post

        Returns:
            API response dict

        Raises:
            APIError: If all retries fail
        """
        url = f"{self.base_url}/api/v1/status/{system}"

        if dry_run:
            self.logger.info(f"[DRY RUN] Would POST to {url}")
            self.logger.info(f"[DRY RUN] Data:\n{json.dumps(data, indent=2)}")
            return {'success': True, 'message': 'Dry run - no data posted'}

        for attempt in range(max_retries):
            try:
                response = self.session.post(
                    url,
                    json=data,
                    auth=self.auth,
                    timeout=self.timeout,
                    headers={'Content-Type': 'application/json'}
                )

                response.raise_for_status()
                result = response.json()

                self.logger.info(
                    f"✓ Posted {system} status: status_id={result.get('status_id')}"
                )
                return result

            except requests.exceptions.HTTPError as e:
                if e.response.status_code in (401, 403):
                    # Don't retry auth errors
                    raise APIAuthError(f"Authentication failed: {e}")
                elif e.response.status_code == 400:
                    # Don't retry validation errors
                    error_detail = e.response.text
                    try:
                        error_json = e.response.json()
                        error_detail = json.dumps(error_json, indent=2)
                    except:
                        pass
                    raise APIValidationError(f"Invalid data: {error_detail}")
                else:
                    # Retry server errors
                    if attempt == max_retries - 1:
                        raise APIError(f"HTTP error after {max_retries} attempts: {e}")

                    wait = 2 ** attempt
                    self.logger.warning(
                        f"HTTP {e.response.status_code}, retry {attempt+1}/{max_retries} in {wait}s"
                    )
                    time.sleep(wait)

            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise APIError(f"Network error after {max_retries} attempts: {e}")

                wait = 2 ** attempt
                self.logger.warning(
                    f"Network error, retry {attempt+1}/{max_retries} in {wait}s: {e}"
                )
                time.sleep(wait)

        raise APIError(f"Failed to post data after {max_retries} attempts")
