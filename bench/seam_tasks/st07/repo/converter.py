"""Converts between different area units."""

# Conversion factors
# 1 square mile = 2.58999 square kilometers = 2,589,990 square meters
SQUARE_MILES_TO_SQ_METERS = 2_589_988

# DEFECT: Using linear conversion factor instead of square conversion
# 1.609 is for linear miles->km, but for area (square miles -> sq km), we need 1.609^2 = 2.589
DEFECTIVE_FACTOR = 1.609 * 1_000_000  # This gives 1.609M, not 2.59M


def convert_to_square_meters(area_square_miles):
    """
    Convert area from square miles to square meters.

    Args:
        area_square_miles: Area in square miles

    Returns:
        Area in square meters
    """
    # DEFECT: Uses the incorrect factor (linear 1.609 scaled to millions)
    # instead of the correct square conversion factor (2.589 million)
    return area_square_miles * DEFECTIVE_FACTOR
