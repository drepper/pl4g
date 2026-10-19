// The runtime a compiled program carries, and the allocator its back end emits.
//
// The first is runtime/io.c, the same file the packaged runtime is compiled
// from, linked into the library here and called directly: what an interpreted
// program does with its devices is what a compiled one does, through the same
// ring.  The second is written as machine code by the back ends
// (pypl4g/target/allocator.py), and here as C++ that does exactly what that
// code does to the three words of an arena.
#pragma once

#include <cstdint>


// What runtime/io.c defines, as C.  A ring and a run are records whose layout
// the language declares beside the C one; here they are only addresses.
extern "C" {
  void* pl4g_heap_alloc(std::uint64_t size);
  void pl4g_heap_free(void* at, std::uint64_t size);
  std::uint64_t pl4g_hash(const unsigned char* at, std::uint64_t len, std::uint64_t seed);
  std::uint64_t pl4g_hash_word(std::uint64_t word, std::uint64_t seed);
  std::uint64_t pl4g_hash_seed(void);
  void pl4g_args(const std::uint64_t* stack, void* out);
  struct pl4g_run {
    void* at;
    std::uint64_t len;
  };
  pl4g_run pl4g_env(const std::uint64_t* stack);
  std::int64_t pl4g_io_submit(void* ring, std::int32_t which, std::int32_t fd, std::uint64_t at, std::uint64_t len,
                              std::uint64_t keep);
  std::int64_t pl4g_io_read_kept(void* ring, std::int32_t fd, std::uint64_t len);
  std::uint64_t pl4g_io_taken(void* ring, std::int64_t slot);
  std::int64_t pl4g_io_wait(void* ring, std::int64_t slot);
  std::int64_t pl4g_io_ready(void* ring, std::int64_t slot);
  void pl4g_io_step(void* ring);
  std::int64_t pl4g_io_open(std::int32_t how, std::int32_t dir, std::uint64_t at, std::uint64_t len);
  std::int64_t pl4g_io_close(void* ring, std::int32_t fd);
  void pl4g_io_drain(void* ring);
}


namespace pl4g::rt {

  // The three words of an arena: the first byte not yet handed out, one past
  // the last byte of the chunk it is in, and the head of the list of chunks.
  struct Arena {
    std::uint64_t next;
    std::uint64_t limit;
    std::uint64_t chunk;
  };

  // `size` bytes out of `arena`, on the sixteen-byte grain; nothing where the
  // system would give no more.
  void* arena_alloc(Arena* arena, std::uint64_t size) noexcept;

  // Give back every chunk of `arena`, leaving it an arena with nothing in it.
  void arena_release(Arena* arena) noexcept;

} // namespace pl4g::rt
