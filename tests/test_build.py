"""Behaviour of the profile builder: wrapping, weather words, patch selection, slicing, rendering."""
from __future__ import annotations

import json
import sys
import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build  # noqa: E402


def pr(repo: str, number: int, state: str, created: str, title: str = "Title here") -> dict[str, object]:
    return {
        "repository": repo,
        "number": number,
        "title": title,
        "url": f"https://github.com/{repo}/pull/{number}",
        "state": state,
        "created_at": created,
    }


class WrapTests(unittest.TestCase):
    def test_wraps_on_words_within_the_width(self) -> None:
        lines = build.wrap("one two three four", 9)
        self.assertEqual([build.plain(line) for line in lines], ["one two", "three", "four"])

    def test_keeps_a_double_space_inside_a_line(self) -> None:
        lines = build.wrap("FDB9  561F", 20)
        self.assertEqual(build.plain(lines[0]), "FDB9  561F")

    def test_a_link_becomes_an_underlined_span_carrying_its_url(self) -> None:
        (line,) = build.wrap("see [Omarchy](https://omarchy.org) daily", 40)
        link = next(span for span in line if span.url)
        self.assertEqual((link.text, link.cls, link.url), ("Omarchy", "u", "https://omarchy.org"))

    def test_bold_and_underline_markup_become_classes(self) -> None:
        (line,) = build.wrap("**kris** [__--linux__]", 40)
        self.assertEqual([(s.text, s.cls) for s in line], [("kris", "h"), (" [", ""), ("--linux", "u"), ("]", "")])

    def test_a_word_wider_than_the_width_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build.wrap("abcdefghij", 5)


class WeatherTests(unittest.TestCase):
    def test_a_wmo_code_reads_as_words(self) -> None:
        self.assertEqual(build.describe_weather(3), "overcast")
        self.assertEqual(build.describe_weather(61), "slight rain")

    def test_an_unknown_code_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build.describe_weather(42)

    def test_the_greeting_line_names_place_conditions_temperature_wind_and_time(self) -> None:
        weather = {"current": {"time": "2026-10-13T11:45", "temperature_2m": 14.7, "weather_code": 3, "wind_speed_10m": 17.6}}
        self.assertEqual(
            build.weather_line(weather, "Amsterdam"),
            "overcast, 15 °C, wind 18 km/h — Amsterdam, 2026-10-13 11:45",
        )


class UptimeTests(unittest.TestCase):
    def test_counts_days_hours_and_minutes_since_boot(self) -> None:
        boot = datetime(2026, 8, 26, 5, 33, tzinfo=timezone.utc)
        now = datetime(2026, 10, 6, 11, 45, tzinfo=timezone.utc)
        self.assertEqual(build.format_uptime(boot, now), "41 days, 6:12")

    def test_pads_the_minutes(self) -> None:
        boot = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 10, 2, 3, 5, tzinfo=timezone.utc)
        self.assertEqual(build.format_uptime(boot, now), "1 day, 3:05")


class PatchSelectionTests(unittest.TestCase):
    def test_open_and_merged_are_kept_newest_first_and_closed_unmerged_dropped(self) -> None:
        prs = [
            pr("a/a", 1, "merged", "2026-09-01T00:00:00Z"),
            pr("b/b", 2, "closed", "2026-09-02T00:00:00Z"),
            pr("c/c", 3, "open", "2026-09-03T00:00:00Z"),
        ]
        patches = build.select_patches(prs, max_entries=10, overrides={})
        self.assertEqual([p.ref for p in patches], ["c/c#3", "a/a#1"])

    def test_the_newest_entries_win_when_capped(self) -> None:
        prs = [pr("a/a", n, "open", f"2026-09-{n:02d}T00:00:00Z") for n in range(1, 6)]
        patches = build.select_patches(prs, max_entries=2, overrides={})
        self.assertEqual([p.ref for p in patches], ["a/a#5", "a/a#4"])

    def test_curated_text_wins_over_the_title_and_merged_is_marked(self) -> None:
        prs = [pr("a/a", 1, "merged", "2026-09-01T00:00:00Z", "Match non-ASCII terms"), pr("a/a", 2, "open", "2026-09-02T00:00:00Z", "SQLite FTS: fix the thing")]
        patches = build.select_patches(prs, max_entries=10, overrides={"a/a#1": "non-Latin search terms now match"})
        self.assertEqual(patches[0].text, "SQLite FTS: fix the thing")
        self.assertEqual(patches[1].text, "non-Latin search terms now match (merged)")

    def test_an_override_for_a_pull_request_that_is_not_shown_is_reported(self) -> None:
        prs = [pr("a/a", 1, "open", "2026-09-01T00:00:00Z")]
        with self.assertRaises(ValueError):
            build.select_patches(prs, max_entries=10, overrides={"z/z#9": "gone"})


