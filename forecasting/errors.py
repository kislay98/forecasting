"""Typed errors. Every refusal names the rule or key that caused it."""

from __future__ import annotations


class ForecastingError(Exception):
    """Base class for all errors raised by this package."""


class ConfigError(ForecastingError):
    """The run config is invalid. `key` is the dotted path of the offending key."""

    def __init__(self, key: str, detail: str) -> None:
        self.key = key
        self.detail = detail
        super().__init__(f"config key '{key}': {detail}")


class AdapterError(ForecastingError):
    """A source could not be read into the canonical table."""


class DataValidationError(ForecastingError):
    """The data breaks a rule in 'Dataset requirements'. Subclasses name the rule."""

    rule: str = "data"

    def __init__(self, detail: str, unique_id: str | None = None) -> None:
        self.detail = detail
        self.unique_id = unique_id
        where = f" [{unique_id}]" if unique_id else ""
        super().__init__(f"{self.rule}{where}: {detail}")


class MissingColumnError(DataValidationError):
    rule = "schema"


class TimestampError(DataValidationError):
    """Unparseable or timezone-aware timestamps."""

    rule = "timestamp"


class NonNumericTargetError(DataValidationError):
    rule = "non_numeric_y"


class NonFiniteTargetError(DataValidationError):
    rule = "non_finite_y"


class DuplicateTimestampError(DataValidationError):
    rule = "duplicates"


class IrregularFrequencyError(DataValidationError):
    rule = "irregular_frequency"


class TradingCalendarError(DataValidationError):
    """A trading_days series has a row on a weekend."""

    rule = "trading_calendar"


class LongGapError(DataValidationError):
    rule = "long_gap"


class TooManyMissingError(DataValidationError):
    rule = "missing_share"


class TooManyZerosError(DataValidationError):
    rule = "zeros"


class NegativeValueError(DataValidationError):
    rule = "negatives"


class NonPositivePriceError(DataValidationError):
    """target: returns needs strictly positive levels for log returns."""

    rule = "non_positive_for_returns"


class TooShortError(DataValidationError):
    rule = "too_short"


class FitError(ForecastingError):
    """A model could not be fitted or could not forecast. Recorded as a failed row."""

    def __init__(self, model: str, reason: str) -> None:
        self.model = model
        self.reason = reason
        super().__init__(f"{model}: {reason}")


class ForecastContractError(ForecastingError):
    """A model returned output that breaks the Forecaster contract (NaN, disordered bounds)."""


class TransformError(ForecastingError):
    """A transform cannot be fitted on this slice (e.g. log of y <= 0)."""


class SchemaError(ForecastingError):
    """Rows do not match the forecast store schema."""


class NotFittedError(RuntimeError):
    """predict or residuals called before fit. A programming error, so not a ForecastingError:
    the engine lets it propagate instead of recording it as a model failure."""
