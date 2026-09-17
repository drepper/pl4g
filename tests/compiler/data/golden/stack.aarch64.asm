※ symbolic assembler dump; internal form, not a syntax

section .data writable
__pl4g_stack_state:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ※ data

section .rodata
.Lstack.message:
    70 6c 34 67 3a 20 74 68 65 20 73 74 61 63 6b 20 72 61 6e 20 6f 75 74 0a ※ data

section .text executable
main()u6:
                             ※ align 16
    00 00 80 52              movz w0, 0
    c0 03 5f d6              ret
    00 00 00 00 00 00 00 00  ※ align 16
__pl4g_stack_fault:
    29 08 40 f9              ldr x9, x1, 16
    0a 00 00 90              adrp x10, __pl4g_stack_state   ※ fixup aarch64_adr_page21 → __pl4g_stack_state
    4a 01 00 91              add.lo12 x10, x10, __pl4g_stack_state   ※ fixup aarch64_add_lo12 → __pl4g_stack_state
    4b 01 40 f9              ldr x11, x10, 0
    3f 01 0b eb              cmp x9, x11
    03 00 00 54              b.lo .L__pl4g_stack_fault.not.the.guard.1   ※ fixup aarch64_branch19 → .L__pl4g_stack_fault.not.the.guard.1
    4b 05 40 f9              ldr x11, x10, 8
    3f 01 0b eb              cmp x9, x11
    02 00 00 54              b.hs .L__pl4g_stack_fault.not.the.guard.1   ※ fixup aarch64_branch19 → .L__pl4g_stack_fault.not.the.guard.1
    40 00 80 d2              movz x0, 2
    01 00 00 90              adrp x1, .Lstack.message   ※ fixup aarch64_adr_page21 → .Lstack.message
    21 00 00 91              add.lo12 x1, x1, .Lstack.message   ※ fixup aarch64_add_lo12 → .Lstack.message
    02 03 80 d2              movz x2, 24
    08 08 80 d2              movz x8, 64
    01 00 00 d4              svc 0
    60 08 80 d2              movz x0, 67
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    01 00 00 00              udf 1
.L__pl4g_stack_fault.not.the.guard.1:
    e1 03 0a aa              mov x1, x10
    00 06 80 d2              movz x0, 48
    21 00 00 8b              add x1, x1, x0
    60 01 80 d2              movz x0, 11
    02 00 80 d2              movz x2, 0
    03 01 80 d2              movz x3, 8
    c8 10 80 d2              movz x8, 134
    01 00 00 d4              svc 0
    c0 03 5f d6              ret
_start:
                             ※ align 16
    fd 03 1f aa              mov x29, xzr
    fe 03 1f aa              mov x30, xzr
    f6 03 00 91              add x22, sp, 0
    17 00 88 d2              movz x23, 16384, 0
    37 06 a0 f2              movk x23, 49, 16
    d6 02 17 cb              sub x22, x22, x23
    f7 ff 9f d2              movz x23, 65535, 0
    f7 03 a0 f2              movk x23, 31, 16
    f7 03 37 aa              mvn x23, x23
    d6 02 17 8a              and x22, x22, x23
    e0 03 16 aa              mov x0, x22
    01 00 88 d2              movz x1, 16384, 0
    21 02 a0 f2              movk x1, 17, 16
    62 00 80 d2              movz x2, 3
    43 04 88 d2              movz x3, 16418
    04 00 80 d2              movz x4, 0
    e4 03 24 aa              mvn x4, x4
    05 00 80 d2              movz x5, 0
    c8 1b 80 d2              movz x8, 222
    01 00 00 d4              svc 0
    f6 03 00 aa              mov x22, x0
    17 00 82 d2              movz x23, 4096
    f7 03 37 aa              mvn x23, x23
    df 02 17 eb              cmp x22, x23
    08 00 00 54              b.hi .L_start.stack.as.it.was.2   ※ fixup aarch64_branch19 → .L_start.stack.as.it.was.2
    e0 03 16 aa              mov x0, x22
    21 00 a0 d2              movz x1, 1, 16
    02 00 80 d2              movz x2, 0
    48 1c 80 d2              movz x8, 226
    01 00 00 d4              svc 0
    17 00 80 d2              movz x23, 0
    1f 00 17 eb              cmp x0, x23
    00 00 00 54              b.eq .L_start.stack.guarded.3   ※ fixup aarch64_branch19 → .L_start.stack.guarded.3
    e0 03 16 aa              mov x0, x22
    01 00 88 d2              movz x1, 16384, 0
    21 02 a0 f2              movk x1, 17, 16
    e8 1a 80 d2              movz x8, 215
    01 00 00 d4              svc 0
    00 00 00 14              b .L_start.stack.as.it.was.2   ※ fixup aarch64_branch26 → .L_start.stack.as.it.was.2
.L_start.stack.guarded.3:
    17 00 00 90              adrp x23, __pl4g_stack_state   ※ fixup aarch64_adr_page21 → __pl4g_stack_state
    f7 02 00 91              add.lo12 x23, x23, __pl4g_stack_state   ※ fixup aarch64_add_lo12 → __pl4g_stack_state
    f6 02 00 f9              str x22, x23, 0
    38 00 a0 d2              movz x24, 1, 16
    18 03 16 8b              add x24, x24, x22
    f8 06 00 f9              str x24, x23, 8
    16 02 a0 d2              movz x22, 16, 16
    18 03 16 8b              add x24, x24, x22
    f8 2a 00 f9              str x24, x23, 80
    16 00 80 d2              movz x22, 0
    f6 2e 00 f9              str x22, x23, 88
    16 00 88 d2              movz x22, 16384
    f6 32 00 f9              str x22, x23, 96
    e0 03 17 aa              mov x0, x23
    01 0a 80 d2              movz x1, 80
    00 00 01 8b              add x0, x0, x1
    01 00 80 d2              movz x1, 0
    88 10 80 d2              movz x8, 132
    01 00 00 d4              svc 0
    16 00 00 90              adrp x22, __pl4g_stack_fault   ※ fixup aarch64_adr_page21 → __pl4g_stack_fault
    d6 02 00 91              add.lo12 x22, x22, __pl4g_stack_fault   ※ fixup aarch64_add_lo12 → __pl4g_stack_fault
    f6 0a 00 f9              str x22, x23, 16
    96 00 80 d2              movz x22, 4, 0
    16 00 a1 f2              movk x22, 2048, 16
    f6 0e 00 f9              str x22, x23, 24
    e1 03 17 aa              mov x1, x23
    00 02 80 d2              movz x0, 16
    21 00 00 8b              add x1, x1, x0
    60 01 80 d2              movz x0, 11
    02 00 80 d2              movz x2, 0
    03 01 80 d2              movz x3, 8
    c8 10 80 d2              movz x8, 134
    01 00 00 d4              svc 0
    1f 03 00 91              add sp, x24, 0
.L_start.stack.as.it.was.2:
    00 00 00 94              bl main()u6   ※ fixup aarch64_branch26 → main()u6
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    01 00 00 00              udf 1
