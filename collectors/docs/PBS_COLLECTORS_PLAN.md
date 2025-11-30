# PBS-Based Data Collector Architecture
**Implementation Plan for Derecho and Casper HPC Systems**

## Executive Summary

This plan details a production-ready PBS-based data collector architecture that gathers HPC metrics from Derecho and Casper systems and posts to the SAM Status Dashboard API. The design maximizes code reuse between collectors while accommodating system-specific differences.

**Key Design Principles**:
- **DRY (Don't Repeat Yourself)**: Shared PBS parsing, API client, and utilities
- **Fail-safe**: Partial data collection continues on individual component failure
- **Observable**: Comprehensive logging with dry-run and verbose modes
- **Secure**: Environment-based credentials, minimal permissions
- **Production-ready**: Retry logic, error handling, monitoring integration

---

## 1. Directory Structure

```
collectors/
├── README.md                    # Setup and usage documentation
├── requirements.txt             # Python dependencies: requests, python-dotenv, pyyaml
├── .env.example                 # Credential template
├── Makefile                     # Installation and deployment helpers
│
├── lib/                         # Shared library code (90% reuse)
│   ├── __init__.py
│   ├── pbs_client.py            # PBS command execution wrapper
│   ├── api_client.py            # SAM API HTTP client with retry logic
│   ├── config.py                # Configuration management (.env + YAML)
│   ├── logging_utils.py         # Structured logging setup
│   ├── ssh_utils.py             # SSH operations for login nodes
│   └── parsers/                 # Data parsing modules
│       ├── __init__.py
│       ├── nodes.py             # Parse pbsnodes -F json
│       ├── jobs.py              # Parse qstat -f -F json
│       ├── queues.py            # Parse qstat -Qa text output
│       ├── filesystems.py       # Parse df output
│       └── reservations.py      # Parse pbs_rstat -f
│
├── derecho/                     # Derecho-specific (10% custom)
│   ├── collector.py             # Main executable
│   ├── config.yaml              # Derecho configuration
│   └── login_nodes.txt          # derecho1-8 with types
│
├── casper/                      # Casper-specific (10% custom)
│   ├── collector.py             # Main executable
│   ├── config.yaml              # Casper configuration
│   ├── node_types.yaml          # Node type hardware specs
│   └── login_nodes.txt          # casper-login1-2
│
├── tests/                       # Testing infrastructure
│   ├── test_pbs_parser.py
│   ├── test_api_client.py
│   ├── mock_data/               # Sample outputs
│   │   ├── pbsnodes_derecho.json
│   │   ├── pbsnodes_casper.json
│   │   ├── qstat_jobs.json
│   │   └── qstat_queues.txt
│   └── integration/
│       ├── test_derecho_collector.py
│       └── test_casper_collector.py
│
└── deploy/                      # Deployment automation
    ├── install.sh
    ├── crontab.template
    ├── logrotate.conf
```

---

## 2. Shared Components Design

### 2.1 PBS Client (`lib/pbs_client.py`)

**Purpose**: Execute PBS commands and handle errors uniformly.

**Key Functions**:
```python
class PBSClient:
    """
    Wrapper for PBS command execution.
    Handles SSH invocation, timeouts, and error capture.
    """

    def __init__(self, host: str, timeout: int = 30):
        self.host = host
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def run_command(self, cmd: str, json_output: bool = False) -> dict | str:
        """
        Execute PBS command via SSH.

        Args:
            cmd: Command to run (e.g., "pbsnodes -aj -F json")
            json_output: If True, parse JSON response

        Returns:
            Parsed JSON dict or raw string output

        Raises:
            PBSCommandError: If command fails or times out
        """
        full_cmd = f'ssh -o ConnectTimeout={self.timeout} {self.host} "{cmd}"'
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout
        )

        if result.returncode != 0:
            raise PBSCommandError(f"Command failed: {cmd}", result.stderr)

        if json_output:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as e:
                raise PBSParseError(f"Invalid JSON from {cmd}", e)

        return result.stdout

    def get_nodes_json(self) -> dict:
        """Execute pbsnodes -aj -F json"""
        return self.run_command("pbsnodes -aj -F json", json_output=True)

    def get_jobs_json(self) -> dict:
        """Execute qstat -f -F json"""
        return self.run_command("qstat -f -F json", json_output=True)

    def get_queue_summary(self) -> str:
        """Execute qstat -Qa"""
        return self.run_command("qstat -Qa")

    def get_reservations(self) -> str:
        """Execute pbs_rstat -f"""
        return self.run_command("pbs_rstat -f")
```

**Error Handling Strategy**:
- `PBSCommandError`: SSH/command execution failures
- `PBSParseError`: JSON parsing failures
- Both inherit from `PBSError` for catch-all handling
- Log detailed error info, return None to allow partial collection

---

### 2.2 Node Parser (`lib/parsers/nodes.py`)

**Purpose**: Parse `pbsnodes -aj -F json` output into API-ready format.

**Key Functions**:
```python
class NodeParser:
    """Parse pbsnodes JSON output."""

    @staticmethod
    def parse_nodes(pbsnodes_json: dict, system_type: str) -> dict:
        """
        Parse pbsnodes JSON into node statistics.

        Args:
            pbsnodes_json: Output from pbsnodes -aj -F json
            system_type: 'derecho' or 'casper'

        Returns:
            {
                'cpu_nodes_total': int,
                'cpu_nodes_available': int,
                'cpu_nodes_down': int,
                'cpu_nodes_reserved': int,
                'gpu_nodes_total': int,  # Derecho only
                ...
                'cpu_cores_total': int,
                'cpu_cores_allocated': int,
                ...
            }
        """
        nodes = pbsnodes_json.get('nodes', {})

        stats = {
            'cpu_nodes_total': 0,
            'cpu_nodes_available': 0,
            'cpu_nodes_down': 0,
            'cpu_nodes_reserved': 0,
            'cpu_cores_total': 0,
            'cpu_cores_allocated': 0,
            'cpu_cores_idle': 0,
            'memory_total_gb': 0.0,
            'memory_allocated_gb': 0.0,
        }

        if system_type == 'derecho':
            stats.update({
                'gpu_nodes_total': 0,
                'gpu_nodes_available': 0,
                'gpu_nodes_down': 0,
                'gpu_nodes_reserved': 0,
                'gpu_count_total': 0,
                'gpu_count_allocated': 0,
                'gpu_count_idle': 0,
            })

        for node_name, node_data in nodes.items():
            state = node_data.get('state', '')

            # Determine node type
            if system_type == 'derecho':
                is_gpu = 'gpu' in node_data.get('resources_available', {}).get('partition', '')
                node_category = 'gpu' if is_gpu else 'cpu'
            else:  # casper
                node_category = 'cpu'  # Will be split by node_type later

            # Count nodes by state
            if 'down' in state or 'offline' in state:
                stats[f'{node_category}_nodes_down'] += 1
            elif 'resv' in state:
                stats[f'{node_category}_nodes_reserved'] += 1
            elif 'free' in state or 'idle' in state:
                stats[f'{node_category}_nodes_available'] += 1

            stats[f'{node_category}_nodes_total'] += 1

            # Aggregate resources
            resources = node_data.get('resources_available', {})
            resources_assigned = node_data.get('resources_assigned', {})

            ncpus = int(resources.get('ncpus', 0))
            ncpus_allocated = int(resources_assigned.get('ncpus', 0))
            stats['cpu_cores_total'] += ncpus
            stats['cpu_cores_allocated'] += ncpus_allocated
            stats['cpu_cores_idle'] += (ncpus - ncpus_allocated)

            # Memory (convert to GB)
            mem_kb = parse_memory(resources.get('mem', '0kb'))
            mem_alloc_kb = parse_memory(resources_assigned.get('mem', '0kb'))
            stats['memory_total_gb'] += mem_kb / (1024 * 1024)
            stats['memory_allocated_gb'] += mem_alloc_kb / (1024 * 1024)

            # GPUs (if present)
            if system_type == 'derecho' and is_gpu:
                ngpus = int(resources.get('ngpus', 0))
                ngpus_allocated = int(resources_assigned.get('ngpus', 0))
                stats['gpu_count_total'] += ngpus
                stats['gpu_count_allocated'] += ngpus_allocated
                stats['gpu_count_idle'] += (ngpus - ngpus_allocated)

        # Calculate utilization percentages
        if stats['cpu_cores_total'] > 0:
            stats['cpu_utilization_percent'] = round(
                (stats['cpu_cores_allocated'] / stats['cpu_cores_total']) * 100, 2
            )

        if system_type == 'derecho' and stats['gpu_count_total'] > 0:
            stats['gpu_utilization_percent'] = round(
                (stats['gpu_count_allocated'] / stats['gpu_count_total']) * 100, 2
            )

        if stats['memory_total_gb'] > 0:
            stats['memory_utilization_percent'] = round(
                (stats['memory_allocated_gb'] / stats['memory_total_gb']) * 100, 2
            )

        return stats

    @staticmethod
    def parse_casper_node_types(pbsnodes_json: dict, node_type_config: dict) -> list:
        """
        Parse Casper nodes by node type.

        Args:
            pbsnodes_json: Output from pbsnodes
            node_type_config: YAML config with node type definitions

        Returns:
            List of node type status dicts for API
        """
        node_types = {}

        for node_name, node_data in pbsnodes_json.get('nodes', {}).items():
            # Determine node type from name or resources
            node_type = classify_casper_node(node_name, node_data, node_type_config)

            if node_type not in node_types:
                node_types[node_type] = {
                    'node_type': node_type,
                    'nodes_total': 0,
                    'nodes_available': 0,
                    'nodes_down': 0,
                    'nodes_allocated': 0,
                    **node_type_config.get(node_type, {})  # cores_per_node, gpu_model, etc.
                }

            state = node_data.get('state', '')
            node_types[node_type]['nodes_total'] += 1

            if 'down' in state or 'offline' in state:
                node_types[node_type]['nodes_down'] += 1
            elif 'free' in state or 'idle' in state:
                node_types[node_type]['nodes_available'] += 1
            elif 'job-busy' in state:
                node_types[node_type]['nodes_allocated'] += 1

        # Calculate utilization per type
        for nt_data in node_types.values():
            if nt_data['nodes_total'] > 0:
                allocated = nt_data['nodes_allocated']
                available = nt_data['nodes_available']
                total = nt_data['nodes_total']
                nt_data['utilization_percent'] = round((allocated / total) * 100, 2)

        return list(node_types.values())
```

**Sample Helper Functions**:
```python
def parse_memory(mem_str: str) -> int:
    """Convert PBS memory string (e.g., '256gb', '512mb') to KB."""
    mem_str = mem_str.lower()
    if 'tb' in mem_str:
        return int(float(mem_str.replace('tb', '')) * 1024 * 1024 * 1024)
    elif 'gb' in mem_str:
        return int(float(mem_str.replace('gb', '')) * 1024 * 1024)
    elif 'mb' in mem_str:
        return int(float(mem_str.replace('mb', '')) * 1024)
    elif 'kb' in mem_str:
        return int(float(mem_str.replace('kb', '')))
    return 0

def classify_casper_node(node_name: str, node_data: dict, config: dict) -> str:
    """
    Determine Casper node type from name or resources.

    Strategy:
    1. Check Qlist resources in node_data
    2. Check GPU resources in node_data
    3. Check memory size for bigmem classification
    4. Hostname fallback
    5. Default to 'htc'
    """
    # Name-based classification
    if 'csgv' in node_name.lower():
        return 'gpu-v100'
    elif 'csga' in node_name.lower():
        return 'gpu-a100'
    elif 'csgm' in node_name.lower():
        return 'gpu-mi100'

    # Resource-based classification
    resources = node_data.get('resources_available', {})

    # FIXME!!: Check QList

    # Check for GPUs
    if 'ngpus' in resources and int(resources['ngpus']) > 0:
        # Determine GPU model from features or config
        gpu_type = resources.get('gpu_type', '')
        if 'v100' in gpu_type.lower():
            return 'v100'
        elif 'a100' in gpu_type.lower():
            return 'a100'
        elif 'h100' in gpu_type.lower():
            return 'h100'
        elif 'mi300a' in gpu_type.lower():
            return 'mi100'

    # Check memory for bigmem
    mem_gb = parse_memory(resources.get('mem', '0')) / (1024 * 1024)
    if mem_gb >= 512:  # Threshold from config
        return 'bigmem'

    return 'standard'
```

---

### 2.3 Jobs Parser (`lib/parsers/jobs.py`)

**Purpose**: Parse `qstat -f -F json` output for job counts and user statistics.

```python
class JobParser:
    """Parse qstat job data."""

    @staticmethod
    def parse_jobs(qstat_json: dict) -> dict:
        """
        Parse qstat JSON into job statistics.

        Returns:
            {
                'running_jobs': int,
                'pending_jobs': int,
                'active_users': int,
            }
        """
        jobs = qstat_json.get('Jobs', {})

        running = 0
        pending = 0
        users = set()

        for job_id, job_data in jobs.items():
            state = job_data.get('job_state', '')
            user = job_data.get('Job_Owner', '').split('@')[0]

            if user:
                users.add(user)

            if state == 'R':  # Running
                running += 1
            elif state == 'Q':  # Queued
                pending += 1

        return {
            'running_jobs': running,
            'pending_jobs': pending,
            'active_users': len(users),
        }
```

---

### 2.4 Queue Parser (`lib/parsers/queues.py`)

**Purpose**: Parse `qstat -Qa` text output for per-queue statistics.

```python
class QueueParser:
    """Parse qstat -Qa output."""

    @staticmethod
    def parse_queues(qstat_output: str, qstat_json: dict) -> list:
        """
        Parse queue summary and detailed job data.

        Args:
            qstat_output: Text output from qstat -Qa
            qstat_json: JSON from qstat -f -F json (for per-queue breakdown)

        Returns:
            List of queue status dicts
        """
        queues = {}

        # Parse jobs by queue
        for job_id, job_data in qstat_json.get('Jobs', {}).items():
            queue = job_data.get('queue', 'unknown')
            state = job_data.get('job_state', '')
            user = job_data.get('Job_Owner', '').split('@')[0]

            resources = job_data.get('Resource_List', {})
            ncpus = int(resources.get('ncpus', 0))
            ngpus = int(resources.get('ngpus', 0))
            nodect = int(resources.get('nodect', 0))

            if queue not in queues:
                queues[queue] = {
                    'queue_name': queue,
                    'running_jobs': 0,
                    'pending_jobs': 0,
                    'active_users': set(),
                    'cores_allocated': 0,
                    'gpus_allocated': 0,
                    'nodes_allocated': 0,
                }

            if state == 'R':
                queues[queue]['running_jobs'] += 1
                queues[queue]['cores_allocated'] += ncpus
                queues[queue]['gpus_allocated'] += ngpus
                queues[queue]['nodes_allocated'] += nodect
            elif state == 'Q':
                queues[queue]['pending_jobs'] += 1

            if user:
                queues[queue]['active_users'].add(user)

        # Convert sets to counts
        result = []
        for q_data in queues.values():
            q_data['active_users'] = len(q_data['active_users'])
            result.append(q_data)

        return result
```

---

### 2.5 SSH Utilities (`lib/ssh_utils.py`)

**Purpose**: SSH to login nodes and gather uptime/load/user counts.

```python
class LoginNodeCollector:
    """Collect login node metrics via SSH."""

    def __init__(self, base_host: str, timeout: int = 10):
        self.base_host = base_host
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def collect_login_node_data(self, login_nodes: list) -> list:
        """
        Collect metrics from all login nodes.

        Args:
            login_nodes: List of dicts with 'name' and optionally 'type'
                Example: [{'name': 'derecho1', 'type': 'cpu'}, ...]

        Returns:
            List of login node status dicts for API
        """
        results = []

        for node_info in login_nodes:
            node_name = node_info['name']
            node_type = node_info.get('type')  # Derecho only

            try:
                data = self._collect_single_node(node_name)
                data['node_name'] = node_name
                if node_type:
                    data['node_type'] = node_type
                results.append(data)
            except Exception as e:
                self.logger.warning(f"Failed to collect from {node_name}: {e}")
                # Add degraded entry
                results.append({
                    'node_name': node_name,
                    'node_type': node_type,
                    'available': False,
                    'degraded': True,
                    'user_count': None,
                    'load_1min': None,
                    'load_5min': None,
                    'load_15min': None,
                })

        return results

    def _collect_single_node(self, node_name: str) -> dict:
        """Collect metrics from a single login node."""
        # SSH through base host to login node
        # Example: ssh derecho "ssh derecho1 'cat /proc/loadavg; who | wc -l'"

        cmd = f"""ssh -o ConnectTimeout={self.timeout} {self.base_host} "ssh {node_name} 'cat /proc/loadavg; echo ---; who | wc -l'" """

        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout
        )

        if result.returncode != 0:
            raise SSHError(f"Failed to connect to {node_name}")

        # Parse output
        parts = result.stdout.strip().split('---')
        loadavg = parts[0].strip().split()
        user_count = int(parts[1].strip())

        return {
            'available': True,
            'degraded': False,
            'user_count': user_count,
            'load_1min': float(loadavg[0]),
            'load_5min': float(loadavg[1]),
            'load_15min': float(loadavg[2]),
        }
```

---

### 2.6 Filesystem Parser (`lib/parsers/filesystems.py`)

**Purpose**: Parse `df` output for filesystem usage.

```python
class FilesystemParser:
    """Parse df output for filesystem status."""

    @staticmethod
    def parse_filesystems(df_output: str, fs_config: list) -> list:
        """
        Parse df output into filesystem status.

        Args:
            df_output: Output from 'BLOCKSIZE=TiB df <paths>'
            fs_config: List of filesystem names to track

        Returns:
            List of filesystem status dicts
        """
        filesystems = []

        for line in df_output.strip().split('\n'):
            if line.startswith('Filesystem') or not line.strip():
                continue

            parts = line.split()
            if len(parts) < 6:
                continue

            # df output: Filesystem  Size  Used  Avail  Use%  Mounted
            filesystem = parts[0]
            size_tb = float(parts[1].replace('T', ''))
            used_tb = float(parts[2].replace('T', ''))
            utilization = int(parts[4].replace('%', ''))
            mountpoint = parts[5]

            # Match to configured filesystem
            fs_name = None
            for configured_fs in fs_config:
                if configured_fs.lower() in mountpoint.lower():
                    fs_name = configured_fs
                    break

            if fs_name:
                filesystems.append({
                    'filesystem_name': fs_name,
                    'available': True,
                    'degraded': utilization > 90,  # Mark degraded if >90% full
                    'capacity_tb': size_tb,
                    'used_tb': used_tb,
                    'utilization_percent': float(utilization),
                })

        return filesystems
```

---

### 2.7 API Client (`lib/api_client.py`)

**Purpose**: HTTP client with retry logic for posting to SAM API.

```python
class SAMAPIClient:
    """Client for SAM Status Dashboard API."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.auth = (username, password)
        self.session = requests.Session()
        self.logger = logging.getLogger(__name__)

    def post_status(self, system: str, data: dict,
                   max_retries: int = 3, dry_run: bool = False) -> dict:
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
            self.logger.info(f"[DRY RUN] Data: {json.dumps(data, indent=2)}")
            return {'success': True, 'message': 'Dry run - no data posted'}

        for attempt in range(max_retries):
            try:
                response = self.session.post(
                    url,
                    json=data,
                    auth=self.auth,
                    timeout=30,
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
                    raise APIValidationError(f"Invalid data: {e.response.text}")
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
```

---

### 2.8 Configuration Management (`lib/config.py`)

**Purpose**: Load environment variables and YAML config files.

```python
class CollectorConfig:
    """Configuration loader for collectors."""

    def __init__(self, system: str, config_dir: str = None):
        self.system = system
        self.config_dir = config_dir or os.path.join(
            os.path.dirname(__file__), '..', system
        )
        self._load_env()
        self._load_yaml()

    def _load_env(self):
        """Load environment variables from .env file."""
        env_file = os.path.join(
            os.path.dirname(__file__), '..', '.env'
        )
        if os.path.exists(env_file):
            load_dotenv(env_file)

        self.api_url = os.getenv('STATUS_API_URL', 'http://localhost:5050')
        self.api_user = os.getenv('STATUS_API_USER')
        self.api_password = os.getenv('STATUS_API_PASSWORD')

        if not self.api_user or not self.api_password:
            raise ConfigError("STATUS_API_USER and STATUS_API_PASSWORD required in .env")

    def _load_yaml(self):
        """Load system-specific YAML configuration."""
        config_file = os.path.join(self.config_dir, 'config.yaml')

        if not os.path.exists(config_file):
            raise ConfigError(f"Config file not found: {config_file}")

        with open(config_file, 'r') as f:
            self.yaml_config = yaml.safe_load(f)

        # Extract common config
        self.pbs_host = self.yaml_config['pbs_host']
        self.login_nodes = self.yaml_config['login_nodes']
        self.filesystems = self.yaml_config.get('filesystems', [])
        self.queues = self.yaml_config.get('queues', [])

    def get_node_type_config(self) -> dict:
        """Get node type configuration (Casper only)."""
        if self.system == 'casper':
            node_types_file = os.path.join(self.config_dir, 'node_types.yaml')
            with open(node_types_file, 'r') as f:
                return yaml.safe_load(f)
        return {}
```

**Example config.yaml (Derecho)**:
```yaml
system_name: derecho
pbs_host: derecho

login_nodes:
  - name: derecho1
    type: cpu
  - name: derecho2
    type: cpu
  - name: derecho3
    type: cpu
  - name: derecho4
    type: cpu
  - name: derecho5
    type: gpu
  - name: derecho6
    type: gpu
  - name: derecho7
    type: gpu
  - name: derecho8
    type: gpu

filesystems:
  - glade
  - campaign
  - derecho_scratch

queues:
  - main
  - preempt
  - develop
  - cpudev
  - cpu
  - gpudev
  - gpu
```

**Example node_types.yaml (Casper)**: FIXME with data from `pbsnodes -aj -F json`
```yaml
standard:
  cores_per_node: 36
  memory_gb_per_node: 192
  gpu_model: null
  gpus_per_node: null

bigmem:
  cores_per_node: 36
  memory_gb_per_node: 768
  gpu_model: null
  gpus_per_node: null

gpu-v100-4way:
  cores_per_node: 36
  memory_gb_per_node: 384
  gpu_model: "NVIDIA V100"
  gpus_per_node: 4

gpu-v100-8way:
  cores_per_node: 36
  memory_gb_per_node: 384
  gpu_model: "NVIDIA V100"
  gpus_per_node: 8

gpu-a100:
  cores_per_node: 64
  memory_gb_per_node: 512
  gpu_model: "NVIDIA A100"
  gpus_per_node: 4

gpu-h100:
  cores_per_node: 64
  memory_gb_per_node: 512
  gpu_model: "NVIDIA A100"
  gpus_per_node: 4

gpu-mi300a:
  cores_per_node: 64
  memory_gb_per_node: 512
  gpu_model: "AMD MI300a"
  gpus_per_node: 4
```

---

### 2.9 Logging Utilities (`lib/logging_utils.py`)

**Purpose**: Structured logging configuration.

```python
def setup_logging(log_file: str = None, verbose: bool = False):
    """
    Configure logging for collectors.

    Args:
        log_file: Path to log file (None = stdout only)
        verbose: Enable DEBUG level logging
    """
    level = logging.DEBUG if verbose else logging.INFO

    format_str = '[%(asctime)s] %(levelname)s [%(name)s] %(message)s'
    formatter = logging.Formatter(format_str, datefmt='%Y-%m-%d %H:%M:%S')

    handlers = []

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    handlers.append(console)

    # File handler
    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers)

    # Suppress noisy libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
```

---

## 3. System-Specific Collectors

### 3.1 Derecho Collector (`derecho/collector.py`)

**Main executable** for Derecho data collection.

```python
#!/usr/bin/env python3
"""
Derecho HPC Status Collector

Collects system metrics from Derecho and posts to SAM Status Dashboard.
Runs every 5 minutes via cron.
"""

import sys
import argparse
from datetime import datetime
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'lib'))

from pbs_client import PBSClient
from api_client import SAMAPIClient
from config import CollectorConfig
from logging_utils import setup_logging
from parsers.nodes import NodeParser
from parsers.jobs import JobParser
from parsers.queues import QueueParser
from parsers.filesystems import FilesystemParser
from ssh_utils import LoginNodeCollector


class DerechoCollector:
    """Main Derecho data collector."""

    def __init__(self, config: CollectorConfig, dry_run: bool = False,
                 json_only: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.json_only = json_only
        self.logger = logging.getLogger(__name__)

        # Initialize clients
        self.pbs = PBSClient(config.pbs_host)
        self.api = SAMAPIClient(
            config.api_url,
            config.api_user,
            config.api_password
        )
        self.login_collector = LoginNodeCollector(config.pbs_host)

    def collect(self) -> dict:
        """
        Collect all Derecho metrics.

        Returns:
            Complete data dict ready for API posting
        """
        data = {
            'timestamp': datetime.now().isoformat()
        }

        # Collect node data
        try:
            self.logger.info("Collecting node data...")
            nodes_json = self.pbs.get_nodes_json()
            node_stats = NodeParser.parse_nodes(nodes_json, 'derecho')
            data.update(node_stats)
            self.logger.info(f"  CPU nodes: {node_stats['cpu_nodes_total']} total, "
                           f"{node_stats['cpu_nodes_available']} available")
            self.logger.info(f"  GPU nodes: {node_stats['gpu_nodes_total']} total, "
                           f"{node_stats['gpu_nodes_available']} available")
        except Exception as e:
            self.logger.error(f"Failed to collect node data: {e}")
            # Set defaults to allow partial collection
            data.update({
                'cpu_nodes_total': 0,
                'cpu_nodes_available': 0,
                'cpu_nodes_down': 0,
                'cpu_nodes_reserved': 0,
                'gpu_nodes_total': 0,
                'gpu_nodes_available': 0,
                'gpu_nodes_down': 0,
                'gpu_nodes_reserved': 0,
                'cpu_cores_total': 0,
                'cpu_cores_allocated': 0,
                'cpu_cores_idle': 0,
                'gpu_count_total': 0,
                'gpu_count_allocated': 0,
                'gpu_count_idle': 0,
                'memory_total_gb': 0.0,
                'memory_allocated_gb': 0.0,
            })

        # Collect job data
        try:
            self.logger.info("Collecting job data...")
            jobs_json = self.pbs.get_jobs_json()
            job_stats = JobParser.parse_jobs(jobs_json)
            data.update(job_stats)
            self.logger.info(f"  Jobs: {job_stats['running_jobs']} running, "
                           f"{job_stats['pending_jobs']} pending")

            # Parse queues
            queue_summary = self.pbs.get_queue_summary()
            data['queues'] = QueueParser.parse_queues(queue_summary, jobs_json)
            self.logger.info(f"  Queues: {len(data['queues'])} tracked")
        except Exception as e:
            self.logger.error(f"Failed to collect job data: {e}")
            data.update({
                'running_jobs': 0,
                'pending_jobs': 0,
                'active_users': 0,
                'queues': []
            })

        # Collect login node data
        try:
            self.logger.info("Collecting login node data...")
            login_nodes = self.login_collector.collect_login_node_data(
                self.config.login_nodes
            )
            data['login_nodes'] = login_nodes
            available = sum(1 for n in login_nodes if n['available'])
            self.logger.info(f"  Login nodes: {available}/{len(login_nodes)} available")
        except Exception as e:
            self.logger.error(f"Failed to collect login node data: {e}")
            data['login_nodes'] = []

        # Collect filesystem data
        try:
            self.logger.info("Collecting filesystem data...")
            df_cmd = f"BLOCKSIZE=TiB df /glade/{{u/home,work,campaign,derecho/scratch}}"
            df_output = self.pbs.run_command(df_cmd)
            data['filesystems'] = FilesystemParser.parse_filesystems(
                df_output, self.config.filesystems
            )
            self.logger.info(f"  Filesystems: {len(data['filesystems'])} tracked")
        except Exception as e:
            self.logger.error(f"Failed to collect filesystem data: {e}")
            data['filesystems'] = []

        return data

    def run(self) -> int:
        """
        Execute collection and posting.

        Returns:
            Exit code (0 = success, 1 = failure)
        """
        try:
            data = self.collect()

            if self.json_only:
                print(json.dumps(data, indent=2))
                return 0

            result = self.api.post_status('derecho', data, dry_run=self.dry_run)

            if not self.dry_run:
                self.logger.info(f"✓ Success: status_id={result.get('status_id')}")

            return 0

        except Exception as e:
            self.logger.error(f"✗ Collection failed: {e}", exc_info=True)
            return 1


def main():
    parser = argparse.ArgumentParser(description='Derecho HPC Status Collector')
    parser.add_argument('--dry-run', action='store_true',
                       help='Collect data but do not post to API')
    parser.add_argument('--json-only', action='store_true',
                       help='Output JSON to stdout and exit (no API call)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    parser.add_argument('--log-file', default='/var/log/derecho_collector.log',
                       help='Log file path (default: /var/log/derecho_collector.log)')

    args = parser.parse_args()

    # Setup logging
    setup_logging(
        log_file=args.log_file if not args.json_only else None,
        verbose=args.verbose
    )

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Derecho Status Collector - Starting")
    logger.info("=" * 60)

    try:
        config = CollectorConfig('derecho')
        collector = DerechoCollector(
            config,
            dry_run=args.dry_run,
            json_only=args.json_only
        )
        exit_code = collector.run()

        logger.info("=" * 60)
        logger.info(f"Derecho Status Collector - {'SUCCESS' if exit_code == 0 else 'FAILED'}")
        logger.info("=" * 60)

        return exit_code

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
```

---

### 3.2 Casper Collector (`casper/collector.py`)

**Main executable** for Casper data collection. Very similar to Derecho but with node type handling.

```python
#!/usr/bin/env python3
"""
Casper DAV Status Collector

Collects system metrics from Casper and posts to SAM Status Dashboard.
Runs every 5 minutes via cron.
"""

# ... (imports same as Derecho)

class CasperCollector:
    """Main Casper data collector."""

    def __init__(self, config: CollectorConfig, dry_run: bool = False,
                 json_only: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.json_only = json_only
        self.logger = logging.getLogger(__name__)

        # Initialize clients
        self.pbs = PBSClient(config.pbs_host)
        self.api = SAMAPIClient(
            config.api_url,
            config.api_user,
            config.api_password
        )
        self.login_collector = LoginNodeCollector(config.pbs_host)

        # Load node type config
        self.node_type_config = config.get_node_type_config()

    def collect(self) -> dict:
        """
        Collect all Casper metrics.

        Returns:
            Complete data dict ready for API posting
        """
        data = {
            'timestamp': datetime.now().isoformat()
        }

        # Collect node data (aggregate)
        try:
            self.logger.info("Collecting node data...")
            nodes_json = self.pbs.get_nodes_json()

            # Aggregate stats
            node_stats = NodeParser.parse_nodes(nodes_json, 'casper')
            data.update({
                'compute_nodes_total': node_stats['cpu_nodes_total'],
                'compute_nodes_available': node_stats['cpu_nodes_available'],
                'compute_nodes_down': node_stats['cpu_nodes_down'],
                'cpu_utilization_percent': node_stats.get('cpu_utilization_percent'),
                'memory_utilization_percent': node_stats.get('memory_utilization_percent'),
            })

            # Node type breakdown
            data['node_types'] = NodeParser.parse_casper_node_types(
                nodes_json, self.node_type_config
            )

            self.logger.info(f"  Nodes: {data['compute_nodes_total']} total, "
                           f"{data['compute_nodes_available']} available")
            self.logger.info(f"  Node types: {len(data['node_types'])} types tracked")

        except Exception as e:
            self.logger.error(f"Failed to collect node data: {e}")
            data.update({
                'compute_nodes_total': 0,
                'compute_nodes_available': 0,
                'compute_nodes_down': 0,
                'node_types': []
            })

        # Collect job data (same as Derecho)
        try:
            self.logger.info("Collecting job data...")
            jobs_json = self.pbs.get_jobs_json()
            job_stats = JobParser.parse_jobs(jobs_json)
            data.update(job_stats)

            queue_summary = self.pbs.get_queue_summary()
            data['queues'] = QueueParser.parse_queues(queue_summary, jobs_json)

            self.logger.info(f"  Jobs: {job_stats['running_jobs']} running, "
                           f"{job_stats['pending_jobs']} pending")
        except Exception as e:
            self.logger.error(f"Failed to collect job data: {e}")
            data.update({
                'running_jobs': 0,
                'pending_jobs': 0,
                'active_users': 0,
                'queues': []
            })

        # Collect login node data (NO node_type field for Casper)
        try:
            self.logger.info("Collecting login node data...")
            login_nodes = self.login_collector.collect_login_node_data(
                self.config.login_nodes
            )
            data['login_nodes'] = login_nodes
            available = sum(1 for n in login_nodes if n['available'])
            self.logger.info(f"  Login nodes: {available}/{len(login_nodes)} available")
        except Exception as e:
            self.logger.error(f"Failed to collect login node data: {e}")
            data['login_nodes'] = []

        # Collect filesystem data (same as Derecho)
        try:
            self.logger.info("Collecting filesystem data...")
            df_cmd = f"BLOCKSIZE=TiB df /glade/{{u/home,work,campaign,derecho/scratch}}"
            df_output = self.pbs.run_command(df_cmd)
            data['filesystems'] = FilesystemParser.parse_filesystems(
                df_output, self.config.filesystems
            )
            self.logger.info(f"  Filesystems: {len(data['filesystems'])} tracked")
        except Exception as e:
            self.logger.error(f"Failed to collect filesystem data: {e}")
            data['filesystems'] = []

        return data

    def run(self) -> int:
        """Execute collection and posting."""
        try:
            data = self.collect()

            if self.json_only:
                print(json.dumps(data, indent=2))
                return 0

            result = self.api.post_status('casper', data, dry_run=self.dry_run)

            if not self.dry_run:
                self.logger.info(f"✓ Success: status_id={result.get('status_id')}")

            return 0

        except Exception as e:
            self.logger.error(f"✗ Collection failed: {e}", exc_info=True)
            return 1


def main():
    # ... (same argument parsing as Derecho, but 'casper' system name)
    pass


if __name__ == '__main__':
    sys.exit(main())
```

---

## 4. Data Collection Flow

### ASCII Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Cron Trigger (*/5 * * * *)               │
│                  /usr/local/bin/derecho_collector.py         │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
                    ┌────────────────┐
                    │  Initialize    │
                    │  - Load .env   │
                    │  - Load YAML   │
                    │  - Setup log   │
                    └────────┬───────┘
                             │
                ┌────────────┴────────────┐
                │                         │
                ▼                         ▼
       ┌────────────────┐       ┌────────────────┐
       │  PBS Commands  │       │  SSH Commands  │
       └────────┬───────┘       └───────┬────────┘
                │                       │
    ┌───────────┼───────────┐           │
    │           │           │           │
    ▼           ▼           ▼           ▼
┌────────┐  ┌──────┐  ┌──────────┐  ┌──────────┐
│pbsnodes│  │qstat │  │qstat -Qa │  │ssh login │
│-aj -F  │  │-f -F │  │          │  │uptime    │
│json    │  │json  │  │          │  │who df    │
└───┬────┘  └───┬──┘  └─────┬────┘  └────┬─────┘
    │           │           │            │
    ▼           ▼           ▼            ▼
┌─────────────────────────────────────────────┐
│           Data Parsers                      │
│  - NodeParser.parse_nodes()                 │
│  - JobParser.parse_jobs()                   │
│  - QueueParser.parse_queues()               │
│  - LoginNodeCollector.collect()             │
│  - FilesystemParser.parse_filesystems()     │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
        ┌──────────────────┐
        │  Aggregate Data  │
        │  Build JSON dict │
        └──────────┬───────┘
                   │
                   ▼
           ┌───────────────┐         ┌──────────┐
           │  Dry Run?     │────Yes──│ Log JSON │
           └───────┬───────┘         │ Exit 0   │
                   │                 └──────────┘
                   No
                   │
                   ▼
        ┌──────────────────────┐
        │  SAMAPIClient.post() │
        │  - Retry logic       │
        │  - Exponential back  │
        └──────────┬───────────┘
                   │
         ┌─────────┴──────────┐
         │                    │
         ▼                    ▼
    ┌────────┐          ┌─────────┐
    │Success │          │ Failure │
    │Log ✓   │          │ Log ✗   │
    │Exit 0  │          │ Exit 1  │
    └────────┘          └─────────┘
```

### Detailed Step-by-Step Flow

1. **Initialization (5 seconds)**
   - Load environment variables from `.env`
   - Load YAML configuration (`config.yaml`)
   - Setup logging (file + stdout)
   - Initialize PBS/API/SSH clients

2. **Node Data Collection (10-15 seconds)**
   - Execute: `ssh derecho "pbsnodes -aj -F json"`
   - Parse JSON response
   - Count nodes by state (free/job-busy/down/reserved)
   - Separate CPU vs GPU nodes (Derecho) or by node type (Casper)
   - Aggregate cores, GPUs, memory
   - Calculate utilization percentages
   - **On error**: Log warning, set defaults, continue

3. **Job Data Collection (5-10 seconds)**
   - Execute: `ssh derecho "qstat -f -F json"`
   - Parse job states (R=running, Q=queued)
   - Count active users (unique usernames)
   - Execute: `ssh derecho "qstat -Qa"`
   - Parse queue summary text
   - Combine with job data for per-queue stats
   - **On error**: Log warning, set defaults, continue

4. **Login Node Data Collection (20-30 seconds)**
   - For each login node (parallel or serial):
     - SSH: `ssh derecho "ssh derechoN 'cat /proc/loadavg; echo ---; who | wc -l'"`
     - Parse load averages (1min, 5min, 15min)
     - Parse user count
   - **On error**: Mark node as unavailable/degraded, continue to next

5. **Filesystem Data Collection (3-5 seconds)**
   - Execute: `ssh derecho "BLOCKSIZE=TiB df /glade/{paths}"`
   - Parse df output for capacity/used/utilization
   - Match mountpoints to configured filesystem names
   - **On error**: Log warning, return empty list

6. **Data Aggregation (< 1 second)**
   - Build complete JSON dict
   - Add timestamp (ISO format)
   - Validate required fields present

7. **API Posting (2-5 seconds)**
   - **If dry-run**: Log JSON, exit 0
   - **If json-only**: Print to stdout, exit 0
   - POST to `/api/v1/status/derecho`
   - Retry up to 3 times with exponential backoff (1s, 2s, 4s)
   - **On auth error (401/403)**: Fail immediately, exit 1
   - **On validation error (400)**: Fail immediately, exit 1
   - **On server error (500)**: Retry, then fail
   - **On network error**: Retry, then fail

8. **Logging & Exit**
   - Log success: `✓ Posted derecho status: status_id=12345`
   - Or failure: `✗ Collection failed: <error>`
   - Exit with appropriate code (0=success, 1=error, 2=fatal)

---

## 5. Error Handling Strategy

### Principles

1. **Partial Collection Succeeds**: If PBS nodes fail but jobs succeed, post what we have
2. **Fail-Fast on Auth**: Don't retry 401/403 errors (credentials wrong)
3. **Retry on Network**: Exponential backoff for transient failures
4. **Detailed Logging**: Every failure logged with context
5. **Defaults on Error**: Missing data gets sensible defaults (0 counts, empty lists)

### Error Categories

| Error Type | Action | Exit Code | Example |
|------------|--------|-----------|---------|
| Auth failure (401/403) | Log error, exit immediately | 1 | Wrong credentials |
| Validation error (400) | Log error with details, exit | 1 | Missing required field |
| PBS command timeout | Log warning, use defaults, continue | 0 (partial) | SSH timeout |
| SSH connection failure | Log warning, mark node degraded, continue | 0 (partial) | Login node down |
| JSON parse error | Log warning, use defaults, continue | 0 (partial) | Malformed PBS output |
| Network timeout | Retry 3x, then exit | 1 | API unreachable |
| Server error (500) | Retry 3x, then exit | 1 | Database error |
| Config file missing | Log fatal error, exit | 2 | .env not found |
| Unknown exception | Log with traceback, exit | 2 | Bug in code |

### Example Error Scenarios

**Scenario 1: One login node is down**
```
[INFO] Collecting login node data...
[WARNING] Failed to collect from derecho3: Connection timeout
[INFO] Login nodes: 7/8 available
```
Result: Continue collection, mark derecho3 as unavailable in data

**Scenario 2: PBS JSON is malformed**
```
[ERROR] Failed to collect node data: JSON parse error at line 45
[WARNING] Using default node counts (0)
[INFO] Collecting job data...
```
Result: Continue with other data, post partial results

**Scenario 3: API is down**
```
[WARNING] HTTP 503, retry 1/3 in 1s
[WARNING] HTTP 503, retry 2/3 in 2s
[WARNING] HTTP 503, retry 3/3 in 4s
[ERROR] ✗ Collection failed: HTTP error after 3 attempts
```
Result: Exit 1, cron will retry in 5 minutes

---

## 6. Testing & Debugging

### 6.1 Dry Run Mode

**Purpose**: Collect data but don't post to API.

```bash
# Derecho
./derecho/collector.py --dry-run --verbose

# Output:
# [INFO] Derecho Status Collector - Starting
# [INFO] Collecting node data...
# [INFO]   CPU nodes: 2488 total, 1850 available
# [DEBUG] pbsnodes JSON: 15234 lines
# [INFO] Collecting job data...
# [INFO]   Jobs: 1245 running, 328 pending
# [INFO] Collecting login node data...
# [INFO]   Login nodes: 8/8 available
# [DRY RUN] Would POST to http://localhost:5050/api/v1/status/derecho
# [DRY RUN] Data: {... full JSON ...}
# [INFO] Derecho Status Collector - SUCCESS
```

### 6.2 JSON-Only Mode

**Purpose**: Output collected data as JSON without API call.

```bash
./derecho/collector.py --json-only > /tmp/derecho_data.json

# Validate JSON
jq . /tmp/derecho_data.json

# Check specific fields
jq '.cpu_nodes_total, .running_jobs, .login_nodes | length' /tmp/derecho_data.json
```

### 6.3 Verbose Logging

**Purpose**: Debug-level logging for troubleshooting.

```bash
./derecho/collector.py --verbose --log-file /tmp/debug.log
tail -f /tmp/debug.log
```

### 6.4 Unit Tests

**Test PBS parsing** with mock data:

```python
# tests/test_pbs_parser.py
import json
from lib.parsers.nodes import NodeParser

def test_parse_derecho_nodes():
    """Test parsing Derecho pbsnodes JSON."""
    with open('tests/mock_data/pbsnodes_derecho.json') as f:
        mock_data = json.load(f)

    result = NodeParser.parse_nodes(mock_data, 'derecho')

    assert result['cpu_nodes_total'] == 2488
    assert result['gpu_nodes_total'] == 82
    assert result['cpu_utilization_percent'] > 0
    assert result['cpu_cores_total'] == 321536

def test_parse_casper_node_types():
    """Test Casper node type classification."""
    # ... similar test with mock data
```

**Test API client** with mock responses:

```python
# tests/test_api_client.py
from unittest.mock import Mock, patch
from lib.api_client import SAMAPIClient

@patch('requests.Session.post')
def test_api_retry_logic(mock_post):
    """Test exponential backoff on failures."""
    # First two calls fail, third succeeds
    mock_post.side_effect = [
        Mock(status_code=503, raise_for_status=lambda: raise_error()),
        Mock(status_code=503, raise_for_status=lambda: raise_error()),
        Mock(status_code=201, json=lambda: {'success': True})
    ]

    client = SAMAPIClient('http://test', 'user', 'pass')
    result = client.post_status('derecho', {}, max_retries=3)

    assert result['success'] == True
    assert mock_post.call_count == 3
```

### 6.5 Integration Tests

**Test end-to-end** with test API:

```bash
# Set test credentials
export STATUS_API_URL=http://localhost:5050
export STATUS_API_USER=test_collector
export STATUS_API_PASSWORD=test_password

# Run collector
./derecho/collector.py --verbose

# Verify data in database
mysql -u root -h 127.0.0.1 -proot system_status -e \
  "SELECT status_id, timestamp, cpu_nodes_total, running_jobs
   FROM derecho_status ORDER BY timestamp DESC LIMIT 1"
```

---

## 7. Deployment

### 7.1 Installation

**Makefile** (`collectors/Makefile`):

```makefile
.PHONY: install test deploy clean

# Installation
install:
	@echo "Installing PBS Collectors..."
	pip install -r requirements.txt
	chmod +x derecho/collector.py
	chmod +x casper/collector.py
	@echo "✓ Installation complete"

# Configuration
config:
	@echo "Configuring collectors..."
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "⚠ Created .env - EDIT WITH YOUR CREDENTIALS"; \
		exit 1; \
	fi
	chmod 600 .env
	@echo "✓ Configuration complete"

# Testing
test:
	pytest tests/ -v

test-dry-run:
	@echo "Testing Derecho collector (dry run)..."
	./derecho/collector.py --dry-run --verbose
	@echo ""
	@echo "Testing Casper collector (dry run)..."
	./casper/collector.py --dry-run --verbose

# Deployment
deploy-cron:
	@echo "Installing cron jobs..."
	@cat deploy/crontab.template | sed "s|{INSTALL_DIR}|$(PWD)|g" | crontab -
	@echo "✓ Cron jobs installed"
	@crontab -l

deploy-logs:
	@echo "Setting up log rotation..."
	@sudo cp deploy/logrotate.conf /etc/logrotate.d/hpc-collectors
	@echo "✓ Log rotation configured"

deploy: config deploy-cron deploy-logs
	@echo "✓ Deployment complete!"

# Cleanup
clean:
	rm -rf **/__pycache__
	rm -rf **/*.pyc
	rm -rf .pytest_cache
```

### 7.2 Crontab Configuration

**Template** (`deploy/crontab.template`):

```cron
# HPC Status Collectors - Run every 5 minutes
# Managed by: collectors/Makefile

# Environment
PATH=/usr/local/bin:/usr/bin:/bin
SHELL=/bin/bash

# Derecho collector (every 5 minutes)
*/5 * * * * {INSTALL_DIR}/derecho/collector.py >> /var/log/derecho_collector.log 2>&1

# Casper collector (every 5 minutes, offset by 2 minutes to avoid overlap)
2-59/5 * * * * {INSTALL_DIR}/casper/collector.py >> /var/log/casper_collector.log 2>&1

# Health check (every hour)
0 * * * * {INSTALL_DIR}/deploy/health_check.sh >> /var/log/collector_health.log 2>&1
```

### 7.3 Log Rotation

**Configuration** (`deploy/logrotate.conf`):

```
/var/log/derecho_collector.log
/var/log/casper_collector.log
/var/log/collector_health.log
{
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root adm
    sharedscripts
    postrotate
        # No action needed - collectors append
    endscript
}
```

### 7.4 Health Check Script

**Monitor collector status** (`deploy/health_check.sh`):

```bash
#!/bin/bash
# Health check for HPC collectors

LOG_DIR="/var/log"
ALERT_EMAIL="hpc-ops@ucar.edu"
MAX_AGE=900  # 15 minutes (3 collection cycles)

check_collector() {
    local name=$1
    local logfile="${LOG_DIR}/${name}_collector.log"

    if [ ! -f "$logfile" ]; then
        echo "✗ ${name}: Log file not found"
        return 1
    fi

    # Check last success
    last_success=$(grep "✓" "$logfile" | tail -1 | awk '{print $1, $2}')
    if [ -z "$last_success" ]; then
        echo "✗ ${name}: No successful collections found"
        return 1
    fi

    # Check age
    last_epoch=$(date -d "$last_success" +%s 2>/dev/null)
    now_epoch=$(date +%s)
    age=$((now_epoch - last_epoch))

    if [ $age -gt $MAX_AGE ]; then
        echo "✗ ${name}: Stale data (${age}s old, max ${MAX_AGE}s)"
        return 1
    fi

    echo "✓ ${name}: Healthy (last success: ${age}s ago)"
    return 0
}

echo "HPC Collector Health Check - $(date)"
echo "=========================================="

status=0

check_collector "derecho" || status=1
check_collector "casper" || status=1

echo "=========================================="

if [ $status -ne 0 ]; then
    echo "⚠ ALERT: Collectors unhealthy"
    # Send alert email
    echo "HPC collectors health check failed. See /var/log/collector_health.log" | \
        mail -s "HPC Collectors Alert" "$ALERT_EMAIL"
fi

exit $status
```

### 7.5 Installation Steps

```bash
# 1. Clone/copy collectors to HPC system
scp -r collectors/ derecho:/opt/hpc-collectors/

# 2. SSH to HPC system
ssh derecho

# 3. Navigate to collectors directory
cd /opt/hpc-collectors

# 4. Install dependencies
make install

# 5. Configure credentials
cp .env.example .env
vim .env  # Edit STATUS_API_USER, STATUS_API_PASSWORD, STATUS_API_URL
make config

# 6. Test collectors
make test-dry-run

# 7. Deploy to cron
make deploy

# 8. Monitor logs
tail -f /var/log/derecho_collector.log
```

---

## 8. Configuration Files

### 8.1 Environment Variables (`.env`)

```bash
# SAM API Configuration
STATUS_API_URL=https://sam.ucar.edu
STATUS_API_USER=derecho_collector
STATUS_API_PASSWORD=YOUR_SECURE_PASSWORD_HERE

# Optional: Override log locations
DERECHO_LOG_FILE=/var/log/derecho_collector.log
CASPER_LOG_FILE=/var/log/casper_collector.log

# Optional: Collection timeout (seconds)
PBS_COMMAND_TIMEOUT=30
SSH_TIMEOUT=10
API_TIMEOUT=30
```

### 8.2 Derecho Configuration (`derecho/config.yaml`)

```yaml
system_name: derecho
pbs_host: derecho

login_nodes:
  - name: derecho1
    type: cpu
  - name: derecho2
    type: cpu
  - name: derecho3
    type: gpu
  - name: derecho4
    type: cpu
  - name: derecho5
    type: cpu
  - name: derecho6
    type: cpu
  - name: derecho7
    type: gpu
  - name: derecho8
    type: cpu

filesystems:
  - glade
  - campaign
  - derecho_scratch

queues:
  - main
  - preempt
  - develop
  - gpudev
  - gpumain
```

### 8.3 Casper Configuration (`casper/config.yaml`)

```yaml
system_name: casper
pbs_host: casper

login_nodes:
  - name: casper-login1
  - name: casper-login2

filesystems:
  - glade
  - campaign

queues:
  - casper
  - gpudev
  - htc
```

### 8.4 Casper Node Types (`casper/node_types.yaml`)

```yaml
standard:
  cores_per_node: 36
  memory_gb_per_node: 192
  gpu_model: null
  gpus_per_node: null

bigmem:
  cores_per_node: 36
  memory_gb_per_node: 768
  gpu_model: null
  gpus_per_node: null

gpu-v100:
  cores_per_node: 36
  memory_gb_per_node: 384
  gpu_model: "NVIDIA V100"
  gpus_per_node: 4

gpu-a100:
  cores_per_node: 64
  memory_gb_per_node: 512
  gpu_model: "NVIDIA A100"
  gpus_per_node: 4

gpu-mi100:
  cores_per_node: 64
  memory_gb_per_node: 512
  gpu_model: "AMD MI100"
  gpus_per_node: 4
```

---

## 9. Monitoring & Maintenance

### 9.1 Monitoring Checklist

- [ ] Cron jobs running (check: `crontab -l`)
- [ ] Log files updating every 5 minutes
- [ ] No repeated errors in logs
- [ ] API posting successful (check status_ids)
- [ ] Dashboard shows recent timestamps (<10 minutes old)
- [ ] Health check passing

### 9.2 Common Issues & Solutions

| Issue | Symptom | Solution |
|-------|---------|----------|
| Stale dashboard data | Last update >15 min ago | Check cron jobs, review logs |
| "401 Unauthorized" | Every collection fails | Verify credentials in .env |
| "Connection timeout" | Intermittent failures | Check network, increase timeouts |
| "JSON parse error" | Partial data posted | Update parser for new PBS format |
| High error rate | Many ✗ in logs | Check PBS commands manually |
| Disk space full | Log rotation failing | Clean old logs, check logrotate |

### 9.3 Manual Testing

```bash
# Test PBS commands directly
ssh derecho "pbsnodes -aj -F json" | jq . | head -50

# Test collector without posting
./derecho/collector.py --json-only | jq .

# Test API endpoint with curl
curl -X POST https://sam.ucar.edu/api/v1/status/derecho \
  -u collector:password \
  -H "Content-Type: application/json" \
  -d @/tmp/derecho_data.json

# Check database
mysql -u root -h 127.0.0.1 -proot system_status \
  -e "SELECT * FROM derecho_status ORDER BY timestamp DESC LIMIT 1\G"
```

### 9.4 Log Analysis

```bash
# Count successes vs failures (last 24 hours)
grep "✓" /var/log/derecho_collector.log | tail -288 | wc -l  # Should be ~288
grep "✗" /var/log/derecho_collector.log | tail -288 | wc -l  # Should be 0

# Find error patterns
grep "ERROR" /var/log/derecho_collector.log | awk '{print $NF}' | sort | uniq -c

# Check collection times
grep "Starting" /var/log/derecho_collector.log | tail -10

# API response times
grep "status_id" /var/log/derecho_collector.log | tail -10
```

---

## 10. Implementation Checklist

### Phase 1: Foundation (Week 1)
- [ ] Create directory structure
- [ ] Implement `lib/pbs_client.py`
- [ ] Implement `lib/api_client.py`
- [ ] Implement `lib/config.py`
- [ ] Implement `lib/logging_utils.py`
- [ ] Create `.env.example` and `requirements.txt`

### Phase 2: Parsers (Week 1-2)
- [ ] Implement `lib/parsers/nodes.py`
- [ ] Implement `lib/parsers/jobs.py`
- [ ] Implement `lib/parsers/queues.py`
- [ ] Implement `lib/ssh_utils.py` (login nodes)
- [ ] Implement `lib/parsers/filesystems.py`
- [ ] Create test fixtures (mock PBS JSON)
- [ ] Write unit tests for parsers

### Phase 3: Derecho Collector (Week 2)
- [ ] Implement `derecho/collector.py`
- [ ] Create `derecho/config.yaml`
- [ ] Test with `--dry-run`
- [ ] Test with `--json-only`
- [ ] Test against dev API
- [ ] Write integration tests

### Phase 4: Casper Collector (Week 2-3)
- [ ] Implement `casper/collector.py`
- [ ] Create `casper/config.yaml`
- [ ] Create `casper/node_types.yaml`
- [ ] Test node type classification logic
- [ ] Test with `--dry-run`
- [ ] Test against dev API

### Phase 5: Deployment (Week 3)
- [ ] Create `Makefile`
- [ ] Create `deploy/install.sh`
- [ ] Create `deploy/crontab.template`
- [ ] Create `deploy/logrotate.conf`
- [ ] Create `deploy/health_check.sh`
- [ ] Test installation on dev HPC system
- [ ] Document deployment procedure

### Phase 6: Production Rollout (Week 4)
- [ ] Deploy to Derecho production
- [ ] Monitor for 48 hours
- [ ] Deploy to Casper production
- [ ] Monitor for 48 hours
- [ ] Setup alerting/monitoring
- [ ] Document operational procedures
- [ ] Training for ops team

---

## 11. Critical Files for Implementation

The following files are essential for implementing this architecture:

1. **`/Users/benkirk/codes/sam-queries/python/webapp/api/v1/status.py`**
   - **Reason**: Defines exact API data format and requirements
   - **Key info**: POST endpoint schemas, required/optional fields, error handling
   - **Lines of interest**: 97-238 (Derecho POST), 240-382 (Casper POST)

2. **`/Users/benkirk/codes/sam-queries/python/system_status/models/derecho.py`**
   - **Reason**: Database schema for Derecho status
   - **Key info**: Field names, data types, nullable constraints
   - **Must match**: All fields in POST endpoint

3. **`/Users/benkirk/codes/sam-queries/python/system_status/models/casper.py`**
   - **Reason**: Database schema for Casper status
   - **Key info**: Node type structure, aggregate vs detailed metrics

4. **`/Users/benkirk/codes/sam-queries/python/system_status/models/login_nodes.py`**
   - **Reason**: Login node schema differences (Derecho has node_type, Casper doesn't)
   - **Critical**: Don't send node_type for Casper login nodes

5. **`/Users/benkirk/codes/sam-queries/docs/HPC_DATA_COLLECTORS_GUIDE.md`**
   - **Reason**: API usage examples and authentication requirements
   - **Key info**: Authentication, error codes, data formats

---

## Appendix A: Example Commands

### Collect PBS Data

```bash
# Node status (JSON)
ssh derecho "pbsnodes -aj -F json" > pbsnodes.json

# Job details (JSON)
ssh derecho "qstat -f -F json" > qstat.json

# Queue summary (text)
ssh derecho "qstat -Qa" > queues.txt

# Reservations
ssh derecho "pbs_rstat -f" > reservations.txt

# Filesystem usage
ssh derecho "BLOCKSIZE=TiB df /glade/u/home /glade/work /glade/campaign /glade/derecho/scratch"

# Login node metrics (example for derecho1)
ssh derecho "ssh derecho1 'cat /proc/loadavg; echo ---; who | wc -l'"
```

### Test Parsers

```python
# Interactive testing
python3
>>> from lib.parsers.nodes import NodeParser
>>> import json
>>> with open('pbsnodes.json') as f:
...     data = json.load(f)
>>> result = NodeParser.parse_nodes(data, 'derecho')
>>> print(result)
```

---

## Appendix B: Dependencies

**`requirements.txt`**:
```
requests>=2.31.0
python-dotenv>=1.0.0
PyYAML>=6.0
```

**System requirements**:
- Python 3.8+
- SSH access to HPC systems
- Network access to SAM API
- Write access to `/var/log/` (or alternate log dir)

---

## Summary

This architecture provides a **production-ready, maintainable PBS data collector system** with:

- **90% code reuse** between Derecho and Casper via shared libraries
- **Robust error handling** with partial collection and retry logic
- **Observable operation** via structured logging, dry-run, and JSON-only modes
- **Secure credential management** via environment variables
- **Automated deployment** via Makefile and cron
- **Comprehensive testing** with unit and integration tests
- **Operational monitoring** via health checks and log analysis

The modular design allows easy extension to additional HPC systems (e.g., Gust, Hobart) by creating new system-specific collectors that reuse the shared library components.
