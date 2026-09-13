※ symbolic assembler dump; internal form, not a syntax

section .data writable
counter:
                             ※ align 1
    01                       ※ data

section .text executable
main()u8:
                             ※ align 16
    93 02 70 00              li t0, 7
    17 03 00 00              auipc.hi20 t1, counter   ※ fixup riscv_pcrel_hi20 → counter
    13 03 03 00              addi.lo12 t1, t1, counter   ※ fixup riscv_pcrel_lo12_i → counter
    23 00 53 00              sb t0, t1, 0
    17 05 00 00              auipc.hi20 a0, counter   ※ fixup riscv_pcrel_hi20 → counter
    13 05 05 00              addi.lo12 a0, a0, counter   ※ fixup riscv_pcrel_lo12_i → counter
    03 45 05 00              lbu a0, a0, 0
    67 80 00 00              ret
_start:
                             ※ align 16
    13 04 00 00              mv s0, zero
    93 00 00 00              mv ra, zero
    ef 00 00 00              jal main()u8   ※ fixup riscv_jal → main()u8
    93 08 e0 05              li a7, 94
    73 00 00 00              ecall
    73 10 00 c0              unimp