class SliceTests(unittest.TestCase):
    def rows(self) -> list[build.Row]:
        return [
            build.Row(0, [build.Span("Header")], url=None),
            build.Row(1, [build.Span("Linked one")], url="https://one"),
            build.Row(2, [build.Span("Linked one, line two")], url="https://one"),
            build.Row(3, [build.Span("Plain")], url=None),
            build.Row(5, [build.Span("Linked two")], url="https://two"),
            build.Row(6, [build.Span("Linked three")], url="https://three"),
            build.Row(8, [build.Span("Tail")], url=None),
        ]

    def test_every_link_run_is_its_own_slice_and_plain_runs_fill_the_gaps(self) -> None:
        slices = build.slice_rows(self.rows(), page_height=build.baseline(8) + build.BOTTOM_PAD)
        self.assertEqual([s.url for s in slices], [None, "https://one", None, "https://two", "https://three", None])

    def test_slices_are_contiguous_from_the_top_to_the_bottom_of_the_page(self) -> None:
        height = build.baseline(8) + build.BOTTOM_PAD
        slices = build.slice_rows(self.rows(), page_height=height)
        self.assertEqual(slices[0].top, 0)
        self.assertEqual(slices[-1].bottom, height)
        for before, after in zip(slices, slices[1:]):
            self.assertEqual(before.bottom, after.top)
        self.assertTrue(all(s.bottom > s.top for s in slices))

    def test_a_link_slice_starts_above_its_first_baseline_and_ends_under_its_last(self) -> None:
        slices = build.slice_rows(self.rows(), page_height=build.baseline(8) + build.BOTTOM_PAD)
        one = slices[1]
        self.assertEqual((one.top, one.bottom), (build.baseline(1) - 16, build.baseline(2) + 6))


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = tomllib.loads((ROOT / "profile.toml").read_text())
        cls.patches = {"fetched_at": "2026-10-13T10:00:00Z", "pull_requests": [
            pr("spotweb/spotweb", 1001, "merged", "2026-09-28T21:15:14Z", "Match non-ASCII search terms"),
            pr("omacom/omarchy", 12231, "open", "2026-09-17T06:26:10Z", "List local audio outputs before network ones"),
        ]}
        cls.weather = {"timezone": "Europe/Amsterdam", "current": {"time": "2026-10-13T11:45", "temperature_2m": 14.7, "weather_code": 3, "wind_speed_10m": 17.6}}
        cls.profile["patches"]["text"] = {"spotweb/spotweb#1001": "non-Latin search terms now match"}
        cls.output = build.render(cls.profile, cls.patches, cls.weather)

    def test_the_readme_references_every_slice_in_order_and_links_the_linked_ones(self) -> None:
        readme = self.output.readme
        positions = [readme.index(f"/{name}") for name, _ in self.output.svgs]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('<a href="https://github.com/spotweb/spotweb/pull/1001">', readme)
        self.assertIn('<a href="https://github.com/omacom/omarchy/pull/12231">', readme)

    def test_the_greeting_carries_the_weather_and_the_man_page_the_patches(self) -> None:
        first = self.output.svgs[0][1]
        self.assertIn("overcast, 15 °C, wind 18 km/h — Amsterdam, 2026-10-13 11:45", first)
        joined = "".join(svg for _, svg in self.output.svgs)
        self.assertIn("omacom/omarchy#12231", joined)
        self.assertIn("non-Latin search terms now match (merged)", joined)

    def test_every_slice_is_a_viewbox_window_on_one_page_of_the_same_height(self) -> None:
        heights = set()
        tops = []
        for _, svg in self.output.svgs:
            box = build.VIEWBOX.search(svg)
            assert box is not None
            tops.append(int(box.group(1)))
            heights.add(int(build.PAGE_RECT.search(svg).group(1)))  # type: ignore[union-attr]
        self.assertEqual(tops, sorted(tops))
        self.assertEqual(len(heights), 1)

    def test_rendering_is_deterministic(self) -> None:
        again = build.render(self.profile, self.patches, self.weather)
        self.assertEqual(again.svgs, self.output.svgs)
        self.assertEqual(again.readme, self.output.readme)

    def test_the_asset_directory_is_named_by_the_content(self) -> None:
        self.assertRegex(self.output.asset_dir, r"^v[0-9a-f]{8}$")
        other = build.render(self.profile, self.patches, {**self.weather, "current": {**self.weather["current"], "temperature_2m": 9.0}})
        self.assertNotEqual(other.asset_dir, self.output.asset_dir)


if __name__ == "__main__":
    unittest.main()
