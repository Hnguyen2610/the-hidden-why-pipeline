#!/usr/bin/env python3
"""Helpers for the YouTube Analytics MVP.

This module keeps the scope list and data-shaping logic separate so the app and
upload flow can both use the same permissions without duplicating logic.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Iterable


def default_analytics_scopes() -> list[str]:
    """Return the OAuth scopes required for upload + analytics + channel reads."""
    return [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]


def has_required_scopes(creds: Any) -> bool:
    """Return whether the cached token includes the required Google scopes."""
    if creds is None:
        return False
    granted_scopes = set(getattr(creds, "scopes", []) or [])
    return set(default_analytics_scopes()).issubset(granted_scopes)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_video_metrics(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw analytics rows into a sorted, UI-friendly list."""
    normalized: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        video_id = str(row.get("video_id") or row.get("videoId") or row.get("id") or "").strip()
        if not video_id:
            continue

        views = int(_coerce_float(row.get("views", 0), 0.0))
        impressions = int(_coerce_float(row.get("impressions", 0), 0.0))
        retention_pct = _coerce_float(row.get("averageViewPercentage", row.get("retention_pct", 0)), 0.0)
        ctr = _coerce_float(row.get("impressionsClickThroughRate", row.get("ctr", 0)), 0.0)
        watch_minutes = _coerce_float(row.get("estimatedMinutesWatched", 0), 0.0)
        avg_view_seconds = _coerce_float(row.get("averageViewDuration", 0), 0.0)
        likes = int(_coerce_float(row.get("likes", 0), 0.0))
        comments = int(_coerce_float(row.get("comments", 0), 0.0))
        shares = int(_coerce_float(row.get("shares", 0), 0.0))

        normalized.append({
            "video_id": video_id,
            "title": str(row.get("title") or video_id),
            "url": row.get("url") or f"https://youtu.be/{video_id}",
            "duration_seconds": int(_coerce_float(row.get("duration_seconds", 0), 0.0)),
            "duration_label": row.get("duration_label", ""),
            "views": views,
            "impressions": impressions,
            "retention_pct": retention_pct,
            "ctr": ctr,
            "avg_retention_label": f"{retention_pct:.1f}%",
            "ctr_label": f"{ctr:.2f}%",
            "watch_minutes": watch_minutes,
            "avg_view_seconds": avg_view_seconds,
            "likes": likes,
            "comments": comments,
            "shares": shares,
        })

    normalized.sort(key=lambda item: (-item["views"], -item["retention_pct"]))
    return normalized


