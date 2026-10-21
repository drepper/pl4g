/* What the runtime remembers from one call to the next.
 *
 * A compiled program has one of each, for the life of its process, and the
 * runtime it carries keeps them in variables of its own.  The interpreter runs
 * many programs in one process, each in a state of its own, so where it is
 * built into the interpreter (PL4G_RUNTIME_STATE) the runtime keeps them in
 * the state the running thread has selected -- `pl4g_runtime_current` -- and
 * keeps account of what it took from the system, so that the state can give
 * all of it back.  The packaged runtime is built without, and is what it was.
 */
#ifndef PL4G_RUNTIME_STATE_H
#define PL4G_RUNTIME_STATE_H

/* sixteen classes sixteen apart, and eight powers of two from 512 to 64 KiB */
#define HEAP_CLASSES 24

struct heap_free {
  struct heap_free *next;
};

/* A mapping of the heap's -- a slab, or an object too large for a class --
 * kept in front of what it holds, so that giving one back is no search. */
struct pl4g_mapped {
  struct pl4g_mapped *next;
  struct pl4g_mapped *prev;
  unsigned long size;
  unsigned long unused;
};

/* What else a state took and nothing gives back while it runs: the runs of
 * arguments and of the environment, and the ring and its mappings.  An entry
 * is a mapping, or a descriptor where `fd` is not negative. */
struct pl4g_ledger_entry {
  unsigned long at;
  unsigned long len;
  long fd;
};
#define PL4G_LEDGER_ENTRIES ((4096 - 16) / sizeof (struct pl4g_ledger_entry))
struct pl4g_ledger {
  struct pl4g_ledger *next;
  unsigned long used;
  struct pl4g_ledger_entry entries[PL4G_LEDGER_ENTRIES];
};

struct pl4g_runtime_state {
  /* what every table's seed is worked out from, and how many were handed out */
  unsigned long hash_base;
  unsigned long hash_count;
  /* the heap: a list of what was given back per class, and the slab cut from */
  struct heap_free *heap_lists[HEAP_CLASSES];
  unsigned char *heap_bump;
  unsigned char *heap_end;
  struct pl4g_mapped *mapped;
  struct pl4g_ledger *ledger;
};

#ifdef __cplusplus
extern "C" {
#endif

/* The state the runtime works in on this thread: one of the process's own
 * until something selects another. */
extern __thread struct pl4g_runtime_state *pl4g_runtime_current;

/* Give back everything `state` took from the system, and leave it as new. */
void pl4g_runtime_release(struct pl4g_runtime_state *state);

#ifdef __cplusplus
}
#endif

#endif /* PL4G_RUNTIME_STATE_H */
