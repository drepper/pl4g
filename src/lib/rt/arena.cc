#include "rt/runtime.hh"

#include <sys/mman.h>


namespace pl4g::rt {

  namespace {

    // Every allocation starts on this many bytes, the strictest alignment any
    // value of the language wants.
    constexpr std::uint64_t grain = 16;
    // The smallest a chunk is asked for.
    constexpr std::uint64_t chunk_minimum = 64 * 1024;
    // The two words at the front of a chunk: the one before it in the list,
    // and how many bytes were mapped.
    constexpr std::uint64_t chunk_body = 16;

    struct Chunk {
      std::uint64_t next;
      std::uint64_t bytes;
    };

  } // anonymous namespace


  void* arena_alloc(Arena* arena, std::uint64_t size) noexcept
  {
    size = (size + grain - 1) & ~(grain - 1);
    std::uint64_t place = arena->next;
    std::uint64_t room = place + size;
    if (room <= arena->limit) {
      arena->next = room;
      return reinterpret_cast<void*>(place);
    }
    // Another chunk, large enough for this and the chunk's own header, and no
    // smaller than the smallest worth asking for.
    std::uint64_t asked = size + chunk_body;
    if (asked < chunk_minimum)
      asked = chunk_minimum;
    void* mapped = ::mmap(nullptr, asked, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapped == MAP_FAILED) [[unlikely]]
      return nullptr;
    auto base = reinterpret_cast<std::uint64_t>(mapped);
    auto chunk = static_cast<Chunk*>(mapped);
    chunk->next = arena->chunk;
    chunk->bytes = asked;
    arena->chunk = base;
    arena->limit = base + asked;
    base += chunk_body;
    arena->next = base + size;
    return reinterpret_cast<void*>(base);
  }


  void arena_release(Arena* arena) noexcept
  {
    // Emptied first: what says how much to unmap is in the chunk unmapped.
    std::uint64_t chunk = arena->chunk;
    arena->next = 0;
    arena->limit = 0;
    arena->chunk = 0;
    while (chunk != 0) {
      auto one = reinterpret_cast<Chunk*>(chunk);
      std::uint64_t after = one->next;
      ::munmap(one, one->bytes);
      chunk = after;
    }
  }

} // namespace pl4g::rt
