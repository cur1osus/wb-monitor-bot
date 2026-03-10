from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    pass


class UserPlan(StringEnum):
    FREE = "free"
    PRO = "pro"
    PRO_PLUS = "pro_plus"


class PlanOfferCode(StringEnum):
    PRO = "pro"
    PRO_PLUS = "proplus"


class FeatureName(StringEnum):
    CHEAP = "cheap"
    COMPARE = "compare"
    REVIEWS = "reviews"


class FeaturePeriod(StringEnum):
    DAY = "day"
    MONTH = "month"


class SearchMode(StringEnum):
    CHEAP = "cheap"
    SIMILAR = "similar"


class CompareMode(StringEnum):
    BALANCED = "balanced"
    CHEAP = "cheap"
    GIFT = "gift"
    QUALITY = "quality"
    SAFE = "safe"
