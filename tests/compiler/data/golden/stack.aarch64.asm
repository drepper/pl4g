※ symbolic assembler dump; internal form, not a syntax

section .data writable
std.ring:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ※ data
__pl4g_init:
                             ※ align 8
    00 00 00 00 01 00 00 00 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ※ data
__pl4g_stack_state:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 04 00 00 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 40 00 00 00 00 00 00 ※ data

section .rodata
.Lstack.message:
    70 6c 34 67 3a 20 74 68 65 20 73 74 61 63 6b 20 72 61 6e 20 6f 75 74 0a ※ data

section .text executable
std.finish()void:
                             ※ align 16
    ff 43 00 d1              sub sp, sp, 16
    fe 03 00 f9              str x30, sp, 0
    00 00 00 90              adrp x0, std.ring   ※ fixup aarch64_adr_page21 → std.ring
    00 00 00 91              add.lo12 x0, x0, std.ring   ※ fixup aarch64_add_lo12 → std.ring
    00 00 00 94              bl pl4g_io_drain   ※ fixup aarch64_branch26 → pl4g_io_drain
    fe 03 40 f9              ldr x30, sp, 0
    ff 43 00 91              add sp, sp, 16
    c0 03 5f d6              ret
main(ptr<Init>)u6:
                             ※ align 16
    00 00 80 52              movz w0, 0
    c0 03 5f d6              ret
.Lpl4g.frames.end:
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
    c0 0a 80 d2              movz x0, 86
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    01 00 00 00              udf 1
.L__pl4g_stack_fault.not.the.guard.1:
    41 c1 00 91              add x1, x10, 48
    60 01 80 d2              movz x0, 11
    02 00 80 d2              movz x2, 0
    03 01 80 d2              movz x3, 8
    c8 10 80 d2              movz x8, 134
    01 00 00 d4              svc 0
    c0 03 5f d6              ret
    00 00 00 00 00 00 00 00  ※ align 16
_start:
    f5 03 00 91              add x21, sp, 0
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
    00 00 00 b4              cbz x0, .L_start.stack.guarded.3   ※ fixup aarch64_branch19 → .L_start.stack.guarded.3
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
    e0 42 01 91              add x0, x23, 80
    01 00 80 d2              movz x1, 0
    88 10 80 d2              movz x8, 132
    01 00 00 d4              svc 0
    e1 42 00 91              add x1, x23, 16
    60 01 80 d2              movz x0, 11
    02 00 80 d2              movz x2, 0
    03 01 80 d2              movz x3, 8
    c8 10 80 d2              movz x8, 134
    01 00 00 d4              svc 0
    1f 03 00 91              add sp, x24, 0
.L_start.stack.as.it.was.2:
    e0 03 15 aa              mov x0, x21
    01 00 00 90              adrp x1, __pl4g_init   ※ fixup aarch64_adr_page21 → __pl4g_init
    21 00 00 91              add.lo12 x1, x1, __pl4g_init   ※ fixup aarch64_add_lo12 → __pl4g_init
    21 40 00 91              add x1, x1, 16
    00 00 00 94              bl pl4g_args   ※ fixup aarch64_branch26 → pl4g_args
    00 00 00 90              adrp x0, __pl4g_init   ※ fixup aarch64_adr_page21 → __pl4g_init
    00 00 00 91              add.lo12 x0, x0, __pl4g_init   ※ fixup aarch64_add_lo12 → __pl4g_init
    00 00 00 94              bl main(ptr<Init>)u6   ※ fixup aarch64_branch26 → main(ptr<Init>)u6
    f3 03 00 aa              mov x19, x0
    00 00 00 94              bl std.finish()void   ※ fixup aarch64_branch26 → std.finish()void
    e0 03 13 aa              mov x0, x19
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    01 00 00 00              udf 1

section .pl4grt.text executable
pl4g_args:
.Lpl4grt.0:
                             ※ align 4
    3f 7c 00 a9 20 04 00 b4 ea 03 00 aa 4b 85 40 f8 cb 03 00 b4 68 05 0b 8b e9 03 01 aa e0 03 1f aa 62 00 80 52 43 04 80 52 04 00 80 92 01 f1 7d d3 c8 1b 80 52 e5 03 1f aa 01 00 00 d4 60 02 f8 b7 e8 03 1f aa 0c 03 80 52 05 00 00 14 08 05 00 91 ae fd 00 a9 1f 01 0b eb 60 01 00 54 0d 01 0c 9b 4e 79 68 f8 ae 01 00 f9 2e ff ff b4 ef 03 1f aa d0 69 6f 38 ef 05 00 91 d0 ff ff 35 ee 05 00 d1 f3 ff ff 17 20 2d 00 a9 c0 03 5f d6 ※ data
