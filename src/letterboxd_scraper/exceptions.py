"""Domain-specific exceptions raised by the scraper."""


class LetterboxdScraperError(Exception):
    """Base class for all package errors."""


class ConfigurationError(LetterboxdScraperError):
    """Raised when a configuration file is invalid or contradictory."""


class FetchError(LetterboxdScraperError):
    """Raised when an HTTP resource cannot be retrieved after retries."""


class ParseError(LetterboxdScraperError):
    """Raised when required data cannot be parsed from a response."""


class ValidationError(LetterboxdScraperError):
    """Raised when a scrape result violates configured safety checks."""
