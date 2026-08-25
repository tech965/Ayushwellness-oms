"""Celery task definitions, grouped by domain.

Status: PLANNED. Will include tasks for Shopify sync, Shiprocket sync,
tracking updates, delay detection, notifications, automation execution,
and analytics aggregation. Every task must be retryable, use exponential
backoff, and be idempotent (see docs/architecture/backend.md).
"""