pl4g_env:
    20 08 00 b4 08 00 40 f9 e1 05 80 92 0a 00 80 92 08 0c 08 8b 09 41 00 91 28 00 80 92 eb 03 09 aa 6c 85 40 f8 08 09 00 91 21 c0 00 91 4a 05 00 91 8c ff ff b5 2a 06 00 b4 ec 03 08 aa e0 03 1f aa c8 1b 80 52 62 00 80 52 43 04 80 52 04 00 80 92 e5 03 1f aa 01 00 00 d4 00 05 f8 b7 e8 03 1f aa 0b 06 80 52 e1 03 0c aa 0a 00 00 14 0f 01 0b 9b ec 03 1f aa ed 39 00 a9 ad 01 0e 8b 08 05 00 91 ff 35 01 a9 5f 01 08 eb ec 7d 02 a9 e0 03 00 54 2d 79 68 f8 0e 00 80 92 30 00 80 92 ec 03 0e aa ae 01 0e 8b ef 03 10 aa d1 05 40 39 8e 05 00 91 10 06 00 91 51 ff ff 35 ae fd ff b4 f0 03 1f aa b1 69 70 38 3f f6 00 71 e0 00 00 54 ef 05 00 d1 10 06 00 91 8c 05 00 d1 ff 09 00 b1 21 ff ff 54 e3 ff ff 17 0f 01 0b 9b ed 41 00 a9 ad 01 10 8b ad 05 00 91 e2 ff ff 17 e1 03 1f aa e0 03 1f aa c0 03 5f d6 e1 03 1f aa c0 03 5f d6 ※ data
