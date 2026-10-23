/* The program the shared object cdecl-value-from-the-host.pl4g is built into
   is loaded by.  It hands the object the addresses of functions of its own,
   as numbers, and the object calls them through values of the system's
   convention; and it calls what the object hands back the same way.  It exits
   with 0 where everything arrived as it was sent, and with a number saying
   what did not.  */
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

static int64_t
many (int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f,
      int64_t g, int64_t h, int64_t i, int64_t j, double x, double y,
      double z, double w, double p, double q, double r, double s, double t,
      double u)
{
  return a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g + 8 * h + 9 * i
         + 10 * j + (int64_t) (x + y + z + w + p + q + r + s + 100 * t
                               + 1000 * u);
}

static double
scaled (int32_t by, int64_t *total, double x)
{
  *total += by;
  return x * by;
}

static uint8_t
next (uint8_t c)
{
  return c + 1;
}

/* Found by the object with dlsym, which is what -rdynamic is for.  */
int64_t
bumped (int64_t n)
{
  return n + 100;
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
  if (run ((uint64_t) (uintptr_t) many, (uint64_t) (uintptr_t) scaled,
           (uint64_t) (uintptr_t) next) != 0)
    return 4;
  /* What the object answers is the address of a function of its own, which
     this program calls as it calls any.  */
  int64_t (*twice) (int64_t, double)
    = (int64_t (*) (int64_t, double)) (uintptr_t) offered ();
  if (twice == NULL || twice (20, 1.5) != 43)
    return 5;
  dlclose (h);
  return 0;
}
