import os
import tempfile
import unittest
from unittest.mock import patch

from youtube_analytics import attach_video_titles, default_analytics_scopes, summarize_channel_overview, summarize_video_metrics

try:
    import app as app_module
    from app import app
except Exception:  # pragma: no cover - keeps tests import-safe in minimal environments
    app_module = None
    app = None


class TestYoutubeAnalytics(unittest.TestCase):
    def test_default_scopes_include_analytics(self):
        scopes = default_analytics_scopes()
        self.assertIn("https://www.googleapis.com/auth/youtube.readonly", scopes)
        self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", scopes)
        self.assertIn("https://www.googleapis.com/auth/youtube.upload", scopes)

    def test_summarize_video_metrics_returns_clean_rows(self):
        rows = summarize_video_metrics([
            {
                "video_id": "abc123",
                "views": 1200,
                "averageViewPercentage": 46.5,
                "impressions": 30000,
                "impressionsClickThroughRate": 4.0,
            },
            {
                "video_id": "def456",
                "views": 800,
                "averageViewPercentage": 31.2,
                "impressions": 42000,
                "impressionsClickThroughRate": 1.9,
            },
        ])

        self.assertEqual(rows[0]["video_id"], "abc123")
        self.assertAlmostEqual(rows[0]["retention_pct"], 46.5)
        self.assertAlmostEqual(rows[0]["ctr"], 4.0)
        self.assertEqual(rows[1]["video_id"], "def456")

    def test_attach_video_titles_merges_and_falls_back_to_id(self):
        videos = [
            {"video_id": "abc123", "views": 1200, "retention_pct": 46.5},
            {"video_id": "missing999", "views": 5, "retention_pct": 10.0},
        ]
        titles = {"abc123": "Why Your Phone Knows What You're Thinking"}

        enriched = attach_video_titles(videos, titles)

        self.assertEqual(enriched[0]["title"], "Why Your Phone Knows What You're Thinking")
        self.assertEqual(enriched[0]["url"], "https://youtu.be/abc123")
        # No matching title -> falls back to the raw id instead of a blank cell.
        self.assertEqual(enriched[1]["title"], "missing999")
        self.assertEqual(enriched[1]["url"], "https://youtu.be/missing999")
        # Original fields (views, retention_pct) are preserved, not dropped.
        self.assertEqual(enriched[0]["views"], 1200)

    def test_summarize_channel_overview_aggregates_valid_metrics(self):
        summary = summarize_channel_overview(
            rows=[
                {"video_id": "a", "views": 100, "likes": 30, "comments": 5, "shares": 7, "averageViewPercentage": 40.0, "duration_seconds": 3600},
                {"video_id": "b", "views": 500, "likes": 80, "comments": 20, "shares": 10, "averageViewPercentage": 70.0, "duration_seconds": 120},
                {"video_id": "c", "views": 200, "likes": 45, "comments": 8, "shares": 11, "averageViewPercentage": 55.0, "duration_seconds": 240},
            ],
            channel_stats={"viewCount": "900", "subscriberCount": "1200", "videoCount": "3"},
        )

        self.assertEqual(summary["total_videos"], 3)
        self.assertEqual(summary["total_views"], 800)
        self.assertEqual(summary["total_subscribers"], 1200)
        self.assertEqual(summary["total_likes"], 155)
        self.assertEqual(summary["top_video_id"], "b")
        self.assertEqual(summary["top_video_views"], 500)
        self.assertEqual(summary["regular_videos"]["total_videos"], 2)
        self.assertEqual(summary["regular_videos"]["total_views"], 300)
        self.assertEqual(summary["shorts"]["total_videos"], 1)
        self.assertEqual(summary["shorts"]["top_video_id"], "b")

    def test_reset_oauth_token_route(self):
        if app is None:
            self.skipTest("Flask app unavailable")

        # Point the route at a throwaway token.json in a temp dir instead of
        # the real project one — this route deletes the file it targets, and
        # BASE_DIR previously pointed at the real project dir, so running this
        # test used to wipe the developer's actual cached OAuth token.
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_token = os.path.join(tmp_dir, "token.json")
            with open(fake_token, "w", encoding="utf-8") as f:
                f.write("{}")

            with patch.object(app_module, "BASE_DIR", tmp_dir):
                client = app.test_client()
                resp = client.post("/api/youtube/auth/reset")
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertIn("Re-auth", data.get("message", ""))
                self.assertFalse(os.path.exists(fake_token), "reset route should delete the token file")

    def test_youtube_oauth_login_route(self):
        if app is None:
            self.skipTest("Flask app unavailable")

        client = app.test_client()
        resp = client.post("/api/youtube/auth/login")
        self.assertIn(resp.status_code, (200, 400, 500))
        data = resp.get_json() or {}
        self.assertTrue(
            "message" in data or "error" in data,
            "OAuth login route should return a JSON status message."
        )


if __name__ == "__main__":
    unittest.main()
