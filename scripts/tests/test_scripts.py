"""Tests for static_scan.py and har_analyze.py.

Run from the skill root:  python3 -m unittest discover -s scripts/tests -v
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import har_analyze  # noqa: E402
import static_scan  # noqa: E402
from har_fixture import build as build_har  # noqa: E402

SAMPLE_APP = os.path.join(HERE, "fixtures", "sample-app")


class StaticScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = static_scan.run(SAMPLE_APP, [], "low")
        cls.findings = cls.result["findings"]

    def by_id(self, finding_id: str):
        return [f for f in self.findings if f["id"] == finding_id]

    def files_for(self, finding_id: str):
        return {f["file"] for f in self.by_id(finding_id)}

    def test_scans_the_fixture(self) -> None:
        self.assertGreaterEqual(self.result["files_scanned"], 12)
        self.assertTrue(any("TanStack" in lib for lib in self.result["libraries"]))

    def test_per_row_query_on_agent_row(self) -> None:
        hits = self.by_id("per-row-query")
        self.assertEqual({h["file"] for h in hits}, {"src/components/agent-row.tsx"})
        self.assertEqual(hits[0]["confidence"], "high")
        self.assertTrue(any("src/components/agent-list.tsx" in site for site in hits[0]["extra"]["rendered_in"]))

    def test_clean_row_is_not_flagged(self) -> None:
        self.assertNotIn("src/components/clean-list.tsx", self.files_for("per-row-query"))
        self.assertNotIn("src/components/clean-list.tsx", self.files_for("shared-poll-timer"))
        self.assertNotIn("src/components/clean-list.tsx", self.files_for("always-on-poll"))
        self.assertNotIn("src/components/clean-list.tsx", self.files_for("broad-invalidation"))

    def test_shared_poll_timer(self) -> None:
        hits = self.by_id("shared-poll-timer")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "src/lib/use-billing-summary.ts")
        # The fixture uses TanStack Query, whose same-key observers re-sync: ownership, not a multiplier.
        self.assertEqual(hits[0]["severity"], "P2")
        self.assertIn("re-sync", hits[0]["title"])
        self.assertEqual(len(hits[0]["extra"]["importers"]), 2)

    def test_always_on_poll_resolves_constant(self) -> None:
        hits = [h for h in self.by_id("always-on-poll") if h["file"] == "src/lib/use-billing-summary.ts"]
        self.assertEqual(len(hits), 1)
        self.assertIn("60000 ms", hits[0]["title"])

    def test_default_stale_zero(self) -> None:
        hits = self.by_id("default-stale-zero")
        self.assertEqual([h["file"] for h in hits], ["src/main.tsx"])
        self.assertEqual(hits[0]["confidence"], "high")
        self.assertEqual([h["file"] for h in self.by_id("default-retry")], ["src/main.tsx"])

    def test_zero_freshness(self) -> None:
        self.assertEqual(self.files_for("zero-freshness"), {"src/components/agent-list.tsx"})

    def test_broad_invalidation_in_input_handler_is_p0(self) -> None:
        hits = self.by_id("broad-invalidation")
        self.assertEqual([h["file"] for h in hits], ["src/components/search-box.tsx"])
        self.assertEqual(hits[0]["severity"], "P0")

    def test_manual_fetching(self) -> None:
        self.assertEqual(self.files_for("effect-fetch"), {"src/components/legacy-panel.tsx"})
        polls = self.by_id("hand-rolled-poll")
        self.assertEqual([p["file"] for p in polls], ["src/components/legacy-panel.tsx"])
        self.assertEqual(polls[0]["severity"], "P0")

    def test_hover_prefetch_is_info(self) -> None:
        hits = self.by_id("hover-prefetch")
        self.assertEqual([h["file"] for h in hits], ["src/components/legacy-panel.tsx"])
        self.assertEqual(hits[0]["severity"], "info")

    def test_loader_patterns(self) -> None:
        self.assertEqual(self.files_for("serial-awaits"), {"src/lib/loaders.ts"})
        self.assertEqual(self.files_for("unbounded-parallel"), {"src/lib/loaders.ts"})
        fetch_all = self.by_id("fetch-all")
        self.assertEqual({f["file"] for f in fetch_all}, {"src/lib/loaders.ts"})
        self.assertEqual(len(fetch_all), 2)

    def test_detail_for_label(self) -> None:
        self.assertEqual(self.files_for("detail-for-label"), {"src/components/agent-row.tsx"})

    def test_key_drift(self) -> None:
        hits = self.by_id("key-drift")
        self.assertEqual(len(hits), 1)
        self.assertIn("agentNames", hits[0]["title"])
        self.assertIn("agent-names", hits[0]["title"])

    def test_markdown_renders(self) -> None:
        md = static_scan.to_markdown(self.result)
        self.assertIn("| # | Severity |", md)
        self.assertIn("per-row-query", md)

    def test_cli_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "scan.json")
            code = static_scan.main([SAMPLE_APP, "--format", "json", "--out", out])
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertIn("findings", data)

    def test_function_body_skips_destructured_params(self) -> None:
        text = "function Row({ id }: Props) { return useQuery(id) }"
        idx = static_scan.find_function_body(text, len("function Row"))
        self.assertEqual(text[idx:idx + 8], "{ return")

    def test_function_body_through_memo(self) -> None:
        text = "const Row = memo(function Row({ id }) { return null })"
        idx = static_scan.find_function_body(text, len("const Row = "))
        self.assertEqual(text[idx:idx + 8], "{ return")

    def test_function_body_expression_arrow_is_skipped(self) -> None:
        text = "const Row = ({ id }) => <li>{id}</li>\nconst x = { a: 1 }"
        self.assertEqual(static_scan.find_function_body(text, len("const Row = ")), -1)


class HarAnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "capture.har")
        with open(cls.path, "w", encoding="utf-8") as handle:
            json.dump(build_har(), handle)
        reqs = har_analyze.load_capture(cls.path)
        args = argparse.Namespace(api_filter=None, ignore=None, dup_window_ms=2000, burst_min=5,
                                  burst_ms=1500, chain_gap_ms=120, large_kb=200)
        cls.result = har_analyze.analyze(reqs, args)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_static_assets_excluded(self) -> None:
        self.assertEqual(self.result["summary"]["static_excluded"], 3)
        self.assertEqual(self.result["summary"]["api"], self.result["summary"]["total"] - 3)

    def test_burst_detected(self) -> None:
        bursts = self.result["bursts"]
        self.assertEqual(len(bursts), 1)
        self.assertIn("/api/v1/agents/{id}", bursts[0]["template"])
        self.assertIn("interleaved clocks", next(p for p in self.result["polling"] if p["timers"] > 1)["note"])
        self.assertEqual(bursts[0]["distinct_ids"], 20)
        self.assertGreaterEqual(bursts[0]["peak_in_flight"], 5)

    def test_batch_request_recognised(self) -> None:
        self.assertEqual(self.result["summary"]["batched_requests"], 1)
        batch_rows = [t for t in self.result["templates"] if t["max_batch"]]
        self.assertEqual(batch_rows[0]["max_batch"], 20)

    def test_stacked_poll_timers(self) -> None:
        billing = [p for p in self.result["polling"] if "/v1/billing/summary" in p["key"]]
        self.assertEqual(len(billing), 1)
        self.assertEqual(billing[0]["timers"], 2)
        self.assertAlmostEqual(billing[0]["period_ms"], 60_000, delta=1000)
        self.assertAlmostEqual(billing[0]["effective_period_ms"], 30_000, delta=1000)

    def test_single_poll_timer(self) -> None:
        jobs = [p for p in self.result["polling"] if "/v1/jobs/" in p["key"]]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["timers"], 1)
        self.assertAlmostEqual(jobs[0]["period_ms"], 5000, delta=100)

    def test_duplicates(self) -> None:
        dups = {d["key"]: d for d in self.result["duplicates"]}
        workspaces = next(d for k, d in dups.items() if "/api/v1/workspaces" in k)
        self.assertEqual(workspaces["count"], 3)
        self.assertEqual(workspaces["in_flight_overlap"], 1)
        search = [d for k, d in dups.items() if "/v1/search" in k]
        self.assertEqual(len(search), 1)
        self.assertEqual(search[0]["count"], 2)
        self.assertTrue(search[0]["verified_same_body"])

    def test_serial_chain(self) -> None:
        chains = self.result["serial_chains"]
        self.assertTrue(chains)
        longest = chains[0]
        self.assertGreaterEqual(longest["length"], 4)
        self.assertEqual(longest["steps"][0], "GET /v1/me")
        self.assertIn("GET /v1/flags", longest["steps"])
        # Requests that overlap with others are parallel work, not links in a serial chain.
        for chain in chains:
            self.assertFalse(any("/api/v1/agents/agent_" in step for step in chain["steps"]), chain["steps"])

    def test_repeated_404(self) -> None:
        errs = [e for e in self.result["errors"] if e["key"].startswith("404")]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["count"], 4)
        self.assertEqual(errs[0]["same_url_repeated"], 1)

    def test_large_response(self) -> None:
        self.assertEqual(self.result["large_responses"][0]["request"], "GET /v1/export")

    def test_per_page_counts(self) -> None:
        self.assertIn("shell", self.result["summary"]["per_page"])

    def test_plain_json_rows(self) -> None:
        rows = [
            {"method": "GET", "url": "https://x.test/v1/a", "started": 1762250400000, "duration_ms": 50},
            {"method": "GET", "url": "https://x.test/v1/a", "started": 1762250400100, "duration_ms": 50},
        ]
        path = os.path.join(self.tmp.name, "rows.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle)
        reqs = har_analyze.load_capture(path)
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[1].start_ms - reqs[0].start_ms, 100)

    def test_markdown_renders(self) -> None:
        md = har_analyze.to_markdown(self.result, self.path)
        self.assertIn("## Per-row bursts", md)
        self.assertIn("2 interleaved clocks", md)

    def test_cli_rejects_missing_file(self) -> None:
        self.assertEqual(har_analyze.main(["/nonexistent.har"]), 2)


if __name__ == "__main__":
    unittest.main()
