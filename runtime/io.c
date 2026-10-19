/* The runtime: I/O, and the heap `⎕heap` names.
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
# define NR_WRITEV 20
# define NR_MMAP 9
# define NR_MUNMAP 11
# define NR_OPENAT 257
# define NR_CLOSE 3
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
# define NR_WRITEV 66
# define NR_MMAP 222
# define NR_MUNMAP 215
# define NR_OPENAT 56
# define NR_CLOSE 57
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
# define NR_WRITEV 66
# define NR_MMAP 222
# define NR_MUNMAP 215
# define NR_OPENAT 56
# define NR_CLOSE 57
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
 * taken and not yet answered.
 *
 * `kept_at` and `kept_len` are room of the heap's a request's bytes were copied
 * into -- a view that was not one run, made into one -- which the kernel reads
 * until it answers and which is given back the moment it has. */
struct pl4g_ring {
  u64 state;                    /* 0 untried, 1 ready, 2 no ring here */
  u64 fd;
  u64 sq_head, sq_tail, sq_mask, sq_array, sqes;
  u64 cq_head, cq_tail, cq_mask, cqes;
  u64 held[ENTRIES];            /* 0 free, 1 submitted, 2 answered */
  i64 answer[ENTRIES];
  u64 kept_at[ENTRIES];
  u64 kept_len[ENTRIES];
};

void pl4g_heap_free(void *at, u64 size);

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
  FIELD(held), FIELD(answer), FIELD(kept_at), FIELD(kept_len),
};

#define UNTRIED 0
#define READY 1
#define NO_RING 2

#define OP_READ 22
#define OP_WRITE 23
#define OP_WRITEV 2

/* Which direction a call goes, and what each of the two ways of going there
 * calls it.  One table rather than two constants at each call site: the ring
 * and the kernel number the same operation differently, and saying so once is
 * what keeps a reader from having to check that they were paired correctly. */
struct way {
  unsigned short op;            /* what the ring calls it */
  unsigned short nr;            /* what the kernel calls it */
};

static const struct way ways[3] = {
  { OP_READ, NR_READ },
  { OP_WRITE, NR_WRITE },
  { OP_WRITEV, NR_WRITEV },
};

#define READING 0
#define WRITING 1
/* Several runs of bytes written as one request.  `at` is then where the list of
 * them is and `len` is how many are in it, which is the shape the ring and the
 * kernel both take -- so it goes through `submit` with nothing said about it,
 * and the way without a ring is `writev` with the same two arguments. */
#define WRITING_SEVERAL 2
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

/* Give back what the request in `slot` kept until the kernel had answered it. */
static void give_back(struct pl4g_ring *r, long slot)
{
  if (r->kept_at[slot] != 0) {
    pl4g_heap_free((void *) r->kept_at[slot], r->kept_len[slot]);
    r->kept_at[slot] = 0;
    r->kept_len[slot] = 0;
  }
}

/* Take in every answer the kernel has put in the completion ring.  Answers how
 * many were taken, which is what says whether waiting again would help.  What a
 * request kept for the kernel to read is given back with its answer. */
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
      give_back(r, (long) who);
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

/* A string as the language holds one: where the bytes are, how many there are,
 * and the allocator they came from.  Not the nul-terminated thing the kernel
 * hands over -- the length is counted here, once, so that nothing downstream has
 * to walk the bytes to find out how many there are.  The bytes are the kernel's,
 * which nothing gives back, so the allocator is none. */
struct counted {
  const unsigned char *at;
  u64 len;
  void *allocator;
};

/* And the pair that is a run of them: where they are and how many. */
struct run {
  struct counted *at;
  u64 len;
};

/* What a `T⟦⟧` of one dimension is: where the first element is, how many
 * there are, and how many elements apart neighbours are -- one, for a run. */
struct view {
  struct counted *at;
  u64 len;
  u64 stride;
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
static void hash_start(const u64 *stack);

void pl4g_args(const u64 *stack, struct view *out)
{
  /* Where the process's tables take their seeds from is worked out here, the
   * one thing every program calls with the stack in hand. */
  hash_start(stack);
  out->at = 0;
  out->len = 0;
  out->stride = 1;
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
    made[at].allocator = 0;
  }
  out->at = made;
  out->len = count;
}

