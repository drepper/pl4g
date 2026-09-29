※ symbolic assembler dump; internal form, not a syntax

section .text executable
main()u6:
                             ※ align 16
    13 05 a0 02              li a0, 42
    67 80 00 00              ret
.Lpl4g.frames.end:
    00 00 00 00 00 00 00 00  ※ align 16
_start:
    13 04 00 00              mv s0, zero
    93 00 00 00              mv ra, zero
    ef 00 00 00              jal main()u6   ※ fixup riscv_jal → main()u6
    93 08 e0 05              li a7, 94
    73 00 00 00              ecall
    73 10 00 c0              unimp
