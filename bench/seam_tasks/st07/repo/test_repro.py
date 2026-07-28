"""Visible reproduction test for area conversion."""
import pytest

from plot_processor import process_plot


class TestAreaConversionRepro:
    """Visible test: square mile to square meter conversion is accurate."""

    def test_unit_conversion_accuracy(self):
        """1 square mile should convert to approximately 2,589,988 square meters."""
        result = process_plot(1, 1)
        area_sq_meters = result["area_sq_meters"]

        # 1 square mile is approximately 2,589,988 square meters
        expected = 2_589_988
        tolerance = expected * 0.01  # 1% tolerance

        # The area must be in the correct range (not off by a linear factor)
        assert abs(area_sq_meters - expected) < tolerance, \
            f"Expected ~{expected} sq m, got {area_sq_meters}"
