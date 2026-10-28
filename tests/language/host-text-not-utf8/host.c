/* Loads the shared object host-text-not-utf8.pl4g is built into and calls
   what it offers, which calls a host function answering bytes that are not
   UTF-8: the program stops there with status 94 and never comes back here.  */
#include <dlfcn.h>
#include <stdint.h>

struct text { const char *text; uint64_t length; };

struct text overlong(void) { return (struct text) { "a\xc0\x80", 3 }; }

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == 0)
    return 2;
  int (*run)(void) = (int (*)(void)) dlsym(h, "run");
  if (run == 0)
    return 3;
  run();
  return 4;
}
