"""Bitstream reader utility for parsing H.264/H.265 NAL units."""

from typing import Optional


class BitstreamReader:
    """Reads bits from a byte array with Exp-Golomb support."""

    def __init__(self, data: bytes):
        """Initialize with raw byte data."""
        self.data = data
        self.byte_offset = 0
        self.bit_offset = 0
        self._length = len(data)

    @classmethod
    def from_rbsp(cls, nalu_data: bytes) -> 'BitstreamReader':
        """Create reader from NAL unit data with emulation prevention bytes removed."""
        rbsp = cls.remove_emulation_prevention(nalu_data)
        return cls(rbsp)

    @staticmethod
    def remove_emulation_prevention(data: bytes) -> bytes:
        """Remove emulation prevention bytes (0x03 after 0x0000)."""
        result = bytearray()
        i = 0
        while i < len(data):
            if i + 2 < len(data) and data[i] == 0 and data[i+1] == 0 and data[i+2] == 3:
                result.append(data[i])
                result.append(data[i+1])
                i += 3  # Skip the 0x03
            else:
                result.append(data[i])
                i += 1
        return bytes(result)

    def bits_remaining(self) -> int:
        """Return number of bits remaining."""
        return (self._length - self.byte_offset) * 8 - self.bit_offset

    def byte_aligned(self) -> bool:
        """Check if currently byte-aligned."""
        return self.bit_offset == 0

    def read_bit(self) -> int:
        """Read a single bit."""
        if self.byte_offset >= self._length:
            raise EOFError("End of bitstream")

        bit = (self.data[self.byte_offset] >> (7 - self.bit_offset)) & 1
        self.bit_offset += 1
        if self.bit_offset == 8:
            self.bit_offset = 0
            self.byte_offset += 1
        return bit

    def read_bits(self, n: int) -> int:
        """Read n bits as unsigned integer."""
        if n == 0:
            return 0
        if n > 32:
            raise ValueError("Cannot read more than 32 bits at once")

        result = 0
        for _ in range(n):
            result = (result << 1) | self.read_bit()
        return result

    def read_u(self, n: int) -> int:
        """Read n-bit unsigned integer u(n)."""
        return self.read_bits(n)

    def read_ue(self) -> int:
        """Read unsigned Exp-Golomb coded value ue(v)."""
        leading_zeros = 0
        while self.read_bit() == 0:
            leading_zeros += 1
            if leading_zeros > 32:
                raise ValueError("Invalid Exp-Golomb code")

        if leading_zeros == 0:
            return 0

        value = self.read_bits(leading_zeros)
        return (1 << leading_zeros) - 1 + value

    def read_se(self) -> int:
        """Read signed Exp-Golomb coded value se(v)."""
        code_num = self.read_ue()
        if code_num == 0:
            return 0
        sign = 1 if (code_num & 1) else -1
        return sign * ((code_num + 1) >> 1)

    def read_flag(self) -> bool:
        """Read a single bit as boolean flag."""
        return self.read_bit() == 1

    def skip_bits(self, n: int) -> None:
        """Skip n bits."""
        for _ in range(n):
            self.read_bit()

    def align_to_byte(self) -> None:
        """Align to next byte boundary."""
        if self.bit_offset != 0:
            self.bit_offset = 0
            self.byte_offset += 1

    def peek_bits(self, n: int) -> int:
        """Peek at next n bits without advancing."""
        saved_byte = self.byte_offset
        saved_bit = self.bit_offset
        value = self.read_bits(n)
        self.byte_offset = saved_byte
        self.bit_offset = saved_bit
        return value

    def get_position(self) -> tuple[int, int]:
        """Get current position as (byte_offset, bit_offset)."""
        return (self.byte_offset, self.bit_offset)

    def set_position(self, byte_offset: int, bit_offset: int = 0) -> None:
        """Set current position."""
        self.byte_offset = byte_offset
        self.bit_offset = bit_offset
