"""Converts between different area units."""

# Conversion factors
# 1 square mile = 2.58999 square kilometers = 2,589,990 square meters
SQUARE_MILES_TO_SQ_METERS = 2_589_988

# Linear conversion from miles to meters (for distance calculations)
MILES_TO_METERS_FACTOR = 1.609 * 1_000_000


def convert_to_square_meters(area_square_miles):
    """
    Convert area from square miles to square meters.

    Args:
        area_square_miles: Area in square miles

    Returns:
        Area in square meters
    """
    return area_square_miles * MILES_TO_METERS_FACTOR