pl4g_io_submit:
    3f 08 00 71 69 00 00 54 a0 02 80 92 c0 03 5f d6 ff 43 02 d1 08 00 40 f9 ea 03 04 aa eb 03 03 aa e9 03 02 2a ed 03 01 2a ec 03 00 aa f3 43 00 f9 48 0a 00 b4 1f 05 00 f1 f1 07 9f 1a 8f 61 01 91 90 61 02 91 2e 00 80 52 52 00 80 52 20 00 80 52 e8 01 40 f9 88 12 00 b4 e6 03 00 2a 80 31 40 f9 c0 13 00 b4 81 35 40 f9 21 12 00 b4 82 39 40 f9 22 12 00 b4 83 3d 40 f9 23 12 00 b4 84 41 40 f9 24 12 00 b4 85 45 40 f9 25 12 00 b4 87 49 40 f9 27 12 00 b4 93 01 40 f9 7f 06 00 f1 a1 06 00 54 1f 05 00 f1 e8 17 9f 1a 1f 04 00 f1 08 15 88 1a 3f 04 00 f1 08 15 88 1a 5f 04 00 f1 08 15 88 1a 7f 04 00 f1 08 15 88 1a 9f 04 00 f1 08 15 88 1a bf 04 00 f1 08 15 88 1a ff 04 00 f1 e0 17 9f 1a 08 01 00 2a 68 04 00 34 80 05 40 f9 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 40 03 f8 b7 88 81 43 a9 08 01 40 b9 00 fc df 88 1f 01 00 6b 20 02 00 54 82 85 44 a9 42 00 40 b9 04 00 00 14 08 05 00 11 1f 00 08 6b 40 01 00 54 03 01 02 0a 24 50 23 8b 83 00 40 f9 7f 1c 00 f1 28 ff ff 54 84 08 80 b9 f2 79 23 f8 04 7a 23 f8 f5 ff ff 17 e8 03 00 2a 81 1d 40 f9 e0 03 1f 2a 28 fc 9f 88 26 f7 07 37 0e 00 80 92 8d 00 00 14 00 e4 00 6f 48 00 80 52 ff 3b 00 f9 88 01 00 f9 00 01 80 52 e1 03 00 91 28 35 80 52 e2 03 1f aa e3 03 1f aa e4 03 1f aa e5 03 1f aa e0 03 00 ad e0 03 01 ad e0 03 02 ad e0 1b 80 3d 01 00 00 d4 20 07 f8 b7 e8 43 40 29 ee 43 40 b9 ef 67 40 b9 e4 03 00 aa e0 03 1f aa e5 03 1f aa 62 00 80 52 c1 09 08 8b ee 11 10 8b 10 e5 7a d3 c8 1b 80 52 23 00 90 52 01 00 00 d4 ef 03 00 aa e0 03 1f aa c8 1b 80 52 e1 03 0e aa 62 00 80 52 23 00 90 52 05 00 a1 52 01 00 00 d4 ee 03 00 aa e0 03 1f aa c8 1b 80 52 e1 03 10 aa 62 00 80 52 23 00 90 52 05 00 a2 52 01 00 00 d4 31 00 80 52 4f f0 ff b7 2e f0 ff b7 00 f0 ff b7 e8 33 40 b9 f0 43 40 b9 e0 0d 08 4e c2 0d 08 4e e1 17 40 fd e3 2b 40 fd e8 01 08 8b ef 01 10 8b f1 03 1f 2a 88 11 00 f9 e8 5b 40 b9 00 10 a1 2e 8f 81 02 a9 ef 67 40 b9 41 10 a3 2e c8 01 08 8b ce 01 0f 8b 80 05 80 3d 88 b9 04 a9 28 00 80 52 81 81 83 3c 88 11 00 a9 69 ff ff 17 31 00 80 52 67 ff ff 17 ee 03 1f aa 0c 00 00 14 4e 00 80 52 0a 00 00 14 6e 00 80 52 08 00 00 14 8e 00 80 52 06 00 00 14 ae 00 80 52 04 00 00 14 ce 00 80 52 02 00 00 14 ee 00 80 52 5f 01 00 f1 28 02 00 52 e8 03 88 1a c8 01 00 37 6a 05 00 b4 08 00 00 90 08 01 00 91 20 7d 40 93 08 49 2d 8b e1 03 0b aa e2 03 0a aa e3 03 1f aa e4 03 1f aa e5 03 1f aa 08 05 40 79 01 00 00 d4 20 00 00 14 92 c5 41 a9 ad 7d 7e d3 00 00 00 90 00 00 00 91 01 00 00 90 0d 68 6d 38 20 00 c0 3d e8 03 1f aa 31 02 40 b9 52 fe df 88 80 19 40 f9 51 02 11 0a 00 18 11 8b 1f fc 02 a9 1f 1c 00 f9 00 00 80 3d 0b 7c 01 a9 4b 06 00 11 09 04 00 b9 89 15 40 f9 0a 18 00 b9 8a 0d 40 f9 0d 00 00 39 0e 10 00 f9 20 00 80 52 31 59 31 b8 e9 03 0f aa ef 03 10 aa 4b fd 9f 88 04 00 00 14 e0 03 1f aa 48 00 80 52 e9 03 10 aa 20 79 2e f8 e8 79 2e f8 f3 43 40 f9 ff 43 02 91 e0 03 0e aa c0 03 5f d6 ※ data
pl4g_io_wait:
    3f 1c 00 f1 69 00 00 54 a0 02 80 92 c0 03 5f d6 e9 03 01 aa ea 03 00 aa 0b 60 01 91 0c 60 02 91 4d 00 80 52 04 00 00 14 e8 03 0e 2a 4e 1d 40 f9 c8 fd 9f 88 68 79 69 f8 1f 05 00 f1 c1 03 00 54 40 05 40 f9 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 60 fd ff b7 48 b9 43 a9 08 01 40 b9 ce fd df 88 1f 01 0e 6b c0 fd ff 54 50 bd 44 a9 10 02 40 b9 04 00 00 14 08 05 00 11 df 01 08 6b e0 fc ff 54 11 01 10 0a f2 51 31 8b 51 02 40 f9 3f 1e 00 f1 28 ff ff 54 52 0a 80 b9 6d 79 31 f8 92 79 31 f8 f5 ff ff 17 1f 09 00 f1 81 00 00 54 80 79 69 f8 7f 79 29 f8 c0 03 5f d6 a0 02 80 92 7f 79 29 f8 c0 03 5f d6 ※ data
pl4g_io_ready:
    3f 1c 00 f1 c8 03 00 54 08 60 01 91 09 79 61 f8 3f 05 00 f1 41 03 00 54 09 00 40 f9 3f 05 00 f1 21 03 00 54 09 a8 43 a9 29 01 40 b9 4a fd df 88 3f 01 0a 6b e0 02 00 54 0c ac 44 a9 0d 60 02 91 4e 00 80 52 8c 01 40 b9 04 00 00 14 29 05 00 11 5f 01 09 6b c0 01 00 54 2f 01 0c 0a 70 51 2f 8b 0f 02 40 f9 ff 1d 00 f1 28 ff ff 54 10 0a 80 b9 0e 79 2f f8 b0 79 2f f8 f5 ff ff 17 20 00 80 52 c0 03 5f d6 e0 03 1f aa c0 03 5f d6 e9 03 0a 2a 0a 1c 40 f9 49 fd 9f 88 08 79 61 f8 1f 05 00 f1 e0 07 9f 1a c0 03 5f d6 ※ data
