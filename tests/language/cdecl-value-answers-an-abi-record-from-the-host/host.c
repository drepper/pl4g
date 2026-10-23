/* The program the shared object cdecl-value-answers-an-abi-record-from-the-host.pl4g
   is built into is loaded by.  It hands the object the addresses of functions
   of its own answering a struct by value, as numbers, and the object calls
   them through values of the system's convention; and it calls what the
   object hands back the same way.  It exits with 0 where every member arrived
   as it was sent, and with a number saying what did not.  */
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

struct pair { int64_t whole; double part; };
struct floats { float x, y, z; };
struct quad { int64_t a; double b; int32_t c; double d; };

static struct pair
pair (int64_t n)
{
  return (struct pair) { n + 1, 0.75 };
}

static struct floats
floats (float x)
{
  return (struct floats) { x, 2 * x, 3 * x };
}

static struct quad
quad (int64_t n, double x)
{
  return (struct quad) { n + 1, 2 * x, (int32_t) n + 2, 3 * x };
}

int
main (int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen (argv[1], RTLD_NOW);
  if (h == NULL)
    return 2;
  int64_t (*run) (uint64_t, uint64_t, uint64_t)
    = (int64_t (*) (uint64_t, uint64_t, uint64_t)) dlsym (h, "run");
  uint64_t (*offered) (void) = (uint64_t (*) (void)) dlsym (h, "offered");
  if (run == NULL || offered == NULL)
    return 3;
  int64_t got = run ((uint64_t) (uintptr_t) pair, (uint64_t) (uintptr_t) floats,
                     (uint64_t) (uintptr_t) quad);
  if (got != 0)
    return 10 + (int) got;
  /* What the object answers is the address of a function of its own
     answering a struct, which this program calls as it calls any.  */
  struct quad (*made) (int64_t, double)
    = (struct quad (*) (int64_t, double)) (uintptr_t) offered ();
  if (made == NULL)
    return 5;
  struct quad q = made (20, 1.5);
  if (q.a != 21 || q.b != 3.0 || q.c != 7 || q.d != 4.5)
    return 6;
  dlclose (h);
  return 0;
}
