※ symbolic assembler dump; internal form, not a syntax

section .rodata
counter:
                             ※ align 1
    2a                       ※ data

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    97 02 00 00              auipc.hi20 t0, counter   ※ fixup riscv_pcrel_hi20 → counter
    93 82 02 00              addi.lo12 t0, t0, counter   ※ fixup riscv_pcrel_lo12_i → counter
    03 c5 02 00              lbu a0, t0, 0
    67 80 00 00              ret
