"""Sample test fixture for seam-discrimination study."""


def add(x, y):
    """Add two numbers. Currently broken: returns product instead of sum."""
    return x * y


def test_add_basic():
    """Test basic addition."""
    assert add(2, 3) == 5


def test_add_zeros():
    """Test addition with zeros."""
    assert add(0, 0) == 0
    assert add(1, 0) == 1


def test_add_negative():
    """Test addition with negative numbers."""
    assert add(-1, 1) == 0
    assert add(-5, -3) == -8