/* -- hashing ---------------------------------------------------------------- */

/* What a table's keys are hashed with: rapidhash (after Nicolas De Carli's,
 * itself after wyhash), seeded, so that which keys land together depends on a
 * seed a program cannot see.  A table carries its own seed, taken from
 * `pl4g_hash_seed`, which mixes a base worked out once per process with a count:
 * no two tables share one, and knowing one says nothing of another.
 *
 * The compiler works out the same hash for the tables it lays out in the image
 * (`pypl4g/sema/rapidhash.py`), and a test holds the two to each other. */
static const u64 rapid_secret[3] = {
  0x2d358dccaa6c78a5ul, 0x8bb84b93962eacc9ul, 0x4b33a62ed433d4a3ul
};

/* Both halves of the product of two words. */
static inline void rapid_mum(u64 *a, u64 *b)
{
  unsigned __int128 r = (unsigned __int128) *a * *b;
  *a = (u64) r;
  *b = (u64) (r >> 64);
}

static inline u64 rapid_mix(u64 a, u64 b)
{
  rapid_mum(&a, &b);
  return a ^ b;
}

static inline u64 rapid_read64(const unsigned char *p)
{
  u64 v;
  __builtin_memcpy(&v, p, 8);
  return v;
}

static inline u64 rapid_read32(const unsigned char *p)
{
  u32 v;
  __builtin_memcpy(&v, p, 4);
  return v;
}

/* The hash of the `len` bytes at `at`, with `seed`. */
u64 pl4g_hash(const unsigned char *at, u64 len, u64 seed)
{
  const unsigned char *p = at;
  seed ^= rapid_mix(seed ^ rapid_secret[0], rapid_secret[1]) ^ len;
  u64 a, b;
  if (len <= 16) {
    if (len >= 4) {
      const unsigned char *last = p + len - 4;
      a = (rapid_read32(p) << 32) | rapid_read32(last);
      u64 delta = (len & 24) >> (len >> 3);
      b = (rapid_read32(p + delta) << 32) | rapid_read32(last - delta);
    } else if (len > 0) {
      a = ((u64) p[0] << 56) | ((u64) p[len >> 1] << 32) | p[len - 1];
      b = 0;
    } else
      a = b = 0;
  } else {
    u64 i = len;
    if (i > 48) {
      u64 see1 = seed, see2 = seed;
      while (i >= 96) {
        seed = rapid_mix(rapid_read64(p) ^ rapid_secret[0], rapid_read64(p + 8) ^ seed);
        see1 = rapid_mix(rapid_read64(p + 16) ^ rapid_secret[1], rapid_read64(p + 24) ^ see1);
        see2 = rapid_mix(rapid_read64(p + 32) ^ rapid_secret[2], rapid_read64(p + 40) ^ see2);
        seed = rapid_mix(rapid_read64(p + 48) ^ rapid_secret[0], rapid_read64(p + 56) ^ seed);
        see1 = rapid_mix(rapid_read64(p + 64) ^ rapid_secret[1], rapid_read64(p + 72) ^ see1);
        see2 = rapid_mix(rapid_read64(p + 80) ^ rapid_secret[2], rapid_read64(p + 88) ^ see2);
        p += 96;
        i -= 96;
      }
      if (i >= 48) {
        seed = rapid_mix(rapid_read64(p) ^ rapid_secret[0], rapid_read64(p + 8) ^ seed);
        see1 = rapid_mix(rapid_read64(p + 16) ^ rapid_secret[1], rapid_read64(p + 24) ^ see1);
        see2 = rapid_mix(rapid_read64(p + 32) ^ rapid_secret[2], rapid_read64(p + 40) ^ see2);
        p += 48;
        i -= 48;
      }
      seed ^= see1 ^ see2;
    }
    if (i > 16) {
      seed = rapid_mix(rapid_read64(p) ^ rapid_secret[2],
                       rapid_read64(p + 8) ^ seed ^ rapid_secret[1]);
      if (i > 32)
        seed = rapid_mix(rapid_read64(p + 16) ^ rapid_secret[2], rapid_read64(p + 24) ^ seed);
    }
    a = rapid_read64(p + i - 16);
    b = rapid_read64(p + i - 8);
  }
  a ^= rapid_secret[1];
  b ^= seed;
  rapid_mum(&a, &b);
  return rapid_mix(a ^ rapid_secret[0] ^ len, b ^ rapid_secret[1]);
}

