"""Server-side enforcement of the recording rules (allowlist + no secrets)."""

from ghostflow.recording.models import RecordedEvent, RecordingCreate
from ghostflow.security import UrlNotAllowedError, UrlPolicy, is_sensitive_field, looks_like_secret, redact_url


class RecordingRejectedError(ValueError):
    pass


def _event_is_sensitive(event: RecordedEvent) -> bool:
    if event.sensitive:
        return True
    t = event.target
    if t is not None and is_sensitive_field(
        input_type=t.input_type,
        autocomplete=t.autocomplete,
        names=(t.label, t.accessible_name, t.element_id, t.name_attr, t.placeholder, t.test_id),
    ):
        return True
    return looks_like_secret(event.value)


def sanitize_recording(recording: RecordingCreate, policy: UrlPolicy) -> RecordingCreate:
    """Reject events outside the allowlist; strip sensitive values; redact URLs."""
    clean: list[RecordedEvent] = []
    for event in sorted(recording.events, key=lambda e: e.seq):
        try:
            policy.check(event.url)
        except UrlNotAllowedError as exc:
            raise RecordingRejectedError(f"Event {event.seq}: {exc}") from None
        update: dict = {"url": redact_url(event.url)}
        if event.type in {"input", "select"} and _event_is_sensitive(event):
            update |= {"value": None, "sensitive": True}
        if event.type == "observation" and looks_like_secret(event.text):
            continue
        clean.append(event.model_copy(update=update))
    return recording.model_copy(update={"events": clean})
