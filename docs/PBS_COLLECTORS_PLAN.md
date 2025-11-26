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
├── requirements.txt             # Python dependencies
├── .env.example                 # Credential template
├── Makefile                     # Installation and deployment helpers
│
├── lib/                         # Shared library code
│   ├── __init__.py
│   ├── pbs_client.py            # PBS command execution and JSON parsing
│   ├── api_client.py            # SAM API HTTP client with retry logic
│   ├── config.py                # Configuration management (.env loading)
│   ├── logging_utils.py         # Structured logging setup
│   ├── ssh_utils.py             # Login node SSH operations
│   └── parsers/                 # Data parsing modules
│       ├── __init__.py
│       ├── nodes.py             # pbsnodes JSON parser
│       ├── jobs.py              # qstat JSON parser
│       ├── queues.py            # qstat -Qa parser
│       ├── filesystems.py       # df output parser
│       └── reservations.py      # pbs_rstat parser
│
├── derecho/                     # Derecho-specific collector
│   ├── collector.py             # Main Derecho collector script
│   ├── config.yaml              # Derecho system configuration
│   └── login_nodes.txt          # List: derecho1-8
│
├── casper/                      # Casper-specific collector
│   ├── collector.py             # Main Casper collector script
│   ├── config.yaml              # Casper system configuration
│   ├── node_types.yaml          # Node type definitions
│   └── login_nodes.txt          # List: casper-login1-2
│
├── tests/                       # Unit and integration tests
│   ├── __init__.py
│   ├── test_pbs_parser.py
│   ├── test_api_client.py
│   ├── mock_data/               # Sample PBS JSON outputs
│   │   ├── pbsnodes_derecho.json
│   │   ├── pbsnodes_casper.json
│   │   ├── qstat_jobs.json
│   │   └── qstat_queues.txt
│   └── integration/
│       ├── test_derecho_collector.py
│       └── test_casper_collector.py
│
└── deploy/                      # Deployment scripts
    ├── install.sh               # Installation script
    ├── crontab.template         # Cron configuration
    ├── logrotate.conf           # Log rotation config
    └── systemd/                 # Optional systemd timers
        ├── derecho-collector.service
        └── derecho-collector.timer
