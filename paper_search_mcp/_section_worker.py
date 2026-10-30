"""Private stdin/stdout worker; accepts bounded PDF bytes, never local paths."""
import json
import math
import sys

MAX_INPUT_BYTES = 20 * 1024 * 1024
MEMORY_LIMIT = 512 * 1024 * 1024
STREAM_LIMIT = 8 * 1024 * 1024


def _resource_limits(seconds):
    import resource
    for kind, cap in ((resource.RLIMIT_AS, MEMORY_LIMIT),
                      (resource.RLIMIT_CPU, math.ceil(seconds) + 1)):
        soft, hard = resource.getrlimit(kind)
        cap = min([cap] + [value for value in (soft, hard) if value != resource.RLIM_INFINITY])
        resource.setrlimit(kind, (cap, cap))


def _parser_configuration():
    import pypdf
    import pypdf.filters as filters
    from contextlib import nullcontext
    def no_external_decoder(*args, **kwargs):
        from .sections import SectionExtractionError
        raise SectionExtractionError("External image decoders are not supported for text extraction")
    filters.JBIG2Decode.decode = staticmethod(no_external_decoder)
    if hasattr(pypdf, "apply_configuration"):
        return pypdf.apply_configuration(
            maximum_declared_stream_length=STREAM_LIMIT,
            array_based_stream_maximum_output_length=STREAM_LIMIT,
            jbig2_maximum_output_length=STREAM_LIMIT, lzw_maximum_output_length=STREAM_LIMIT,
            run_length_maximum_output_length=STREAM_LIMIT, zlib_maximum_output_length=STREAM_LIMIT,
            zlib_maximum_recovery_input_length=100_000,
            image_maximum_buffer_size=STREAM_LIMIT, flate_maximum_row_length=STREAM_LIMIT,
            page_tree_maximum_entries=1000, page_tree_maximum_depth=50,
            xform_maximum_invocations_per_extraction=100, jbig2dec_binary=None,
        )
    # Locked installations currently use pypdf 6.9.2. Its legacy settings are
    # safe to lower here because this process has exactly one parser and dies
    # after the request; these changes never affect the MCP process or readers.
    for name in ("MAX_DECLARED_STREAM_LENGTH", "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
                 "JBIG2_MAX_OUTPUT_LENGTH", "LZW_MAX_OUTPUT_LENGTH",
                 "RUN_LENGTH_MAX_OUTPUT_LENGTH", "ZLIB_MAX_OUTPUT_LENGTH"):
        if not hasattr(filters, name):
            raise RuntimeError("Required pypdf resource limit is unavailable")
        setattr(filters, name, STREAM_LIMIT)
    filters.ZLIB_MAX_RECOVERY_INPUT_LENGTH = 100_000
    return nullcontext()


def main():
    try:
        # Bound allocations even while receiving/parsing the request.
        _resource_limits(60)
        header = json.loads(sys.stdin.buffer.readline(4096))
        size = header.pop("size")
        if type(size) is not int or not 0 < size <= MAX_INPUT_BYTES:
            raise ValueError("Invalid input size")
        from .sections import _parse_pdf_sections, validate_limits, SectionExtractionError
        validate_limits(**header)
        _resource_limits(header["timeout_seconds"])
        data = sys.stdin.buffer.read(size + 1)
        if len(data) != size:
            raise ValueError("Invalid input size")
        with _parser_configuration():
            result = _parse_pdf_sections(data, **header)
        response = {"status": "ok", "result": result}
    except TimeoutError:
        response = {"status": "timeout"}
    except MemoryError:
        response = {"status": "error", "message": "The PDF exceeded the parser memory budget"}
    except Exception as exc:
        # Only our fixed, sanitized errors are forwarded. Parser exceptions and
        # process setup errors may include PDF strings, paths or environment.
        from .sections import SectionExtractionError
        message = str(exc) if isinstance(exc, SectionExtractionError) else "The isolated PDF parser could not process this input safely"
        response = {"status": "error", "message": message}
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
