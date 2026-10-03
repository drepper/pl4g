※ symbolic assembler dump; internal form, not a syntax

section .data writable
counter:
                             ※ align 1
    01                       ※ data
    00 00 00 00 00 00 00     ※ align 8

section .text executable
main(ptr<Init>)u6:
    93 02 a0 02              li t0, 42
    17 03 00 00              auipc.hi20 t1, counter   ※ fixup riscv_pcrel_hi20 → counter
    13 03 03 00              addi.lo12 t1, t1, counter   ※ fixup riscv_pcrel_lo12_i → counter
    23 00 53 00              sb t0, t1, 0
    97 02 00 00              auipc.hi20 t0, counter   ※ fixup riscv_pcrel_hi20 → counter
    93 82 02 00              addi.lo12 t0, t0, counter   ※ fixup riscv_pcrel_lo12_i → counter
    03 c5 02 00              lbu a0, t0, 0
    67 80 00 00              ret
