"""Axis formatting and bounded, memory-only plot snapshots."""

from collections import OrderedDict
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from ascendant import plotting, indicators
import math
from ascendant.web import plots


class PlottingTests(unittest.TestCase):
    def test_index_axis_fits_narrow_values_and_keeps_baseline_and_extremes(self):
        for values in ([99, 100, 101], [100, 100], [-150, 100, 180], [110, 120], []):
            low, high, step = plotting.index_y_axis(values)
            self.assertLess(low, min([100] + values))
            self.assertGreater(high, max([100] + values))
            self.assertGreater(step, 0)
        low, high, _ = plotting.index_y_axis([99, 100, 101])
        self.assertGreater(low, 95)
        self.assertLess(high, 105)

    def test_index_line_export_fits_values_but_bars_keep_zero(self):
        for style in ('line', 'bar'):
            figure, axis = plotting.plt.subplots()
            self.addCleanup(plotting.plt.close, figure)
            points = [{'label': str(i), 'value': value} for i, value in enumerate([980, 1000, 1020])]
            with patch.object(plotting.plt, 'subplots', return_value=(figure, axis)), \
                    patch.object(figure, 'savefig'), patch.object(plots.config.PLOTS_DIR.__class__, 'mkdir'):
                plots.save_web_plot_png({'name': 'Example', 'uid': '42'}, 'all', 'Views', 'Views', points,
                                       value_mode='index', chart_style=style)
            low, high = axis.get_ylim()
            self.assertLess(low, 100)
            self.assertGreater(high, 100)
            if style == 'line':
                self.assertGreater(low, 95)
                self.assertLess(high, 105)
            else:
                self.assertEqual(low, 0)

    def test_round_zero_based_axes(self):
        for values, expected in [([0], (1, 0.2)), ([10000, 30000], (40000, 10000)),
                                 ([100000], (150000, 50000))]:
            # Tick intervals are restricted to powers of ten times 1, 2, or 5.
            upper, step = plotting.nice_y_axis(values)
            self.assertGreater(upper, max(values))
            self.assertGreater(step, 0)
            self.assertEqual((upper, step), expected)
        upper, step = plotting.nice_y_axis([0.001, 0.002])
        self.assertAlmostEqual(upper, 0.0025)
        self.assertAlmostEqual(step, 0.0005)

    def test_png_axis_formatter_uses_commas_without_unnecessary_decimals(self):
        figure, axis = plotting.plt.subplots()
        self.addCleanup(plotting.plt.close, figure)
        plotting.configure_y_axis(axis, [10000, 30000])
        self.assertEqual(axis.get_ylim(), (0, 40000))
        formatter = axis.yaxis.get_major_formatter()
        self.assertEqual(formatter(10000, 0), "10,000")
        self.assertEqual(formatter(1000000, 0), "1,000,000")
        self.assertEqual(formatter(0.001, 0), "0.001")
        self.assertEqual(formatter(0, 0), "0")

    def test_robust_index_handles_zeros_extremes_and_negative_values(self):
        source = [0, 99, 99, 199, 999]
        result = indicators.performance_index(source)
        self.assertEqual(result["baseline"], 99)
        self.assertEqual(result["values"][1:3], [100, 100])
        self.assertAlmostEqual(result["values"][3], 125)
        self.assertAlmostEqual(result["values"][-1], 100 + 25 * math.log2(10))
        self.assertLess(result["values"][0], 0)
        self.assertEqual(source, [0, 99, 99, 199, 999])
        self.assertEqual(indicators.performance_index([0, 0])["values"], [100, 100])
        self.assertEqual(indicators.performance_index([]), {"baseline": None, "values": []})
        self.assertTrue(all(math.isfinite(value) for value in indicators.performance_index([1e308, 1e308])["values"]))
        self.assertEqual(indicators.moving_average([-10, -20, -30], 3)[-1], -20)

    def test_unusual_scores_use_only_previous_videos_and_preserve_flat_windows(self):
        history = [100 + 10 * index for index in range(20)]
        scores = indicators.unusual_scores(history + [10000])
        self.assertEqual(scores[:20], [None] * 20)
        self.assertGreater(scores[-1], 3.5)
        logs = [math.log1p(value) for value in history]
        from statistics import median
        expected = .6745 * (math.log1p(10000) - median(logs)) / median([abs(value - median(logs)) for value in logs])
        self.assertAlmostEqual(scores[-1], expected)
        self.assertLess(indicators.unusual_scores(history + [0])[-1], -3.5)
        self.assertEqual(indicators.unusual_scores([100] * 20 + [10000]), [None] * 21)
        self.assertEqual(indicators.unusual_scores(history + [10000, 0])[:21], scores)

    def test_index_and_log_exports_match_calculations_and_keep_negative_range(self):
        for mode in ("index", "log"):
            figure, axis = plotting.plt.subplots()
            self.addCleanup(plotting.plt.close, figure)
            raw = [0, 99, 99, 199, 999]
            points = [{"label": f"2026-09-{i + 1:02d} 12:00:00", "value": value} for i, value in enumerate(raw)]
            with patch.object(plotting.plt, "subplots", return_value=(figure, axis)), \
                    patch.object(figure, "savefig"), patch.object(plots.config.PLOTS_DIR.__class__, "mkdir"):
                plots.save_web_plot_png({"name": "Example", "uid": "42"}, "all", "Views", "Views", points,
                                       axis_mode="number", value_mode=mode, ma_periods=[5])
            expected = indicators.performance_index(raw)["values"] if mode == "index" else [math.log1p(v) / math.log(10) for v in raw]
            for observed, value in zip(axis.lines[0].get_ydata(), expected):
                self.assertAlmostEqual(observed, value)
            if mode == "index":
                self.assertLessEqual(axis.get_ylim()[0], min(expected))
                self.assertEqual(axis.get_ylabel(), "Performance index")
            else:
                self.assertAlmostEqual(axis.lines[-1].get_ydata()[-1], math.log1p(sum(raw) / 5) / math.log(10))
            self.assertFalse(axis.texts, "Selection context must stay outside the plotted area")

    def test_display_export_options_are_validated_and_cached_separately(self):
        for options in (("bad", False, "line"), ("index", "yes", "line"), ("raw", True, "pie")):
            with self.assertRaises(ValueError):
                plots.validate_display(*options)
        with patch.object(plots, "_pending_plots", OrderedDict()), \
                patch.object(plots, "save_web_plot_png", side_effect=["raw.png", "index.png", "flags.png", "bars.png"]) as save, \
                patch.object(plots.config.PLOTS_DIR.__class__, "is_file", return_value=True):
            key = plots.prepare_plot({"uid": "42"}, "all", "Views", "Views", [{"label": "x", "value": 1}])
            raw = plots.save_prepared_plot(key)
            index = plots.save_prepared_plot(key, value_mode="index")
            self.assertNotEqual(raw, index)
            self.assertEqual(plots.save_prepared_plot(key, value_mode="index"), index)
            plots.save_prepared_plot(key, value_mode="index", show_anomalies=True)
            plots.save_prepared_plot(key, value_mode="index", show_anomalies=True, chart_style="bar")
            self.assertEqual(save.call_count, 4)

    def test_snapshot_limit_empty_data_and_copy_isolation(self):
        with ExitStack() as stack:
            pending = OrderedDict()
            stack.enter_context(patch.object(plots, "_pending_plots", pending))
            stack.enter_context(patch.object(plots, "PENDING_PLOT_LIMIT", 2))
            save = stack.enter_context(patch.object(plots, "save_web_plot_png", return_value="sample.png"))
            selected = {"name": "Original", "uid": "42"}
            points = [{"label": "2026-09-01 00:00:00", "value": 0, "title": "Original"}]
            self.assertIsNone(plots.prepare_plot(selected, "all", "Views", "Views", []))
            first = plots.prepare_plot(selected, "all", "Views", "Views", points)
            second = plots.prepare_plot(selected, "all", "Views", "Views", points)
            selected["name"] = "Changed"
            points[0]["value"] = 100
            self.assertEqual(pending[second]["selected"]["name"], "Original")
            self.assertEqual(pending[second]["points"][0]["value"], 0)
            plots.prepare_plot(selected, "all", "Views", "Views", points)
            self.assertEqual(len(pending), 2)
            with self.assertRaisesRegex(ValueError, "expired"):
                plots.save_prepared_plot(first)
            save.assert_not_called()

    def test_number_export_uses_equal_positions_in_chronological_order(self):
        figure, axis = plotting.plt.subplots()
        self.addCleanup(plotting.plt.close, figure)
        points = [{"label": label, "value": value} for label, value in (
            ("2026-09-30 12:00:00", 30), ("2026-09-01 12:00:00", 10), ("2026-09-02 12:00:00", 20))]
        with patch.object(plotting.plt, "subplots", return_value=(figure, axis)), \
                patch.object(figure, "savefig"), patch.object(plots.config.PLOTS_DIR.__class__, "mkdir"):
            plots.save_web_plot_png({"name": "Example", "uid": "42"}, "all", "Views", "Views", points, axis_mode="number")
        self.assertEqual(list(axis.lines[0].get_xdata()), [1, 2, 3])
        self.assertEqual(list(axis.lines[0].get_ydata()), [10, 20, 30])
        self.assertIn("Video number", axis.get_xlabel())

    def test_moving_averages_have_full_trailing_windows(self):
        self.assertEqual(plots.moving_average([1, 2, 3, 4, 5, 6], 5), [None, None, None, None, 3, 4])
        self.assertEqual(plots.moving_average([0] * 6, 5), [None] * 4 + [0, 0])
        self.assertEqual(plots.moving_average([9, 2], 5), [None, None])
        self.assertEqual(plots.moving_average([], 5), [])
        self.assertEqual(plots.moving_average([100, 0, 0, 0, 0, 0], 5), [None] * 4 + [20, 0])
        for period in (5, 10, 20):
            result = plots.moving_average(list(range(1, 31)), period)
            self.assertEqual(result[period - 1], (period + 1) / 2)
            self.assertEqual(result[-1], (61 - period) / 2)
        for invalid in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                plots.moving_average([1, 2], invalid)

    def test_moving_average_export_and_window_validation(self):
        self.assertEqual(plots.normalize_ma_periods([10, 5, 5]), (5, 10))
        for invalid in ([True], [7], ["5"], "5", {}):
            with self.assertRaises(ValueError):
                plots.normalize_ma_periods(invalid)
        figure, axis = plotting.plt.subplots()
        self.addCleanup(plotting.plt.close, figure)
        points = [{"label": f"2026-09-{index:02d} 12:00:00", "value": index} for index in range(10, 0, -1)]
        with patch.object(plotting.plt, "subplots", return_value=(figure, axis)), \
                patch.object(figure, "savefig"), patch.object(plots.config.PLOTS_DIR.__class__, "mkdir"):
            plots.save_web_plot_png({"name": "Example", "uid": "42"}, "all", "Views", "Views", points,
                                   axis_mode="number", ma_periods=[5, 10, 20])
        self.assertEqual([line.get_label() for line in axis.lines], ["Views", "MA5", "MA10"])
        self.assertEqual(list(axis.lines[1].get_ydata()), [None] * 4 + [3, 4, 5, 6, 7, 8])
        self.assertEqual(list(axis.lines[2].get_ydata()), [None] * 9 + [5.5])

    def test_additional_indicators_export_to_separate_panel(self):
        figure, (axis, relative) = plotting.plt.subplots(2, 1, sharex=True)
        self.addCleanup(plotting.plt.close, figure)
        points = [{"label": f"2026-09-{index + 1:02d} 12:00:00", "value": value}
                  for index, value in enumerate([10] * 20 + [30])]
        with patch.object(plotting.plt, "subplots", return_value=(figure, (axis, relative))), \
                patch.object(figure, "savefig"), patch.object(plots.config.PLOTS_DIR.__class__, "mkdir"):
            plots.save_web_plot_png({"name": "Example", "uid": "42"}, "all", "Views", "Views", points,
                                   axis_mode="number", indicators=["ema10", "ema20", "median5", "median10", "relative20"])
        lines = {line.get_label(): line for line in axis.lines}
        self.assertEqual(set(lines), {"Views", "EMA10", "EMA20", "Median5", "Median10"})
        self.assertAlmostEqual(lines["EMA10"].get_ydata()[-1], 10 + 20 * 2 / 11)
        self.assertEqual(lines["Median10"].get_ydata()[-1], 10)
        self.assertEqual(list(relative.lines[0].get_ydata()), [None] * 20 + [3])
        self.assertEqual(len(figure.axes), 2)
        self.assertTrue(axis.get_shared_x_axes().joined(axis, relative))
        self.assertEqual(plots.normalize_indicators(["ema20", "ema10", "ema10"]), ("ema10", "ema20"))
        for invalid in ([True], [5], ["ema999"], "ema10", {}):
            with self.assertRaises(ValueError):
                plots.normalize_indicators(invalid)
