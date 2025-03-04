class PPPPError(Exception):
    """Base class for all exceptions."""


class NotConnectedError(PPPPError):
    """Error when the camera is not connected."""


class AlreadyConnectedError(PPPPError):
    """Error when the camera is already connected."""


class AuthError(PPPPError):
    """Error when the camera user or password are invalid."""


class CommandResultError(PPPPError):
    """Error when a command returns an error."""
