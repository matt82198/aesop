"""Main entry point for the plot processor."""

from plot_processor import process_plot


def main():
    """Process various plot sizes."""
    plot_10x10 = process_plot(10, 10)
    plot_5x5 = process_plot(5, 5)
    plot_1x1 = process_plot(1, 1)

    return {
        "plot_10x10": plot_10x10,
        "plot_5x5": plot_5x5,
        "plot_1x1": plot_1x1,
    }
