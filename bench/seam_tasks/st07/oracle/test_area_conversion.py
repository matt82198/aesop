"""Test suite for area unit conversions."""

import sys
import os

# Add repo to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'repo'))

from plot_processor import process_plot


class TestAreaConversion:
    """Tests for correct square mile to square meter conversions."""

    def test_1x1_plot_conversion(self):
        """1 square mile should convert to ~2,589,988 square meters."""
        result = process_plot(1, 1)
        area_sq_meters = result["area_sq_meters"]

        # Allow 0.1% tolerance for floating point
        expected = 2_589_988
        tolerance = expected * 0.001
        assert abs(area_sq_meters - expected) < tolerance, \
            f"Expected {expected} sq m, got {area_sq_meters}"

    def test_10x10_plot_conversion(self):
        """100 square miles should convert to ~258,998,800 square meters."""
        result = process_plot(10, 10)
        area_sq_miles = result["area_sq_miles"]
        area_sq_meters = result["area_sq_meters"]

        assert area_sq_miles == 100, f"Expected 100 sq miles, got {area_sq_miles}"

        expected = 258_998_800
        tolerance = expected * 0.001
        assert abs(area_sq_meters - expected) < tolerance, \
            f"Expected {expected} sq m, got {area_sq_meters}"

    def test_5x5_plot_conversion(self):
        """25 square miles should convert to ~64,749,700 square meters."""
        result = process_plot(5, 5)
        area_sq_miles = result["area_sq_miles"]
        area_sq_meters = result["area_sq_meters"]

        assert area_sq_miles == 25, f"Expected 25 sq miles, got {area_sq_miles}"

        expected = 25 * 2_589_988
        tolerance = expected * 0.001
        assert abs(area_sq_meters - expected) < tolerance, \
            f"Expected {expected} sq m, got {area_sq_meters}"

    def test_conversion_factor_correctness(self):
        """Conversion factor should be approximately 2.59 million."""
        result = process_plot(1, 1)
        area_sq_meters = result["area_sq_meters"]

        # Should be ~2.59 million, not ~1.61 million
        assert area_sq_meters > 2_000_000, \
            f"Conversion factor too small: {area_sq_meters} sq m per sq mile"
        assert area_sq_meters < 3_000_000, \
            f"Conversion factor too large: {area_sq_meters} sq m per sq mile"

    def test_2x3_plot_conversion(self):
        """6 square miles should convert to ~15,539,928 square meters."""
        result = process_plot(2, 3)
        area_sq_miles = result["area_sq_miles"]
        area_sq_meters = result["area_sq_meters"]

        assert area_sq_miles == 6, f"Expected 6 sq miles, got {area_sq_miles}"

        expected = 6 * 2_589_988
        tolerance = expected * 0.001
        assert abs(area_sq_meters - expected) < tolerance, \
            f"Expected {expected} sq m, got {area_sq_meters}"
