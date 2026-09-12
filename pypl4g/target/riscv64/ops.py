"""Operations that only RISC-V has."""

from typing import Final

from ...mc.ops import Op, register

#: Enters the kernel.  The call number is in a7 and the arguments in a0 onwards.
ENVIRONMENT_CALL: Final[Op] = register(Op("riscv.ecall", 0, has_result=False))
