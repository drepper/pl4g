※ symbolic assembler dump; internal form, not a syntax

section .data writable
__pl4g_stack_state:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 04 00 00 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 40 00 00 00 00 00 00 ※ data

section .rodata
.Lstack.message:
    70 6c 34 67 3a 20 74 68 65 20 73 74 61 63 6b 20 72 61 6e 20 6f 75 74 0a ※ data

section .text executable
main()u6:
                             ※ align 16
    13 05 00 00              li a0, 0
    67 80 00 00              ret
.Lpl4g.frames.end:
    00 00 00 00 00 00 00 00  ※ align 16
__pl4g_stack_fault:
    83 b2 05 01              ld t0, a1, 16
    17 03 00 00              auipc.hi20 t1, __pl4g_stack_state   ※ fixup riscv_pcrel_hi20 → __pl4g_stack_state
    13 03 03 00              addi.lo12 t1, t1, __pl4g_stack_state   ※ fixup riscv_pcrel_lo12_i → __pl4g_stack_state
    83 33 03 00              ld t2, t1, 0
    63 e0 72 00              bltu t0, t2, .L__pl4g_stack_fault.not.the.guard.1   ※ fixup riscv_branch → .L__pl4g_stack_fault.not.the.guard.1
    83 33 83 00              ld t2, t1, 8
    63 f0 72 00              bgeu t0, t2, .L__pl4g_stack_fault.not.the.guard.1   ※ fixup riscv_branch → .L__pl4g_stack_fault.not.the.guard.1
    13 05 20 00              li a0, 2
    97 02 00 00              auipc.hi20 t0, .Lstack.message   ※ fixup riscv_pcrel_hi20 → .Lstack.message
    93 82 02 00              addi.lo12 t0, t0, .Lstack.message   ※ fixup riscv_pcrel_lo12_i → .Lstack.message
    93 85 02 00              mv a1, t0
    13 06 80 01              li a2, 24
    93 08 00 04              li a7, 64
    73 00 00 00              ecall
    13 05 30 04              li a0, 67
    93 08 e0 05              li a7, 94
    73 00 00 00              ecall
    73 10 00 c0              unimp
.L__pl4g_stack_fault.not.the.guard.1:
    93 02 00 03              li t0, 48
    b3 05 53 00              add a1, t1, t0
    13 05 b0 00              li a0, 11
    13 06 00 00              li a2, 0
    93 06 80 00              li a3, 8
    93 08 60 08              li a7, 134
    73 00 00 00              ecall
    67 80 00 00              ret
    00 00 00 00 00 00 00 00  ※ align 16
_start:
    13 04 00 00              mv s0, zero
    93 00 00 00              mv ra, zero
    13 0a 01 00              mv s4, sp
    b7 4a 31 00              lui s5, 788
    33 0a 5a 41              sub s4, s4, s5
    b7 0a 20 00              lui s5, 512
    9b 8a fa ff              addiw s5, s5, -1
    93 ca fa ff              not s5, s5
    33 7a 5a 01              and s4, s4, s5
    13 05 0a 00              mv a0, s4
    b7 45 11 00              lui a1, 276
    13 06 30 00              li a2, 3
    b7 46 00 00              lui a3, 4
    9b 86 26 02              addiw a3, a3, 34
    13 07 00 00              li a4, 0
    13 47 f7 ff              not a4, a4
    93 07 00 00              li a5, 0
    93 08 e0 0d              li a7, 222
    73 00 00 00              ecall
    13 0a 05 00              mv s4, a0
    b7 1a 00 00              lui s5, 1
    93 ca fa ff              not s5, s5
    63 e0 4a 01              bltu s5, s4, .L_start.stack.as.it.was.2   ※ fixup riscv_branch → .L_start.stack.as.it.was.2
    13 05 0a 00              mv a0, s4
    b7 05 01 00              lui a1, 16
    13 06 00 00              li a2, 0
    93 08 20 0e              li a7, 226
    73 00 00 00              ecall
    63 00 05 00              beq a0, zero, .L_start.stack.guarded.3   ※ fixup riscv_branch → .L_start.stack.guarded.3
    13 05 0a 00              mv a0, s4
    b7 45 11 00              lui a1, 276
    93 08 70 0d              li a7, 215
    73 00 00 00              ecall
    6f 00 00 00              j .L_start.stack.as.it.was.2   ※ fixup riscv_jal → .L_start.stack.as.it.was.2
.L_start.stack.guarded.3:
    97 02 00 00              auipc.hi20 t0, __pl4g_stack_state   ※ fixup riscv_pcrel_hi20 → __pl4g_stack_state
    93 82 02 00              addi.lo12 t0, t0, __pl4g_stack_state   ※ fixup riscv_pcrel_lo12_i → __pl4g_stack_state
    93 8a 02 00              mv s5, t0
    23 b0 4a 01              sd s4, s5, 0
    37 0b 01 00              lui s6, 16
    33 0b 4b 01              add s6, s6, s4
    23 b4 6a 01              sd s6, s5, 8
    37 0a 10 00              lui s4, 256
    33 0b 4b 01              add s6, s6, s4
    23 b8 6a 05              sd s6, s5, 80
    93 02 00 05              li t0, 80
    33 85 5a 00              add a0, s5, t0
    93 05 00 00              li a1, 0
    93 08 40 08              li a7, 132
    73 00 00 00              ecall
    93 02 00 01              li t0, 16
    b3 85 5a 00              add a1, s5, t0
    13 05 b0 00              li a0, 11
    13 06 00 00              li a2, 0
    93 06 80 00              li a3, 8
    93 08 60 08              li a7, 134
    73 00 00 00              ecall
    13 01 0b 00              mv sp, s6
.L_start.stack.as.it.was.2:
    ef 00 00 00              jal main()u6   ※ fixup riscv_jal → main()u6
    93 08 e0 05              li a7, 94
    73 00 00 00              ecall
    73 10 00 c0              unimp
