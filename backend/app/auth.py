"""Compatibility for the historically exported limiter; HTTP auth lives in app.api.auth."""

from app.rate_limit import RateLimiter

__all__ = ["RateLimiter"]
