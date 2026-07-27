"""Pagination helper with dict merge precedence bug."""


def get_pagination_params(user_params):
    """
    Merge user-provided pagination parameters with defaults.

    Args:
        user_params: Dictionary with user-provided pagination parameters
                    (e.g., {"page": 2, "page_size": 50}).

    Returns:
        Dictionary with merged pagination parameters where user values
        take precedence over defaults.
    """
    defaults = {"page": 1, "page_size": 10, "sort_by": "created"}

    # BUG: The merge order is backwards - user_params is updated with defaults
    # This overwrites any user-provided values with the defaults
    user_params.update(defaults)
    return user_params


def apply_pagination_defaults(request_params):
    """
    Apply pagination defaults to request parameters.

    Args:
        request_params: Dictionary of request parameters.

    Returns:
        Dictionary with pagination defaults applied.
    """
    defaults = {"limit": 20, "offset": 0}

    # BUG: Wrong merge order - updating request_params with defaults overwrites user input
    request_params.update(defaults)
    return request_params
