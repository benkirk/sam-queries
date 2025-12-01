"""
Configuration management for collectors.
"""

import os
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

try:
    from .exceptions import ConfigError
except ImportError:
    from exceptions import ConfigError


class CollectorConfig:
    """Configuration loader for collectors."""

    def __init__(self, system, config_dir=None):
        self.system = system
        self.config_dir = config_dir or os.path.join(
            os.path.dirname(__file__), '..', system
        )
        self.logger = logging.getLogger(__name__)

        self._load_env()
        self._load_yaml()

    def _load_env(self):
        """Load environment variables from .env file."""
        # Look for .env in parent directory
        env_file=find_dotenv()
        load_dotenv(env_file)
        self.logger.debug(f"Loaded environment from {env_file}")

        self.api_url = os.getenv('STATUS_API_URL', 'http://localhost:5050')
        self.api_user = os.getenv('STATUS_API_USER', 'collector')
        self.api_password = os.getenv('STATUS_API_PASSWORD')

        if not self.api_password:
            raise ConfigError("STATUS_API_PASSWORD required in .env or environment")

        # Optional overrides
        self.pbs_timeout = int(os.getenv('PBS_COMMAND_TIMEOUT', '30'))
        self.ssh_timeout = int(os.getenv('SSH_TIMEOUT', '10'))
        self.api_timeout = int(os.getenv('API_TIMEOUT', '30'))

    def _load_yaml(self):
        """Load system-specific YAML configuration."""
        config_file = os.path.join(self.config_dir, 'config.yaml')

        if not os.path.exists(config_file):
            raise ConfigError(f"Config file not found: {config_file}")

        with open(config_file, 'r') as f:
            self.yaml_config = yaml.safe_load(f)

        # Extract common config
        self.system_name = self.yaml_config.get('system_name', self.system)
        self.pbs_host = self.yaml_config['pbs_host']
        self.login_nodes = self.yaml_config.get('login_nodes', [])
        self.filesystems = self.yaml_config.get('filesystems', [])
        self.queues = self.yaml_config.get('queues', [])

        self.logger.debug(f"Loaded config from {config_file}")

    def get_node_type_config(self):
        """Get node type configuration (if exists)."""
        node_types_file = os.path.join(self.config_dir, 'node_types.yaml')
        if os.path.exists(node_types_file):
            with open(node_types_file, 'r') as f:
                return yaml.safe_load(f)
        return {}
