"""
Custom exceptions for PBS collectors.
"""


class CollectorError(Exception):
    """Base exception for all collector errors."""
    pass


class PBSError(CollectorError):
    """Base exception for PBS-related errors."""
    pass


class PBSCommandError(PBSError):
    """Raised when a PBS command fails."""
    pass


class PBSParseError(PBSError):
    """Raised when PBS output cannot be parsed."""
    pass


class SSHError(CollectorError):
    """Raised when SSH connection or command fails."""
    pass


class APIError(CollectorError):
    """Base exception for API-related errors."""
    pass


class APIAuthError(APIError):
    """Raised for authentication failures (401/403)."""
    pass


class APIValidationError(APIError):
    """Raised for validation errors (400)."""
    pass


class ConfigError(CollectorError):
    """Raised for configuration errors."""
    pass
