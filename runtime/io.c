/* The I/O runtime.
 *
 * Written in C and compiled for every architecture the compiler generates for,
 * ahead of time; what is packaged with the compiler is the code and its
 * relocations, which the compiler places in an image and patches with the
 * machinery it already has for its own code.  The language reaches it through
 * functions marked `@[external]`, which follow the system's calling convention,
 * and a record marked `@[abi]`, which is laid out the way C lays one out.
 *
 * It depends on nothing: no libc, no headers, no relocations to anything but
 * itself.  The system calls are written out because there is nothing to call
 * that would make them.
 */

typedef unsigned long u64;
typedef long i64;
typedef unsigned int u32;
typedef int i32;

/* -- the system calls ------------------------------------------------------ */

#if defined(__x86_64__)
# define NR_READ 0
# define NR_WRITE 1
# define NR_MMAP 9
# define NR_IO_URING_SETUP 425
# define NR_IO_URING_ENTER 426

static inline i64 sys(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f)
{
  register i64 r10 __asm__("r10") = d;
  register i64 r8 __asm__("r8") = e;
  register i64 r9 __asm__("r9") = f;
  i64 out;
  __asm__ volatile("syscall"
                   : "=a"(out)
                   : "a"(n), "D"(a), "S"(b), "d"(c), "r"(r10), "r"(r8), "r"(r9)
                   : "rcx", "r11", "memory");
  return out;
}

#elif defined(__aarch64__)
# define NR_READ 63
# define NR_WRITE 64
# define NR_MMAP 222
# define NR_IO_URING_SETUP 425
# define NR_IO_URING_ENTER 426

static inline i64 sys(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f)
{
  register i64 x8 __asm__("x8") = n;
  register i64 x0 __asm__("x0") = a;
  register i64 x1 __asm__("x1") = b;
  register i64 x2 __asm__("x2") = c;
  register i64 x3 __asm__("x3") = d;
  register i64 x4 __asm__("x4") = e;
  register i64 x5 __asm__("x5") = f;
  __asm__ volatile("svc #0"
                   : "+r"(x0)
                   : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
                   : "memory");
  return x0;
}

#elif defined(__riscv) && __riscv_xlen == 64
# define NR_READ 63
# define NR_WRITE 64
# define NR_MMAP 222
# define NR_IO_URING_SETUP 425
# define NR_IO_URING_ENTER 426

static inline i64 sys(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f)
{
  register i64 a7 __asm__("a7") = n;
  register i64 a0 __asm__("a0") = a;
  register i64 a1 __asm__("a1") = b;
  register i64 a2 __asm__("a2") = c;
  register i64 a3 __asm__("a3") = d;
  register i64 a4 __asm__("a4") = e;
  register i64 a5 __asm__("a5") = f;
  __asm__ volatile("ecall"
                   : "+r"(a0)
                   : "r"(a7), "r"(a1), "r"(a2), "r"(a3), "r"(a4), "r"(a5)
                   : "memory");
  return a0;
}

#else
# error "no system call convention for this architecture"
#endif

/* -- what the kernel's structures look like -------------------------------- */

struct sq_offsets {
  u32 head, tail, ring_mask, ring_entries, flags, dropped, array, resv1;
  u64 user_addr;
};

struct cq_offsets {
  u32 head, tail, ring_mask, ring_entries, overflow, cqes, flags, resv1;
  u64 user_addr;
};

struct params {
  u32 sq_entries, cq_entries, flags, sq_thread_cpu, sq_thread_idle;
  u32 features, wq_fd, resv[3];
  struct sq_offsets sq_off;
  struct cq_offsets cq_off;
};

struct sqe {
  unsigned char opcode, flags;
  unsigned short ioprio;
  i32 fd;
  u64 off, addr;
  u32 len, rw_flags;
  u64 user_data;
  unsigned short buf_index, personality;
  i32 splice_fd_in;
  u64 addr3, pad2;
};

struct cqe {
  u64 user_data;
  i32 res;
  u32 flags;
};

/* -- the ring, as the language declares it --------------------------------- */

/* Every field is a machine word, so that the record the language writes beside
 * this one is laid out the same by inspection rather than by argument. */
struct pl4g_ring {
  u64 state;                    /* 0 untried, 1 ready, 2 no ring here */
  u64 fd;
  u64 sq_head, sq_tail, sq_mask, sq_array, sqes;
  u64 cq_head, cq_tail, cq_mask, cqes;
};

#define UNTRIED 0
#define READY 1
#define NO_RING 2

#define ENTRIES 8
#define OP_READ 22
#define OP_WRITE 23

/* Which direction a call goes, and what each of the two ways of going there
 * calls it.  One table rather than two constants at each call site: the ring
 * and the kernel number the same operation differently, and saying so once is
 * what keeps a reader from having to check that they were paired correctly. */
struct way {
  unsigned short op;            /* what the ring calls it */
  unsigned short nr;            /* what the kernel calls it */
};

static const struct way ways[2] = {
  { OP_READ, NR_READ },
  { OP_WRITE, NR_WRITE },
};

