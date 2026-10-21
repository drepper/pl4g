/* The program the shared object host-function-on-the-stack.pl4g is built into
   is loaded by.  It defines the host functions the object calls, each taking
   more arguments than the system's convention passes in registers, and calls
   the one function the object offers with more than that as well.  It exits
   with 0 where everything arrived as it was sent, and with a number saying
   what did not.  */
#include <dlfcn.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <uchar.h>

/* Five of these on the stack on x86-64, three on the other two.  */
int64_t ints(int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f, int64_t g, int64_t h,
             int64_t i, int64_t j, int64_t k)
{
  return a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g + 8 * h + 9 * i + 10 * j + 11 * k;
}

/* Three past the eight floating-point registers: on the stack on x86-64 and
   AArch64, in the ordinary registers on RISC-V.  */
double reals(double a, double b, double c, double d, double e, double f, double g, double h,
             double i, float j, double k)
{
  return a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g + 8 * h + 9 * i + 10 * j + 11 * k;
}

/* Both kinds running out, narrow ones among them.  */
int64_t mixed(uint8_t a, double b, int16_t c, float d, bool e, int64_t f, uint32_t g, int8_t h,
              double i, int64_t j, double k, double l, double m, double n, double o, double p, double q,
              uint8_t r, int32_t s, double t, char32_t u)
{
  return a + (int64_t) b + c + (int64_t) d + e + f + g + h + (int64_t) i + j
         + (int64_t) (k + l + m + n + o + p + q) + r + s + (int64_t) t + (u == U'\x263A' ? 1000 : 0);
}

typedef int64_t offered (int64_t, int64_t, int64_t, int64_t, int64_t, int64_t, int64_t, double, double,
                         double, double, double, double, double, double, double, uint8_t, int16_t, bool);

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == NULL)
    return 2;
  offered *run = (offered *) dlsym(h, "run");
  if (run == NULL)
    return 3;
  /* Twice: the second call finds each function where the first left it.  */
  int64_t got = run(1, 2, 3, 4, 5, 6, 7, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 200, -300, true);
  int64_t again = run(1, 2, 3, 4, 5, 6, 7, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 200, -300, true);
  dlclose(h);
  return got != again ? 4 : got == 0 ? 0 : 10 + (int) (got < 0 || got > 100 ? 100 : got);
}
