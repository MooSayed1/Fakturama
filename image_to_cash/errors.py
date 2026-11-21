"""The one exception the whole program uses to stop safely."""


class ManualReview(Exception):
    """Something is ambiguous, wrong or unexpected: stop and let a human look.

    The message says why. The run's main loop catches it, takes a screenshot,
    logs the reason and exits with a non-zero code. Nothing ever guesses.
    """
