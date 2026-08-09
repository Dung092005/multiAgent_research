"""Application exception hierarchy."""


class ApplicationError(Exception):
    """Base class for expected application failures."""


class ConfigurationError(ApplicationError):
    pass


class DatabaseError(ApplicationError):
    pass


class InputValidationError(ApplicationError):
    pass


class OrderNotFoundError(DatabaseError):
    pass


class LLMRequestError(ApplicationError):
    pass


class LLMStructuredOutputError(LLMRequestError):
    pass


class PolicyNoMatchError(ApplicationError):
    pass


class EvidenceValidationError(ApplicationError):
    pass


class FinancialValidationError(ApplicationError):
    pass


class OutputValidationError(ApplicationError):
    pass
