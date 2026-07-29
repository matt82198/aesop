#!/usr/bin/env python3
"""Verification policy mapping for AgentDriver backends.

Pure function: given a DriverCapabilities (with recommended_verification_tier),
return a dict of verification tuning parameters that the wave's integration
verifier can use to decide how aggressively to spot-check and repair.

This encodes spike-multitool-portability.md Section 4.3: the tier->policy
mapping that tells the template what verification bars each backend owes.

No I/O, no imports beyond agent_driver.
"""

from agent_driver import DriverCapabilities


def verification_policy(caps: DriverCapabilities) -> dict:
    """Map a backend's verification tier to orchestrator verification tuning.

    Returns a dict with the following keys (all required):
      validate_all_json: bool -- if True, validate every worker's JSON output
          (Tier 2+); if False, trust it (Tier 1).
      spot_check_frac: float in [0.0, 1.0] -- fraction of results to spot-check.
      repair_cap: int -- maximum repair attempts per worker.
      require_adversarial_review: bool -- if True, require refutation-style
          review (reason about code vs contract, not just re-run tests).
      adversarial_review_sample_frac: float in [0.0, 1.0] -- fraction of
          verified items to send for adversarial review (applies when enabled).

    The tier is a proxy for tool_use_accuracy and determines the orchestrator's
    burden. Weaker backends (lower accuracy) require higher tiers + heavier
    verification.

    Args:
        caps: DriverCapabilities from probe_capabilities().

    Returns:
        dict with verification tuning.

    Raises:
        ValueError: if tier is not in [1, 2, 3, 4].
    """
    tier = caps.recommended_verification_tier

    # Map tier -> policy per spike Section 4.3.
    if tier == 1:
        return {
            "validate_all_json": False,
            "spot_check_frac": 0.10,
            "repair_cap": 1,
            "require_adversarial_review": False,
            "adversarial_review_sample_frac": 0.0,
        }
    elif tier == 2:
        return {
            "validate_all_json": True,
            "spot_check_frac": 0.50,
            "repair_cap": 2,
            "require_adversarial_review": True,
            "adversarial_review_sample_frac": 0.10,
        }
    elif tier == 3:
        return {
            "validate_all_json": True,
            "spot_check_frac": 1.00,
            "repair_cap": 2,
            "require_adversarial_review": True,
            "adversarial_review_sample_frac": 0.20,
        }
    elif tier == 4:
        return {
            "validate_all_json": True,
            "spot_check_frac": 1.00,
            "repair_cap": 3,
            "require_adversarial_review": True,
            "adversarial_review_sample_frac": 0.30,
        }
    else:
        raise ValueError(
            f"Unknown verification tier {tier}; must be in [1, 2, 3, 4]"
        )