pl4g_io_step:
    08 00 40 f9 1f 05 00 f1 a1 07 00 54 ea 03 00 aa 00 0d 08 4e 41 8d c5 3c 42 05 c0 3d 48 2d 42 a9 42 8c e0 6e 20 8c e0 6e 49 31 43 a9 1f 05 00 f1 00 18 82 4e e8 17 9f 1a 3f 05 00 f1 e9 17 9f 1a 7f 05 00 f1 29 15 89 1a 9f 05 00 f1 00 b8 b1 4e 0b 00 26 1e 08 01 0b 4b 08 01 09 0b e9 17 9f 1a 08 01 09 2a c8 04 00 34 09 04 40 f9 eb 03 00 aa 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e0 03 09 aa e4 03 1f aa e5 03 1f aa 01 00 00 d4 60 03 f8 b7 e8 03 0b aa 69 ad 43 a9 29 01 40 b9 6b fd df 88 3f 01 0b 6b 60 02 00 54 0d b1 44 a9 0e 61 02 91 4f 00 80 52 ad 01 40 b9 04 00 00 14 29 05 00 11 7f 01 09 6b 40 01 00 54 30 01 0d 0a 91 51 30 8b 30 02 40 f9 1f 1e 00 f1 28 ff ff 54 31 0a 80 b9 4f 79 30 f8 d1 79 30 f8 f5 ff ff 17 e9 03 0b 2a 08 1d 40 f9 09 fd 9f 88 c0 03 5f d6 ※ data
pl4g_io_open:
    5f fc 3f f1 69 00 00 54 60 04 80 92 c0 03 5f d6 fd 0f 1f f8 ff 07 40 d1 02 01 00 b4 e8 03 00 91 e9 03 02 aa 2a 14 40 38 6a 02 00 34 29 05 00 f1 0a 15 00 38 81 ff ff 54 e8 03 00 91 1f 08 00 71 1f 69 22 38 88 01 00 54 08 00 00 90 08 01 00 91 e1 03 00 91 02 59 60 f8 60 0c 80 92 08 07 80 52 c3 36 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 02 00 00 14 a0 02 80 92 ff 07 40 91 fd 07 41 f8 c0 03 5f d6 ※ data
pl4g_io_close:
    eb 03 00 aa ea 03 01 2a 68 85 45 f8 1f 05 00 f1 c1 07 00 54 00 0d 08 4e e9 03 00 aa 0c 60 02 91 4d 00 80 52 07 00 00 14 e8 03 0e 2a 2e 1d 40 f9 c8 fd 9f 88 28 01 40 f9 1f 05 00 f1 61 06 00 54 21 81 c5 3c 22 81 c6 3c 28 bd 47 a9 42 8c e0 6e 21 8c e0 6e 2e c1 48 a9 1f 05 00 f1 21 18 82 4e e8 17 9f 1a df 05 00 f1 ee 17 9f 1a ff 05 00 f1 ce 15 8e 1a 1f 06 00 f1 21 b8 b1 4e 2f 00 26 1e 08 01 0f 4b 08 01 0e 0b ee 17 9f 1a 08 01 0e 2a c8 03 00 34 20 05 40 f9 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 a0 02 f8 b7 28 b9 43 a9 08 01 40 b9 ce fd df 88 1f 01 0e 6b 20 fb ff 54 30 bd 44 a9 10 02 40 b9 04 00 00 14 08 05 00 11 df 01 08 6b 40 fa ff 54 11 01 10 0a f2 51 31 8b 51 02 40 f9 3f 1e 00 f1 28 ff ff 54 52 0a 80 b9 6d 79 31 f8 92 79 31 f8 f5 ff ff 17 40 7d 40 93 28 07 80 52 e1 03 1f aa e2 03 1f aa e3 03 1f aa e4 03 1f aa e5 03 1f aa 01 00 00 d4 c0 03 5f d6 ※ data
