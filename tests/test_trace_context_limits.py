"""Exercise the shipped parser's W3C context and oversized-header boundary."""

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_valid_tracestate_preserves_remote_parent():
    context = TraceContextTextMapPropagator().extract(
        {"traceparent": TRACEPARENT, "tracestate": "vendor=value"}
    )
    parent = trace.get_current_span(context).get_span_context()
    assert parent.is_remote
    assert parent.trace_id == int("0af7651916cd43dd8448eb211c80319c", 16)
    assert dict(parent.trace_state) == {"vendor": "value"}


def test_oversized_tracestate_is_dropped_without_losing_parent():
    # All 32 members are individually valid. The combined header exceeds the
    # parser's 8 KiB admission limit, so rejection must precede member parsing.
    header = ",".join(f"k{i}={'v' * 256}" for i in range(32))
    assert len(header) > 8192
    context = TraceContextTextMapPropagator().extract(
        {"traceparent": TRACEPARENT, "tracestate": header}
    )
    parent = trace.get_current_span(context).get_span_context()
    assert parent.is_remote
    assert parent.span_id == int("b7ad6b7169203331", 16)
    assert not parent.trace_state
