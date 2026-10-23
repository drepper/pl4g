/* The program the shared object auxv-in-a-shared-object.pl4g is built into is
   loaded by.  It exits with 0 where the object's table of the auxiliary vector
   said nothing, as a shared object's does, and with a number saying what went
   wrong where it did not.  */
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == NULL)
    return 2;
  uint64_t (*asked)(void) = (uint64_t (*)(void)) dlsym(h, "asked");
  if (asked == NULL)
    return 3;
  uint64_t got = asked();
  dlclose(h);
  return got == 0 ? 0 : 4;
}