def summarize_channel_overview(rows: Iterable[dict[str, Any]], channel_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate per-video analytics into a dashboard-friendly channel summary.

    This keeps the total counts consistent with the exact metrics We have from
    the Analytics API, while using channel-level metadata where available for
    subscriber count and total videos.
    """
    normalized_rows = summarize_video_metrics(rows)

    def summarize_format(format_rows: list[dict[str, Any]]) -> dict[str, Any]:
        top_format_video = max(
            format_rows,
            key=lambda item: (int(item.get("views", 0)), int(item.get("likes", 0))),
            default=None,
        )
        return {
            "total_videos": len(format_rows),
            "total_views": sum(int(item.get("views", 0)) for item in format_rows),
            "total_likes": sum(int(item.get("likes", 0)) for item in format_rows),
            "total_comments": sum(int(item.get("comments", 0)) for item in format_rows),
            "total_shares": sum(int(item.get("shares", 0)) for item in format_rows),
            "total_watch_minutes": int(sum(_coerce_float(item.get("watch_minutes", 0), 0.0) for item in format_rows)),
            "top_video_id": top_format_video.get("video_id", "") if top_format_video else "",
            "top_video_title": top_format_video.get("title", "") if top_format_video else "",
            "top_video_views": int(top_format_video.get("views", 0)) if top_format_video else 0,
            "top_video_likes": int(top_format_video.get("likes", 0)) if top_format_video else 0,
        }

    total_views = sum(int(item.get("views", 0)) for item in normalized_rows)
    total_likes = sum(int(item.get("likes", 0)) for item in normalized_rows)
    total_comments = sum(int(item.get("comments", 0)) for item in normalized_rows)
    total_shares = sum(int(item.get("shares", 0)) for item in normalized_rows)
    total_minutes = sum(_coerce_float(item.get("watch_minutes", 0), 0.0) for item in normalized_rows)

    top_video = None
    if normalized_rows:
        top_video = max(normalized_rows, key=lambda item: (int(item.get("views", 0)), int(item.get("likes", 0))))

    stats = channel_stats or {}
    total_videos = _coerce_float(stats.get("videoCount", len(normalized_rows)), 0.0)
    total_subscribers = _coerce_float(stats.get("subscriberCount", 0), 0.0)
    total_channel_views = total_views

    summary = {
        "total_videos": int(total_videos),
        "total_views": int(total_channel_views) if total_channel_views else int(total_views),
        "total_subscribers": int(total_subscribers),
        "total_likes": int(total_likes),
        "total_comments": int(total_comments),
        "total_shares": int(total_shares),
        "total_watch_minutes": int(total_minutes),
        "top_video_id": top_video.get("video_id") if top_video else "",
        "top_video_title": top_video.get("title", top_video.get("video_id", "")) if top_video else "",
        "top_video_views": int(top_video.get("views", 0)) if top_video else 0,
        "top_video_likes": int(top_video.get("likes", 0)) if top_video else 0,
        "regular_videos": summarize_format([
            item for item in normalized_rows
            if int(item.get("duration_seconds", 0)) > 180
        ]),
        "shorts": summarize_format([
            item for item in normalized_rows
            if 0 < int(item.get("duration_seconds", 0)) <= 180
        ]),
    }
    return summary


def parse_iso8601_duration(duration: str) -> int:
    """Parse a YouTube Data API ISO-8601 duration (e.g. 'PT1M52S') into whole
    seconds. Returns 0 for anything that doesn't match (never raises) — there
    is no official "is this a Short" flag in the API, so callers use this as
    the closest available proxy (YouTube Shorts must be <=180s)."""
    if not duration:
        return 0
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", duration)
    if not match:
        return 0
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def format_duration(total_seconds: int) -> str:
    """Format whole seconds as m:ss (or h:mm:ss past an hour)."""
    total_seconds = max(0, int(total_seconds))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def attach_video_titles(
    videos: list[dict[str, Any]],
    titles_by_id: dict[str, str],
    durations_by_id: "dict[str, int] | None" = None,
) -> list[dict[str, Any]]:
    """Merge in each video's title, watch URL, and (optionally) duration from
    id -> value lookups (e.g. from the YouTube Data API's videos().list()). A
    row whose id has no title match keeps the raw id as its title, so the
    table never shows a blank cell just because one lookup failed or a video
    was deleted."""
    durations_by_id = durations_by_id or {}
    enriched = []
    for video in videos:
        video_id = video.get("video_id", "")
        title = titles_by_id.get(video_id) or video_id
        duration_seconds = durations_by_id.get(video_id, 0)
        enriched.append({
            **video,
            "title": title,
            "url": f"https://youtu.be/{video_id}" if video_id else "",
            "duration_seconds": duration_seconds,
            "duration_label": format_duration(duration_seconds) if duration_seconds else "",
        })
    return enriched


def _extract_http_error_details(exc: Exception) -> tuple[str, str, str]:
    """Best-effort extraction of (reason, message, url) from a googleapiclient
    HttpError's JSON error body. Never raises — falls back to generic values
    when the shape doesn't match what Google normally sends."""
    reason, message, url = "", str(exc), ""
    content = getattr(exc, "content", None)
    if content:
        try:
            payload = json.loads(content)
            error = payload.get("error", {})
            errors_list = error.get("errors") or []
            reason = (errors_list[0].get("reason") if errors_list else "") or ""
            message = error.get("message") or message
        except (ValueError, TypeError, AttributeError, IndexError):
            pass
    url_match = re.search(r"https?://\S+", message)
    if url_match:
        url = url_match.group(0).rstrip(".,)")
    return reason, message, url


def fetch_video_analytics(analytics_service: Any, max_results: int = 10, start_days: int = 30) -> list[dict[str, Any]]:
    """Query YouTube Analytics for the most recent videos and normalize the result."""
    if analytics_service is None:
        return []

    end_date = date.today().strftime("%Y-%m-%d")
    start_date = (date.today() - timedelta(days=start_days)).strftime("%Y-%m-%d")

    try:
        # "impressions"/"impressionsClickThroughRate" are NOT valid metric
        # identifiers for this interactive reports.query() endpoint (confirmed
        # by a live 400 "Unknown identifier" response) — YouTube Studio shows
        # impressions/CTR, but the YouTube Analytics API has never exposed them
        # here; Google only started exposing thumbnail-impression data in Jan
        # 2026, and that appears to live in the separate bulk YouTube Reporting
        # API (scheduled report jobs), not this synchronous query call. The
        # metrics below, unlike impressions/CTR, are long-standing and were
        # confirmed against a real channel before being added here.
        report = analytics_service.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views,averageViewPercentage,estimatedMinutesWatched,averageViewDuration,likes,comments,shares",
            dimensions="video",
            sort="-views",
            maxResults=max_results,
        ).execute()
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (401, 403):
            reason, _message, url = _extract_http_error_details(exc)
            if reason == "accessNotConfigured":
                # A 403 here doesn't always mean the OAuth scope is wrong — Google
                # returns the exact same status when the "YouTube Analytics API"
                # simply hasn't been enabled for this Cloud project. Re-authenticating
                # does nothing for that case, so it needs its own message + the
                # project-specific "Enable API" link Google includes in the error.
                err = PermissionError(
                    "YouTube Analytics API is not enabled for this Google Cloud project. "
                    "Enable it via the link below, then retry in a minute or two."
                )
                err.issue_type = "api_disabled"
                err.enable_url = url
                raise err from exc
            err = PermissionError(
                "YouTube Analytics permission denied. Re-authenticate with the new yt-analytics.readonly scope."
            )
            err.issue_type = "reauth"
            raise err from exc
        raise

    rows: list[dict[str, Any]] = []
    headers = [header.get("name") for header in report.get("columnHeaders", [])]
    for row in report.get("rows", []):
        payload = dict(zip(headers, row))
        rows.append({
            "video_id": payload.get("video"),
            "views": payload.get("views", 0),
            "averageViewPercentage": payload.get("averageViewPercentage", 0),
            "estimatedMinutesWatched": payload.get("estimatedMinutesWatched", 0),
            "averageViewDuration": payload.get("averageViewDuration", 0),
            "likes": payload.get("likes", 0),
            "comments": payload.get("comments", 0),
            "shares": payload.get("shares", 0),
        })

    return summarize_video_metrics(rows)
