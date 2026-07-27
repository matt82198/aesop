"""Processes plot measurements and converts units."""

from area_calculator import calculate_rectangular_area
from converter import convert_to_square_meters


def process_plot(length_miles, width_miles):
    """
    Calculate plot area in square miles and convert to square meters.

    Args:
        length_miles: Plot length in miles
        width_miles: Plot width in miles

    Returns:
        dict with area in both units
    """
    # Calculate area in square miles
    area_sq_miles = calculate_rectangular_area(length_miles, width_miles)

    # Convert to square meters using boundary converter
    area_sq_meters = convert_to_square_meters(area_sq_miles)

    return {
        "area_sq_miles": area_sq_miles,
        "area_sq_meters": area_sq_meters,
    }
