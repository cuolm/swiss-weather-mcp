"""The error for a question the data cannot answer."""


class CannotAnswerError(Exception):
    """
    A question the data cannot answer.

    For example an unknown place, a time that is not a full hour or a period outside the forecast.
    The message says why.
    """
