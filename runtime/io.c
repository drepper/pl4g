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

#define ENTRIES 8

/* Every field is a machine word or a run of them, so that the record the
 * language writes beside this one is laid out the same by inspection rather
 * than by argument.
 *
 * `held` says what is known about each request that has been given a slot, and
 * `answer` what the kernel said about it once it has said anything.  A slot is
 * taken when a request is submitted and given back when what the kernel said
 * has been read out of it, so what is outstanding is exactly the slots that are
 * taken and not yet answered. */
struct pl4g_ring {
  u64 state;                    /* 0 untried, 1 ready, 2 no ring here */
  u64 fd;
  u64 sq_head, sq_tail, sq_mask, sq_array, sqes;
  u64 cq_head, cq_tail, cq_mask, cqes;
  u64 held[ENTRIES];            /* 0 free, 1 submitted, 2 answered */
  i64 answer[ENTRIES];
};

#define FREE 0
#define SUBMITTED 1
#define ANSWERED 2

/* What `pl4g_io_submit` answers where it could not take a slot. */
#define NO_SLOT (-1)

/* What the language has to agree with about the record it hands over.  The
 * two declarations are written in two languages and nothing makes them one, so
 * this is what they are compared through: a test reads these numbers out of the
 * packaged runtime and works the same ones out for the `@[abi]` record in
 * `modules/std.pl4g`.  A field added on one side and not the other is then a
 * failing test rather than a program reading the wrong word. */
/* Where each field is *and* how wide it is.  Where alone would not do: a field
 * made narrower can leave every offset where it was -- padding takes up what it
 * gave back -- and a reader of the wrong width is exactly the bug this is here
 * to catch. */
#define FIELD(name) __builtin_offsetof(struct pl4g_ring, name), \
                    sizeof (((struct pl4g_ring *) 0)->name)

const u64 pl4g_io_shape[] = {
  sizeof (struct pl4g_ring), _Alignof (struct pl4g_ring),
  FIELD(state), FIELD(fd),
  FIELD(sq_head), FIELD(sq_tail), FIELD(sq_mask), FIELD(sq_array),
  FIELD(sqes),
  FIELD(cq_head), FIELD(cq_tail), FIELD(cq_mask), FIELD(cqes),
  FIELD(held), FIELD(answer),
};

#define UNTRIED 0
#define READY 1
#define NO_RING 2

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

/* The one refusal this file makes up rather than passes on. */
#define EINVAL 22
#define OFF_SQ_RING 0UL
#define OFF_CQ_RING 0x8000000UL
#define OFF_SQES 0x10000000UL
#define PROT_READ_WRITE 3
#define MAP_SHARED_POPULATE 0x8001
#define MAP_PRIVATE_ANONYMOUS 0x22

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

/* Take in every answer the kernel has put in the completion ring.  Answers how
 * many were taken, which is what says whether waiting again would help. */
static int reap(struct pl4g_ring *r)
{
  u32 head = load_plain(r->cq_head);
  u32 tail = load_acquire(r->cq_tail);
  u32 mask = load_plain(r->cq_mask);
  int taken = 0;
  while (head != tail) {
    const struct cqe *c = (const struct cqe *) r->cqes + (head & mask);
    u64 who = c->user_data;
    if (who < ENTRIES) {
      r->answer[who] = c->res;
      r->held[who] = ANSWERED;
    }
    head += 1;
    taken += 1;
  }
  /* what has been taken in is the kernel's to reuse, and saying so is a
   * release: nothing read out of a completion may be seen after it */
  store_release(r->cq_head, head);
  return taken;
}

/* Hand the kernel what has been written and wait for at least one answer. */
static i64 enter(struct pl4g_ring *r, long least)
{
  return sys(NR_IO_URING_ENTER, (i64) r->fd, ENTRIES, least,
             least ? ENTER_GETEVENTS : 0, 0, 0);
}

/* How many requests have been submitted and not yet answered. */
static int outstanding(const struct pl4g_ring *r)
{
  int found = 0;
  for (int at = 0; at < ENTRIES; ++at)
    if (r->held[at] == SUBMITTED)
      found += 1;
  return found;
}

