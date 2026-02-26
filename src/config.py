"""Base configuration for SAM CLI tools and webapp.

All env-var reading for the two databases and mail is centralised here.
sam.session / system_status.session continue to work unchanged; they read
the same vars at import time for connection-string building.
"""
import os
from dotenv import load_dotenv, find_dotenv

# Load .env on import so the class attrs below pick up values.
# (sam.session also does this; calling it twice is harmless.)
load_dotenv(find_dotenv())


class SAMConfig:
    # ------------------------------------------------------------------ SAM DB
    SAM_DB_USERNAME    = os.getenv('SAM_DB_USERNAME', '')
    SAM_DB_PASSWORD    = os.getenv('SAM_DB_PASSWORD', '')
    SAM_DB_SERVER      = os.getenv('SAM_DB_SERVER', '')
    SAM_DB_NAME        = os.getenv('SAM_DB_NAME', 'sam')
    SAM_DB_REQUIRE_SSL = os.getenv('SAM_DB_REQUIRE_SSL', 'false').lower() in ('true', '1', 'yes')

    # --------------------------------------------------------- system_status DB
    STATUS_DB_USERNAME    = os.getenv('STATUS_DB_USERNAME', '')
    STATUS_DB_PASSWORD    = os.getenv('STATUS_DB_PASSWORD', '')
    STATUS_DB_SERVER      = os.getenv('STATUS_DB_SERVER', '')
    STATUS_DB_NAME        = os.getenv('STATUS_DB_NAME', 'system_status')
    STATUS_DB_REQUIRE_SSL = os.getenv('STATUS_DB_REQUIRE_SSL', 'false').lower() in ('true', '1', 'yes')

    # -------------------------------------------------------------------- Mail
    MAIL_SERVER       = os.getenv('MAIL_SERVER', 'ndir.ucar.edu')
    MAIL_PORT         = int(os.getenv('MAIL_PORT', '25'))
    MAIL_USE_TLS      = os.getenv('MAIL_USE_TLS', 'false').lower() == 'true'
    MAIL_USERNAME     = os.getenv('MAIL_USERNAME', '')
    MAIL_PASSWORD     = os.getenv('MAIL_PASSWORD', '')
    MAIL_DEFAULT_FROM = os.getenv('MAIL_DEFAULT_FROM', 'sam-admin@ucar.edu')

    # ---------------------------------------------------------------- Validate
    @classmethod
    def validate(cls):
        """Fail fast at startup if required SAM env vars are missing."""
        required = {
            'SAM_DB_USERNAME': cls.SAM_DB_USERNAME,
            'SAM_DB_PASSWORD': cls.SAM_DB_PASSWORD,
            'SAM_DB_SERVER':   cls.SAM_DB_SERVER,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(
                "Missing required environment variables:\n" +
                "".join(f"  {k}\n" for k in missing) +
                "\nSee .env.example for a template."
            )
