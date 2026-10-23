/* The program the shared object abi-record-answered-to-c.pl4g is built into
   is loaded by.  It defines the host functions that object calls, each
   answering a struct by value, and calls the functions the object offers,
   which do the same; it is linked with -rdynamic so that dlsym finds its own.
   It exits with 0 where every member arrived as it was sent, and with a
   number saying which did not.  */
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

struct pair { int64_t whole; double part; };
struct floats { float x, y, z; };
struct quad { int64_t a; double b; int32_t c; double d; };

struct pair host_pair(int64_t n) { return (struct pair) { n + 1, 0.75 }; }

struct floats host_floats(float x) { return (struct floats) { x, 2 * x, 3 * x }; }

struct quad host_quad(int64_t n, double x)
{
  return (struct quad) { n + 1, 2 * x, (int32_t) n + 2, 3 * x };
}

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == NULL)
    return 2;
  struct pair (*make_pair)(int64_t, double) = (struct pair (*)(int64_t, double)) dlsym(h, "make_pair");
  struct floats (*make_floats)(float) = (struct floats (*)(float)) dlsym(h, "make_floats");
  struct quad (*make_quad)(int64_t, double) = (struct quad (*)(int64_t, double)) dlsym(h, "make_quad");
  int64_t (*ask_host)(void) = (int64_t (*)(void)) dlsym(h, "ask_host");
  if (make_pair == NULL || make_floats == NULL || make_quad == NULL || ask_host == NULL)
    return 3;
  struct pair p = make_pair(7, 0.5);
  if (p.whole != 7 || p.part != 0.5)
    return 4;
  struct floats f = make_floats(1.0f);
  if (f.x != 1.0f || f.y != 2.0f || f.z != 3.0f)
    return 5;
  /* What the object answers through the room made here is answered back
     where that room is, which is what a C caller may count on.  */
  struct quad q = make_quad(9, 1.25);
  if (q.a != 9 || q.b != 1.25 || q.c != 77 || q.d != 1.75)
    return 6;
  int64_t asked = ask_host();
  if (asked != 0)
    return 10 + (int) asked;
  /* Twice: the second time each host function is found where the first
     call left it.  */
  if (ask_host() != 0)
    return 20;
  dlclose(h);
  return 0;
}
