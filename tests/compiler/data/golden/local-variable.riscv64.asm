※ symbolic assembler dump; internal form, not a syntax

section .text executable
main()u8:
                             ※ align 16
    13 05 70 00              li a0, 7
    67 80 00 00              ret
    00 00 00 00 00 00 00 00  ※ align 16
_start:
    13 04 00 00              mv s0, zero
    93 00 00 00              mv ra, zero
    ef 00 00 00              jal main()u8   ※ fixup riscv_jal → main()u8
    93 08 e0 05              li a7, 94
    73 00 00 00              ecall
    73 10 00 c0              unimp
