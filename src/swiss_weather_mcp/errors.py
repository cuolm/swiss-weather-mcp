class CannotAnswerError(Exception):
    """
    A question the MeteoSwiss data cannot answer, such as an unknown place, a time that is not a
    full hour or a period outside the forecast. The message says why, and the server shows it to
    the model so it can ask again.
    """
