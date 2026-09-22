class AssistantError(RuntimeError):
    """Base error that can be presented to a user."""


class ProviderUnavailableError(AssistantError):
    """An optional external provider is unavailable."""


class ProviderResponseError(AssistantError):
    """An external provider returned an invalid response."""


class ActionExecutionError(AssistantError):
    """A validated action could not be executed."""
