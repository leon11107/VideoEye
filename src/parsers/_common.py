"""Shared helpers for the H.264 and H.265 NAL unit parsers."""

from ..utils.bitstream_reader import BitstreamReader


def read_sei_payload_header(reader: BitstreamReader) -> tuple[int, int]:
    """Read an SEI message's payloadType and payloadSize.

    Both are coded as a run of 0xFF bytes terminated by a non-0xFF byte, the
    sum of which is the value (H.264 7.3.2.3.1 / H.265 7.3.5). Identical in
    both codecs, so shared here.
    """
    payload_type = 0
    while True:
        byte = reader.read_u(8)
        payload_type += byte
        if byte != 255:
            break

    payload_size = 0
    while True:
        byte = reader.read_u(8)
        payload_size += byte
        if byte != 255:
            break

    return payload_type, payload_size


def more_rbsp_data(reader: BitstreamReader) -> bool:
    """more_rbsp_data(): true if data remains before the rbsp trailing bits
    (the final rbsp_stop_one_bit + zero padding). Identical for H.264/H.265."""
    if reader.bits_remaining() <= 0:
        return False
    data, n = reader.data, reader._length
    last = -1
    for i in range(n - 1, -1, -1):
        if data[i]:
            last = i
            break
    if last < 0:
        return False
    b = data[last]
    stop_bit = next(7 - j for j in range(8) if (b >> j) & 1)
    stop_pos = last * 8 + stop_bit
    return reader.byte_offset * 8 + reader.bit_offset < stop_pos
