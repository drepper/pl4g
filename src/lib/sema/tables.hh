// The hash table a set and a dictionary are, written in the representation.
//
// A port of pypl4g's sema/tables.py.  A collection is a table: a block of
// memory in an arena holding how much room it has and where its entries are,
// open-addressed with linear probing, its keys hashed with a seed of its own.
// What a table needs is generated as functions of the IR, once per module and,
// where the key or the value decides, once per shape; and beside it the
// allocators every other kind of room is taken from.
#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <utility>

#include "ir/builder.hh"
#include "ir/ir.hh"


namespace pl4g::sema::tables {

  // What the allocator is called, and what gives an arena back.
  inline constexpr std::string_view alloc_symbol = "__pl4g_alloc";
  inline constexpr std::string_view release_symbol = "__pl4g_release";

  // What `⎕heap` is: the packaged runtime's allocator of one object at a time.
  inline constexpr std::string_view heap_alloc_symbol = "pl4g_heap_alloc";
  inline constexpr std::string_view heap_free_symbol = "pl4g_heap_free";
  // The compiler's own around them.
  inline constexpr std::string_view heap_new_symbol = "__pl4g_heap_new";
  inline constexpr std::string_view allocate_symbol = "__pl4g_allocate";
  inline constexpr std::string_view dispose_symbol = "__pl4g_dispose";

  // The variable whose address names `⎕heap`, and what the module files it under.
  inline constexpr std::string_view heap_symbol = "__pl4g_heap";
  inline constexpr std::string_view heap_key = "\N{APL FUNCTIONAL SYMBOL QUAD}heap";

  // How wide a word is: every field of a table is one.
  inline constexpr std::int64_t word = 8;

  // Where the fields of a table block are.
  inline constexpr std::int64_t arena_field = 0;
  inline constexpr std::int64_t mask_field = word;
  inline constexpr std::int64_t count_field = 2 * word;
  inline constexpr std::int64_t stride_field = 3 * word;
  inline constexpr std::int64_t entries_field = 4 * word;
  inline constexpr std::int64_t used_field = 5 * word;
  inline constexpr std::int64_t seed_field = 6 * word;
  // How much room a block takes.
  inline constexpr std::int64_t block_size = 7 * word;

  // The fields of one entry that are where they are whatever it holds.
  inline constexpr std::int64_t state_at = 0;
  inline constexpr std::int64_t key_at = word;

  // What the first word of an entry says about it.
  inline constexpr std::int64_t empty = 0;
  inline constexpr std::int64_t live = 1;
  inline constexpr std::int64_t gone = 2;

  // How many entries a table has to begin with, and how full it gets.
  inline constexpr std::int64_t first_capacity = 8;
  inline constexpr std::int64_t load_numerator = 3;
  inline constexpr std::int64_t load_denominator = 4;

  // The seed of a table laid out in the image with its keys in it.
  inline constexpr std::uint64_t image_seed = 0x6A09E667F3BCC908;

  // What the generated functions are called.
  inline constexpr std::string_view new_symbol = "__pl4g_table_new";
  inline constexpr std::string_view slot_symbol = "__pl4g_table_slot";
  inline constexpr std::string_view put_symbol = "__pl4g_table_put";
  inline constexpr std::string_view arena_of_symbol = "__pl4g_table_arena";
  inline constexpr std::string_view select_symbol = "__pl4g_table_select";
  inline constexpr std::string_view give_up_symbol = "__pl4g_table_give_up";
  inline constexpr std::string_view next_symbol = "__pl4g_table_next";

  // What the runtime hashes a key with, and gives a table its seed with.
  inline constexpr std::string_view hash_symbol = "pl4g_hash";
  inline constexpr std::string_view hash_word_symbol = "pl4g_hash_word";
  inline constexpr std::string_view hash_seed_symbol = "pl4g_hash_seed";

  // What `__pl4g_table_select` takes from the table it walks.
  inline constexpr std::int64_t want_absent = 0;
  inline constexpr std::int64_t want_present = 1;
  inline constexpr std::int64_t want_either = 2;


  // What one table holds, which is what its entries are shaped by: a key, and
  // a value unless it is a set's.
  struct Shape {
    const ir::Type* key = nullptr;
    const ir::Type* value = nullptr;

    // How many words the key takes, which is at least one.
    std::int64_t key_words() const;
    // Where the value of an entry begins.
    std::int64_t value_at() const { return key_at + key_words() * word; }
    // How long one entry is.
    std::int64_t stride() const;
    // What the functions generated for this shape are called after.
    std::string tag() const;

    // Equal as pypl4g's frozen dataclass is: its types by `==`.
    friend bool operator==(const Shape& a, const Shape& b)
    {
      return ir::same(a.key, b.key)
             && (a.value == nullptr ? b.value == nullptr : b.value != nullptr && ir::same(a.value, b.value));
    }
  };

  // What a key of this shape is passed and stored as: a word where it fits one.
  const ir::Type* key_ir_type(const Shape& shape);

  // The variable whose address names `⎕heap`, made once per module.
  ir::GlobalVar* heap_global(ir::Module& module);
  // Whether `value` is, while compiling, the address `⎕heap` is named by.
  bool is_heap(const ir::Value* value);

  // `__pl4g_heap_new(size)`: room from the heap, or the program stopped.
  ir::Function* heap_new_function(ir::Module& module);
  // The runtime's `pl4g_heap_free(where, size)`.
  ir::Function* heap_free_function(ir::Module& module);
  // `__pl4g_allocate(allocator, size)`: room from whichever allocator is named.
  ir::Function* allocator_function(ir::Module& module);
  // `__pl4g_dispose(allocator, where, size)`: one object given back.
  ir::Function* dispose_function(ir::Module& module);
  // The allocator of something in the image, which nothing gives back.
  ir::Value* no_allocator(ir::Builder& builder);
  // Room for `size` bytes from `arena`, the heap called straight away where
  // the compiler knows it is the heap.
  ir::Value* allocate(ir::Builder& builder, ir::Value* arena, ir::Value* size);
  // The one that gives a whole arena back, declared on first ask.
  ir::Function* release_function(ir::Module& module);

  // The runtime's hash of bytes, hash of a word, and seed.
  ir::Function* hash_function(ir::Module& module);
  ir::Function* hash_word_function(ir::Module& module);
  ir::Function* seed_function(ir::Module& module);

  // A function of this module's own, and whether it has yet to be built.
  std::pair<ir::Function*, bool> generated(ir::Module& module, std::string_view name,
                                           std::span<const ir::Type* const> params, const ir::Type* result);

  // Which arena a table came out of; `⎕heap` for one in the image.
  ir::Value* arena_of(ir::Builder& builder, ir::Value* table);
  // How many entries a table holds.
  ir::Value* count_of(ir::Builder& builder, ir::Value* table);
  // What a table is reached by: a pointer to its first word.
  const ir::PtrType* table_type(ir::Module& module);

  // Make the allocator, for something that wants room and not a table.
  void ensure_allocator(ir::Module& module);
  // Build what the four operators a set answers are made out of.
  ir::Function* ensure_operators(ir::Module& module, const Shape& shape);
  // Build what a collection of this shape needs.
  void ensure_runtime(ir::Module& module, const Shape& shape);
  // Build what taking a key out of a table is made of.
  ir::Function* ensure_take(ir::Module& module, const Shape& shape);
  // What the function of `shape` is called.
  std::string named(std::string_view symbol, const Shape& shape);

  // Where the entry at one place in a table's array of entries is.
  ir::Value* entry_at(ir::Builder& builder, ir::Value* table, ir::Value* at);

} // namespace pl4g::sema::tables