/* The hash of a key that is one word: its eight bytes, as they are in memory. */
u64 pl4g_hash_word(u64 word, u64 seed)
{
  unsigned char bytes[8];
  __builtin_memcpy(bytes, &word, 8);
  return pl4g_hash(bytes, 8, seed);
}

/* What every table's seed is worked out from, once per process, and how many
 * have been handed out. */
static u64 hash_base;
static u64 hash_count;

/* What the auxiliary vector calls the 16 random bytes the kernel leaves on the
 * stack for the process. */
#define AT_NULL 0
#define AT_RANDOM 25

/* Whether `one` begins with `name` and then `=`, answering what follows. */
static const unsigned char *setting(const unsigned char *one, const char *name)
{
  while (*name != 0) {
    if (*one != (unsigned char) *name)
      return 0;
    one += 1;
    name += 1;
  }
  return *one == '=' ? one + 1 : 0;
}

/* Work the base out from what the process was started with: `PL4G_HASH_SEED`
 * where the environment sets it -- a number, which makes every table of the run
 * one that can be had again -- and the kernel's random bytes otherwise. */
static void hash_start(const u64 *stack)
{
  if (stack == 0)
    return;
  const unsigned char *const *envp =
    (const unsigned char *const *) &stack[stack[0] + 2];
  u64 at = 0;
  while (envp[at] != 0) {
    const unsigned char *said = setting(envp[at], "PL4G_HASH_SEED");
    if (said != 0) {
      u64 n = 0;
      while (*said >= '0' && *said <= '9') {
        n = n * 10 + (u64) (*said - '0');
        said += 1;
      }
      hash_base = rapid_mix(n ^ rapid_secret[2], rapid_secret[0]);
      return;
    }
    at += 1;
  }
  const u64 *aux = (const u64 *) &envp[at + 1];
  for (; aux[0] != AT_NULL; aux += 2)
    if (aux[0] == AT_RANDOM) {
      const unsigned char *random = (const unsigned char *) aux[1];
      hash_base = rapid_mix(rapid_read64(random) ^ rapid_secret[0],
                            rapid_read64(random + 8) ^ rapid_secret[1]);
      return;
    }
  /* No random bytes: where the stack is, which differs from run to run where
   * the system places it at random, and is better than nothing where not. */
  hash_base = rapid_mix((u64) stack ^ rapid_secret[0], rapid_secret[1]);
}

/* A seed for a table of its own: never nought, which is what a table laid out
 * in the image says before it has been given one. */