/* A slot nothing is using, waiting for one where every one is taken. */
static long a_slot(struct pl4g_ring *r)
{
  for (int round = 0; round < 2; ++round) {
    for (int at = 0; at < ENTRIES; ++at)
      if (r->held[at] == FREE)
        return at;
    /* Every slot is taken: what frees one is an answer being read, so ask the
     * kernel for the ones it owes.  Once only -- a second round that found
     * nothing means every slot holds an answer nobody has read, which is the
     * program's to sort out and not this. */
    if (r->state != READY || outstanding(r) == 0 || enter(r, 1) < 0)
      break;
    reap(r);
  }
  return NO_SLOT;
}

/* Write one request into the submission ring, without telling the kernel. */
static void submit(struct pl4g_ring *r, long slot, unsigned char op, i32 fd,
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
  e->user_data = (u64) slot;
  ((u32 *) r->sq_array)[index] = index;
  /* the entry is written before the kernel is told it is there */
  store_release(r->sq_tail, tail + 1);
  r->held[slot] = SUBMITTED;
  r->answer[slot] = 0;
}

/* -- the arguments the program was started with ---------------------------- */

/* A string as the language holds one: where the bytes are and how many there
 * are.  Not the nul-terminated thing the kernel hands over -- the length is
 * counted here, once, so that nothing downstream has to walk the bytes to find
 * out how many there are. */
struct counted {
  const unsigned char *at;
  u64 len;
};

/* And the pair that is a run of them: where they are and how many. */
struct run {
  struct counted *at;
  u64 len;
};

static u64 how_long(const unsigned char *s)
{
  u64 n = 0;
  while (s[n] != 0)
    n += 1;
  return n;
}

/* Read the arguments off the stack the kernel set the process up with, and put
 * them where `out` says.
 *
 * What the kernel leaves at the stack pointer is the count, then that many
 * pointers, then a null.  Each of them is nul-terminated, which is the kernel's
 * shape and not this language's, so each is counted here and what comes out is
 * a run of counted strings.
 *
 * The run itself is asked of the system, since how many there are is not known
 * until the program starts.  It is never given back: it lasts as long as the
 * program does, which is exactly how long what a program was started with is
 * worth having.  Where the system will not give it, the answer is no arguments
 * at all rather than some of them. */
void pl4g_args(const u64 *stack, struct run *out)
{
  out->at = 0;
  out->len = 0;
  if (stack == 0)
    return;
  u64 count = stack[0];
  const unsigned char *const *argv = (const unsigned char *const *) &stack[1];
  if (count == 0)
    return;
  i64 room = sys(NR_MMAP, 0, count * sizeof (struct counted),
                 PROT_READ_WRITE, MAP_PRIVATE_ANONYMOUS, -1, 0);
  /* What the kernel refuses with is a small negative number and what it gives
     is an address, which on every one of these systems is a long way below the
     top -- so the refusals are the range and not merely the sign. */
  if (room < 0)
    return;
  struct counted *made = (struct counted *) room;
  for (u64 at = 0; at < count; ++at) {
    made[at].at = argv[at];
    made[at].len = argv[at] == 0 ? 0 : how_long(argv[at]);
  }
  out->at = made;
  out->len = count;
}

/* -- the environment ------------------------------------------------------- */

/* Read the environment off the same stack, as a run of names and values, each
 * name followed by what it stands for.
 *
 * What the kernel leaves after the arguments is another run of pointers ended
 * by a null, each of them `NAME=VALUE`.  The splitting is done here because
 * this is where the bytes already are: a string in this language is where its
 * bytes are and how many there are, so a name and a value are two such strings
 * pointing into the one the kernel handed over, and nothing is copied.
 *
 * A variable with no `=` in it -- which a program started by hand may be given
 * and the shell never produces -- is a name standing for nothing.
 *
 * Flat rather than a run of pairs: the language has a dictionary to put these
 * in and no syntax for taking a pair apart into one, so what comes back is
 * walked two at a time by the few lines of `std` that build the table. */