#define READING 0
#define WRITING 1
#define ENTER_GETEVENTS 1
#define OFF_SQ_RING 0UL
#define OFF_CQ_RING 0x8000000UL
#define OFF_SQES 0x10000000UL
#define PROT_READ_WRITE 3
#define MAP_SHARED_POPULATE 0x8001

static inline u32 load_acquire(const u64 at)
{
  return __atomic_load_n((const u32 *) at, __ATOMIC_ACQUIRE);
}

static inline void store_release(const u64 at, u32 value)
{
  __atomic_store_n((u32 *) at, value, __ATOMIC_RELEASE);
}

static inline u32 load_plain(const u64 at)
{
  return *(const u32 *) at;
}

/* Make the ring, once.  A system that has no io_uring will not grow one, so a
 * failure is remembered and the calls below go straight to the kernel after
 * it -- which is what lets a program run where there is no ring at all. */
static int started(struct pl4g_ring *r)
{
  if (r->state != UNTRIED)
    return r->state == READY;
  r->state = NO_RING;
  struct params p;
  for (u64 i = 0; i < sizeof p / sizeof (u64); ++i)
    ((u64 *) &p)[i] = 0;
  i64 fd = sys(NR_IO_URING_SETUP, ENTRIES, (i64) &p, 0, 0, 0, 0);
  if (fd < 0)
    return 0;
  u64 sq_len = p.sq_off.array + (u64) p.sq_entries * 4;
  u64 cq_len = p.cq_off.cqes + (u64) p.cq_entries * 16;
  u64 sqe_len = (u64) p.sq_entries * sizeof (struct sqe);
  i64 sq = sys(NR_MMAP, 0, sq_len, PROT_READ_WRITE, MAP_SHARED_POPULATE,
               fd, OFF_SQ_RING);
  i64 cq = sys(NR_MMAP, 0, cq_len, PROT_READ_WRITE, MAP_SHARED_POPULATE,
               fd, OFF_CQ_RING);
  i64 sqes = sys(NR_MMAP, 0, sqe_len, PROT_READ_WRITE, MAP_SHARED_POPULATE,
                 fd, OFF_SQES);
  if (sq < 0 || cq < 0 || sqes < 0)
    return 0;
  r->fd = (u64) fd;
  r->sq_head = (u64) sq + p.sq_off.head;
  r->sq_tail = (u64) sq + p.sq_off.tail;
  r->sq_mask = (u64) sq + p.sq_off.ring_mask;
  r->sq_array = (u64) sq + p.sq_off.array;
  r->sqes = (u64) sqes;
  r->cq_head = (u64) cq + p.cq_off.head;
  r->cq_tail = (u64) cq + p.cq_off.tail;
  r->cq_mask = (u64) cq + p.cq_off.ring_mask;
  r->cqes = (u64) cq + p.cq_off.cqes;
  r->state = READY;
  return 1;
}

/* Submit one request and wait for its answer, which is what the kernel said. */
static i64 through_the_ring(struct pl4g_ring *r, unsigned char op, i32 fd,
                            u64 at, u64 len, u64 off)
{
  u32 mask = load_plain(r->sq_mask);
  u32 tail = load_acquire(r->sq_tail);
  u32 index = tail & mask;
  struct sqe *e = (struct sqe *) r->sqes + index;
  for (u64 i = 0; i < sizeof *e / sizeof (u64); ++i)
    ((u64 *) e)[i] = 0;
  e->opcode = op;
  e->fd = fd;
  e->off = off;
  e->addr = at;
  e->len = (u32) len;
  e->user_data = index;
  ((u32 *) r->sq_array)[index] = index;
  /* the entry is written before the kernel is told it is there */
  store_release(r->sq_tail, tail + 1);
  for (;;) {
    i64 entered = sys(NR_IO_URING_ENTER, (i64) r->fd, 1, 1,
                      ENTER_GETEVENTS, 0, 0);
    if (entered < 0)
      return entered;
    u32 head = load_plain(r->cq_head);
    u32 ctail = load_acquire(r->cq_tail);
    if (head == ctail)
      continue;
    u32 cmask = load_plain(r->cq_mask);
    const struct cqe *c = (const struct cqe *) r->cqes + (head & cmask);
    i64 res = c->res;
    store_release(r->cq_head, head + 1);
    return res;
  }
}

/* -- what the language calls ----------------------------------------------- */

static i64 go(struct pl4g_ring *r, int which, i32 fd, u64 at, u64 len)
{
  if (len == 0)
    return 0;
  if (started(r))
    return through_the_ring(r, (unsigned char) ways[which].op, fd, at, len, 0);
  return sys(ways[which].nr, fd, (i64) at, (i64) len, 0, 0, 0);
}

i64 pl4g_io_write(struct pl4g_ring *r, i32 fd, u64 at, u64 len)
{
  return go(r, WRITING, fd, at, len);
}

i64 pl4g_io_read(struct pl4g_ring *r, i32 fd, u64 at, u64 len)
{
  return go(r, READING, fd, at, len);
}
