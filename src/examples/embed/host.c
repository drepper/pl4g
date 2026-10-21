/* An example of a program embedding PL4G's interpreter.

   It loads the interpreter as a shared object with dlopen, finds the three
   functions the object offers with dlsym, makes a state of the interpreter
   and hands it a PL4G program held in a string.  That program asks this one
   for its input through two functions defined here, works out a few things
   about it, and hands the answers back through a third, which only stores
   them.  Once the program has run, main checks what was stored against what
   it works out itself, and exits with 0 where everything agrees and with a
   number saying what did not.

   It does so in two states made at the same time, to show that they can be:
   each holds what its runs leave behind -- the heap, the seed of every table --
   and neither sees the other's.

   The three functions are found by the interpreter with
   dlsym(RTLD_DEFAULT, ...), so this program is linked with -rdynamic: what it
   defines is then in its dynamic symbol table, where dlsym looks.

   Run as `pl4g-embed-example [LIBPL4G-EMBED.SO]`.  */
#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "embed/pl4g-embed.h"

/* The interpreter, where no other is named.  */
#ifndef DEFAULT_LIBRARY
#define DEFAULT_LIBRARY "libpl4g-embed.so"
#endif

/* The input: what the program is told there is, and each reading.  */
static const int64_t readings[] = { 17, -4, 256, 9, 0, 1000, -73, 42, 5, 5 };
#define COUNT (int64_t) (sizeof readings / sizeof readings[0])

/* What the program delivers, and whether it did.  */
static struct {
  bool delivered;
  int64_t count;
  int64_t sum;
  int64_t smallest;
  int64_t largest;
  int64_t squares;
} result;

/* How many readings there are.  */
int64_t example_count(void)
{
  return COUNT;
}

/* The reading at AT, or nought for an index outside them.  */
int64_t example_value(int64_t at)
{
  return at >= 0 && at < COUNT ? readings[at] : 0;
}

/* Where the program hands its answers.  Only stored: main reads them once the
   program has run.  */
void example_deliver(int64_t count, int64_t sum, int64_t smallest, int64_t largest, int64_t squares)
{
  result.delivered = true;
  result.count = count;
  result.sum = sum;
  result.smallest = smallest;
  result.largest = largest;
  result.squares = squares;
}

/* The program, in PL4G.  It declares the three functions above as defined by
   its host, reads every reading, and delivers how many there were, their sum,
   the smallest and the largest, and the sum of their squares.  */
static const char program[] =
  "@[host(\"example_count\")]\n"
  "fn count() \u2192 i64\n"
  "\n"
  "@[host(\"example_value\")]\n"
  "fn value(at: i64) \u2192 i64\n"
  "\n"
  "@[host(\"example_deliver\")]\n"
  "fn deliver(count: i64, sum: i64, smallest: i64, largest: i64, squares: i64)\n"
  "\n"
  "let std := \u2395import(\"std\")\n"
  "\n"
  "@[startup, impure]\n"
  "fn main(init: std.Init) \u2192 u6:\n"
  "    let n: i64 = count()\n"
  "    let sum: mut i64 = 0i64\n"
  "    let squares: mut i64 = 0i64\n"
  "    let smallest: mut i64 = value(0i64)\n"
  "    let largest: mut i64 = smallest\n"
  "    foreach at := 0i64\u2026n:\n"
  "        let x: i64 = value(at)\n"
  "        sum \u2190 sum + x\n"
  "        squares \u2190 squares + x \u00d7 x\n"
  "        if x < smallest:\n"
  "            smallest \u2190 x\n"
  "        if x > largest:\n"
  "            largest \u2190 x\n"
  "    deliver(n, sum, smallest, largest, squares)\n"
  "    0u6\n";

static int check(void);

int main(int argc, char *argv[])
{
  const char *library = argc > 1 ? argv[1] : DEFAULT_LIBRARY;
  void *handle = dlopen(library, RTLD_NOW);
  if (handle == NULL)
    {
      fprintf(stderr, "cannot load %s: %s\n", library, dlerror());
      return 2;
    }
  pl4g_embed_state_new_t state_new = (pl4g_embed_state_new_t) dlsym(handle, "pl4g_embed_state_new");
  pl4g_embed_state_free_t state_free = (pl4g_embed_state_free_t) dlsym(handle, "pl4g_embed_state_free");
  pl4g_embed_run_source_t run = (pl4g_embed_run_source_t) dlsym(handle, "pl4g_embed_run_source");
  if (state_new == NULL || state_free == NULL || run == NULL)
    {
      fprintf(stderr, "%s does not offer what it should: %s\n", library, dlerror());
      return 3;
    }

  pl4g_embed_state *states[2] = { state_new(), state_new() };
  if (states[0] == NULL || states[1] == NULL)
    {
      fputs("no state could be made\n", stderr);
      return 7;
    }
  int checked = 0;
  for (int which = 0; which < 2 && checked == 0; ++which)
    {
      result.delivered = false;
      int status = run(states[which], program, strlen(program), "example.pl4g");
      if (status != 0)
        {
          fprintf(stderr, "the program ended with status %d\n", status);
          checked = 4;
        }
      else
        checked = check();
    }
  state_free(states[0]);
  state_free(states[1]);
  dlclose(handle);
  return checked;
}

/* What was delivered, held to what is due: 0 where it agrees.  */
static int check(void)
{
  /* What the answers have to be, worked out here.  */
  int64_t sum = 0, squares = 0, smallest = readings[0], largest = readings[0];
  for (int64_t at = 0; at < COUNT; ++at)
    {
      sum += readings[at];
      squares += readings[at] * readings[at];
      if (readings[at] < smallest)
        smallest = readings[at];
      if (readings[at] > largest)
        largest = readings[at];
    }
  if (! result.delivered)
    {
      fputs("the program delivered nothing\n", stderr);
      return 5;
    }
  if (result.count != COUNT || result.sum != sum || result.smallest != smallest || result.largest != largest
      || result.squares != squares)
    {
      fprintf(stderr, "delivered %lld, %lld, %lld, %lld, %lld where %lld, %lld, %lld, %lld, %lld were due\n",
              (long long) result.count, (long long) result.sum, (long long) result.smallest,
              (long long) result.largest, (long long) result.squares, (long long) COUNT, (long long) sum,
              (long long) smallest, (long long) largest, (long long) squares);
      return 6;
    }
  printf("delivered: %lld readings, sum %lld, smallest %lld, largest %lld, sum of squares %lld\n",
         (long long) result.count, (long long) result.sum, (long long) result.smallest,
         (long long) result.largest, (long long) result.squares);
  return 0;
}