u64 pl4g_hash_seed(void)
{
  hash_count += 1;
  u64 seed = rapid_mix(hash_base ^ rapid_secret[0], hash_count ^ rapid_secret[1]);
  return seed == 0 ? 1 : seed;
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
    made[2 * at].allocator = 0;
    made[2 * at + 1].at = one + (split < len ? split + 1 : len);
    made[2 * at + 1].len = split < len ? len - split - 1 : 0;
    made[2 * at + 1].allocator = 0;
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
 * question with one answer.
 *
 * Where `keep` says so, the `len` bytes at `at` are a copy in the heap's room
 * -- a view that was not one run, made one for the kernel to read -- which the
 * slot keeps until the kernel has answered, and which is given back then: now,
 * where there is nothing to wait for. */
i64 pl4g_io_submit(struct pl4g_ring *r, i32 which, i32 fd, u64 at, u64 len,
                   u64 keep)
{
  /* `which` indexes a table and comes from the language, so it is checked here:
   * this is the one door from a program into an array of function numbers, and
   * a program cannot be the thing that keeps it shut. */
  if (which < 0 || which >= (i32) (sizeof ways / sizeof ways[0])) {
    if (keep)
      pl4g_heap_free((void *) at, len);
    return -EINVAL;
  }
  int ready = started(r);
  long slot = a_slot(r);
  if (slot < 0) {
    if (keep)
      pl4g_heap_free((void *) at, len);
    return NO_SLOT;
  }
  if (!ready || len == 0) {
    /* No ring, or nothing to do: the work is done here and the answer put
     * where an answer goes.  A slot is taken for it all the same, so that what
     * the program holds is the same handle either way and what it reads out of
     * it is the number the kernel gave -- which is the whole of what emulating
     * a ring has to get right. */
    r->answer[slot] = len == 0 ? 0
        : sys(ways[which].nr, fd, (i64) at, (i64) len, 0, 0, 0);
    r->held[slot] = ANSWERED;
    if (keep)
      pl4g_heap_free((void *) at, len);
    return slot;
  }
  /* At the file's own position, which is what -1 asks the ring for: a nought
   * there is the absolute offset nought, which a pipe ignores and a file does
   * not -- every read would start at the top and every write overwrite it. */
  submit(r, slot, (unsigned char) ways[which].op, fd, at, len, (u64) -1);
  if (keep) {
    r->kept_at[slot] = at;
    r->kept_len[slot] = len;
  }
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

/* Whether the request in `slot` has been answered, without waiting for it:
 * whatever answers the kernel has put in the completion ring are taken in
 * first.  The slot stays the program's, to be read with `pl4g_io_wait`, which
 * then does not wait.  What a scheduler asks of every task it holds before
 * deciding whether to resume it. */
i64 pl4g_io_ready(struct pl4g_ring *r, i64 slot)
{
  if (slot < 0 || slot >= ENTRIES)
    return 1;
  if (r->held[slot] == SUBMITTED && r->state == READY)
    reap(r);
  return r->held[slot] != SUBMITTED;
}

/* Hand the kernel what has been written and wait until it has answered at least
 * one request -- any one.  What a scheduler does when no task it holds can go
 * on: whichever answer comes first is the task that goes next. */
void pl4g_io_step(struct pl4g_ring *r)
{
  if (r->state == READY && outstanding(r) > 0 && enter(r, 1) >= 0)
    reap(r);
}

/* -- opening and closing ---------------------------------------------------- */

/* How `pl4g_io_open` is asked to open: the numbers the language passes, one per
 * type `std.fs.Dir.open` makes.  The flags are the kernel's, and the same on the
 * three architectures. */
#define OPEN_READING 0
#define OPEN_WRITING 1
#define OPEN_BOTH 2
#define O_WRONLY 01
#define O_RDWR 02
#define O_CREAT 0100
#define O_TRUNC 01000
#define O_CLOEXEC 02000000
#define ENAMETOOLONG 36
#define NAME_MAX_BYTES 4096

/* Open the file named by the `len` bytes at `at`, relative to the directory
 * `dir` -- a descriptor of one, or `AT_FDCWD` for the one the process is in, which
 * is what `std.fs.State.cwd` hands out.  Answers the descriptor, or what the
 * kernel refused with as a negative number.
 *
 * The name the language holds is counted and the one the kernel wants ends in a
 * nul, so it is copied here, once, with one added -- refusing a name with a nul
 * inside it, which the kernel would read as a shorter one, and one too long for
 * any file system to have.  A file opened for writing is made if it is not
 * there and emptied if it is; one opened for both is made and left as it is. */
i64 pl4g_io_open(i32 how, i32 dir, u64 at, u64 len)
{
  unsigned char name[NAME_MAX_BYTES];
  if (len >= NAME_MAX_BYTES)
    return -ENAMETOOLONG;
  const unsigned char *from = (const unsigned char *) at;
  for (u64 i = 0; i < len; ++i) {
    if (from[i] == 0)
      return -EINVAL;
    name[i] = from[i];
  }
  name[len] = 0;
  i64 flags = O_CLOEXEC;
  if (how == OPEN_WRITING)
    flags |= O_WRONLY | O_CREAT | O_TRUNC;
  else if (how == OPEN_BOTH)
    flags |= O_RDWR | O_CREAT;
  else if (how != OPEN_READING)
    return -EINVAL;
  return sys(NR_OPENAT, dir, (i64) name, flags, 0666, 0, 0);
}

/* Close `fd`, once every request the ring holds has been answered: one still in
 * flight may be a write to it, and closing under it would be the kernel's to
 * sort out in a way the program never hears about.  What those requests answered
 * stays where it is, to be read with `pl4g_io_wait`.  Answers what `close` said:
 * nought, or a negative number. */
i64 pl4g_io_close(struct pl4g_ring *r, i32 fd)
{
  while (r->state == READY && outstanding(r) > 0) {
    if (enter(r, 1) < 0)
      break;
    reap(r);
  }
  return sys(NR_CLOSE, fd, 0, 0, 0, 0, 0);
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

/* -- the heap ---------------------------------------------------------------- */

/* What `⎕heap` is: an allocator that gives back one object at a time.
 *
 * **The caller says how big what it gives back is**, as it said how big what it
 * asked for was: the compiler knows the size of every object it frees, so there is
 * no header in front of an object to say it, and an object of sixteen bytes takes
 * sixteen.  C23's `free_sized`, C++'s sized `delete` and Rust's `dealloc`, which
 * takes the layout, are the same interface.
 *
 * Sizes are rounded to a *class*: sixteen bytes apart up to 256, and then powers of
 * two up to 64 KiB.  Each class keeps a list of what was given back to it, linked
 * through the objects themselves, and what is asked of a class with nothing on its
 * list is cut from a slab taken from the system.  Anything larger than the largest
 * class is mapped on its own and unmapped when it is given back, its size being
 * known exactly.
 *
 * One thread: nothing here is shared with another, the language having none. */

#define HEAP_GRAIN 16UL
#define HEAP_SMALL 256UL
#define HEAP_LARGEST 65536UL
#define HEAP_SLAB (1UL << 20)
#define HEAP_PAGE 4096UL
/* sixteen classes sixteen apart, and eight powers of two from 512 to 64 KiB */
#define HEAP_CLASSES 24

struct heap_free {
  struct heap_free *next;
};

static struct heap_free *heap_lists[HEAP_CLASSES];
static unsigned char *heap_bump;
static unsigned char *heap_end;

/* Which class a size falls in, and how big the objects of that class are. */
static u64 heap_class(u64 size, u64 *rounded)
{
  if (size <= HEAP_SMALL) {
    u64 n = size == 0 ? 1 : (size + HEAP_GRAIN - 1) / HEAP_GRAIN;
    *rounded = n * HEAP_GRAIN;
    return n - 1;
  }
  u64 room = 2 * HEAP_SMALL;
  u64 at = HEAP_SMALL / HEAP_GRAIN;
  while (room < size) {
    room *= 2;
    at += 1;
  }
  *rounded = room;
  return at;
}

static u64 heap_pages(u64 size)
{
  return (size + HEAP_PAGE - 1) & ~(HEAP_PAGE - 1);
}

/* Room for `size` bytes, aligned to sixteen; nothing where the system has none. */
void *pl4g_heap_alloc(u64 size)
{
  if (size > HEAP_LARGEST) {
    i64 room = sys(NR_MMAP, 0, heap_pages(size), PROT_READ_WRITE,
                   MAP_PRIVATE_ANONYMOUS, -1, 0);
    return room < 0 ? 0 : (void *) room;
  }
  u64 rounded;
  u64 c = heap_class(size, &rounded);
  struct heap_free *found = heap_lists[c];
  if (found != 0) {
    heap_lists[c] = found->next;
    return found;
  }
  if (heap_bump == 0 || (u64) (heap_end - heap_bump) < rounded) {
    /* What is left of the old slab is not worth a list of its own: it is less
       than one object of this class, and the classes below will not ask for it
       often enough to matter. */
    i64 room = sys(NR_MMAP, 0, HEAP_SLAB, PROT_READ_WRITE,
                   MAP_PRIVATE_ANONYMOUS, -1, 0);
    if (room < 0)
      return 0;
    heap_bump = (unsigned char *) room;
    heap_end = heap_bump + HEAP_SLAB;
  }
  void *made = heap_bump;
  heap_bump += rounded;
  return made;
}

/* Give back what `pl4g_heap_alloc(size)` answered. */
void pl4g_heap_free(void *at, u64 size)
{
  if (at == 0)
    return;
  if (size > HEAP_LARGEST) {
    sys(NR_MUNMAP, (i64) at, heap_pages(size), 0, 0, 0, 0);
    return;
  }
  u64 rounded;
  u64 c = heap_class(size, &rounded);
  struct heap_free *one = (struct heap_free *) at;
  one->next = heap_lists[c];
  heap_lists[c] = one;
}
