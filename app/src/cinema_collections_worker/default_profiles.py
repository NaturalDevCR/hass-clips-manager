"""Built-in processing profiles."""

from .profile_validation import ProcessingProfile


def compatibility_4k_loudness_profile() -> ProcessingProfile:
    """Return the editable Compatibility 4K Loudness baseline."""
    return ProcessingProfile()