pl4g_io_drain:
    ea 03 00 aa e9 03 00 aa 48 85 45 f8 1f 05 00 f1 a1 07 00 54 00 0d 08 4e 2b 61 02 91 4c 00 80 52 07 00 00 14 e8 03 0d 2a 2d 1d 40 f9 a8 fd 9f 88 28 01 40 f9 1f 05 00 f1 61 06 00 54 21 81 c5 3c 22 81 c6 3c 28 b9 47 a9 42 8c e0 6e 21 8c e0 6e 2d bd 48 a9 1f 05 00 f1 21 18 82 4e e8 17 9f 1a bf 05 00 f1 ed 17 9f 1a df 05 00 f1 ad 15 8d 1a ff 05 00 f1 21 b8 b1 4e 2e 00 26 1e 08 01 0e 4b 08 01 0d 0b ed 17 9f 1a 08 01 0d 2a c8 03 00 34 20 05 40 f9 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 a0 02 f8 b7 28 b5 43 a9 08 01 40 b9 ad fd df 88 1f 01 0d 6b 20 fb ff 54 2f b9 44 a9 ef 01 40 b9 04 00 00 14 08 05 00 11 bf 01 08 6b 40 fa ff 54 10 01 0f 0a d1 51 30 8b 30 02 40 f9 1f 1e 00 f1 28 ff ff 54 31 0a 80 b9 4c 79 30 f8 71 79 30 f8 f5 ff ff 17 00 e4 00 6f 20 81 85 3c 20 81 86 3c 20 81 87 3c 20 81 88 3c c0 03 5f d6 ※ data
pl4g_heap_alloc:
    1f 40 40 f1 a9 01 00 54 08 fc 3f 91 e0 03 1f aa 62 00 80 52 01 cd 74 92 c8 1b 80 52 43 04 80 52 04 00 80 92 e5 03 1f aa 01 00 00 d4 1f 00 00 f1 e0 43 80 9a c0 03 5f d6 1f 04 04 f1 02 01 00 54 08 3c 00 91 1f 00 00 f1 08 fd 44 d3 08 15 9f 9a 09 ed 7c d3 08 05 00 d1 0c 00 00 14 1f 04 08 f1 03 01 00 54 09 40 80 52 08 02 80 52 29 f9 7f d3 08 05 00 91 3f 01 00 eb a3 ff ff 54 03 00 00 14 08 02 80 52 09 40 80 52 0a 00 00 90 4a 01 00 91 40 79 68 f8 80 00 00 b4 09 00 40 f9 49 79 28 f8 c0 03 5f d6 0a 00 00 90 0b 00 00 90 40 01 40 f9 a0 00 00 b4 68 01 40 f9 08 01 00 cb 1f 01 09 eb 82 01 00 54 e0 03 1f aa c8 1b 80 52 01 02 a0 52 62 00 80 52 43 04 80 52 04 00 80 92 e5 03 1f aa 01 00 00 d4 c0 00 f8 b7 08 00 44 91 68 01 00 f9 08 00 09 8b 48 01 00 f9 c0 03 5f d6 e0 03 1f aa c0 03 5f d6 ※ data
pl4g_heap_free:
    60 04 00 b4 3f 40 40 f1 49 01 00 54 28 fc 3f 91 e2 03 1f aa e3 03 1f aa 01 cd 74 92 e8 1a 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 c0 03 5f d6 3f 04 04 f1 e2 00 00 54 28 3c 00 91 3f 00 00 f1 08 fd 44 d3 08 05 00 d1 e8 03 88 9a 0b 00 00 14 3f 04 08 f1 03 01 00 54 09 40 80 52 08 02 80 52 29 f9 7f d3 08 05 00 91 3f 01 01 eb a3 ff ff 54 02 00 00 14 08 02 80 52 09 00 00 90 29 01 00 91 2a 79 68 f8 0a 00 00 f9 20 79 28 f8 c0 03 5f d6 ※ data

section .pl4grt.rodata.cst16
.Lpl4grt.1:
                             ※ align 16
    00 00 00 00 00 00 00 00 ff ff ff ff ff ff ff ff ※ data

section .pl4grt.rodata
.Lpl4grt.2:
pl4g_io_shape:
                             ※ align 8
    d8 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 10 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 18 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 20 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 28 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 30 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 38 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 48 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 50 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 58 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00 98 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00 16 00 3f 00 17 00 40 00 02 00 42 00 00 00 00 00 00 00 08 00 00 00 00 00 41 02 08 00 00 00 00 00 42 00 08 00 00 00 00 00 ※ data

section .pl4grt.bss writable
.Lpl4grt.3:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ※ data