struct run pl4g_env(const u64 *stack)
{
  struct run out = { 0, 0 };
  if (stack == 0)
    return out;
  /* The count, that many arguments, a null, and then these. */
  const unsigned char *const *envp =
    (const unsigned char *const *) &stack[stack[0] + 2];
  u64 count = 0;
  while (envp[count] != 0)
    count += 1;
  if (count == 0)
    return out;
  i64 room = sys(NR_MMAP, 0, 2 * count * sizeof (struct counted),
                 PROT_READ_WRITE, MAP_PRIVATE_ANONYMOUS, -1, 0);
  /* As above: a refusal is a small negative number and an address is not. */
  if (room < 0)
    return out;
  struct counted *made = (struct counted *) room;
  for (u64 at = 0; at < count; ++at) {
    const unsigned char *one = envp[at];
    u64 len = how_long(one);
    u64 split = 0;
    while (split < len && one[split] != '=')
      split += 1;
    made[2 * at].at = one;
    made[2 * at].len = split;
    made[2 * at + 1].at = one + (split < len ? split + 1 : len);
    made[2 * at + 1].len = split < len ? len - split - 1 : 0;
  }
  out.at = made;
  out.len = 2 * count;
  return out;
}

/* -- what the language calls ----------------------------------------------- */

/* Start a request, answering the slot it is in.
 *
 * The kernel is not told yet: what tells it is waiting for an answer, or the
 * drain a program does before it ends.  So a write is outstanding until one of
 * those happens, which is what lets several be in flight at once.
 *
 * A slot is taken either way -- with a ring and without one -- so the handle a
 * program holds is the same thing whichever it got, and reading it is one
 * question with one answer. */
i64 pl4g_io_submit(struct pl4g_ring *r, i32 which, i32 fd, u64 at, u64 len)
{
  int ready = started(r);
  long slot = a_slot(r);
  if (slot < 0)
    return NO_SLOT;
  if (!ready || len == 0) {
    /* No ring, or nothing to do: the work is done here and the answer put
     * where an answer goes.  A slot is taken for it all the same, so that what
     * the program holds is the same handle either way and what it reads out of
     * it is the number the kernel gave -- which is the whole of what emulating
     * a ring has to get right. */
    r->answer[slot] = len == 0 ? 0
        : sys(ways[which].nr, fd, (i64) at, (i64) len, 0, 0, 0);
    r->held[slot] = ANSWERED;
    return slot;
  }
  submit(r, slot, (unsigned char) ways[which].op, fd, at, len, 0);
  return slot;
}

/* Wait for the request in `slot` and answer what the kernel said about it.
 * The slot is given back, so an answer is read once. */
i64 pl4g_io_wait(struct pl4g_ring *r, i64 slot)
{
  if (slot < 0 || slot >= ENTRIES)
    return -EINVAL;
  while (r->held[slot] == SUBMITTED) {
    i64 got = enter(r, 1);
    if (got < 0)
      return got;
    reap(r);
  }
  i64 answer = r->held[slot] == ANSWERED ? r->answer[slot] : -EINVAL;
  r->held[slot] = FREE;
  return answer;
}

/* Wait for every request that is outstanding, and give its slot back.
 *
 * What a program does before it ends: a request the kernel has not answered is
 * a write that may not have happened, and a program that ended without knowing
 * would have written nothing and said nothing about it. */
void pl4g_io_drain(struct pl4g_ring *r)
{
  while (r->state == READY && outstanding(r) > 0) {
    /* Asking for one answer is not being given one: `io_uring_enter` may come
     * back with nothing ready, and what makes that right is asking again.  The
     * wait is in the kernel, so a turn that finds nothing costs a call and not
     * a spin -- and a request that will never be answered hangs here exactly as
     * waiting for that one request would. */
    if (enter(r, 1) < 0)
      break;
    reap(r);
  }
  for (int at = 0; at < ENTRIES; ++at)
    r->held[at] = FREE;
}
